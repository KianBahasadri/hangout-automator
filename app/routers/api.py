from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi import Query
from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.database import get_db
from app.ids import RowIdPath
from app.models import (
    Allergy,
    Hangout,
    HangoutInvite,
    HangoutStatus,
    Profile,
    Tag,
    Workspace,
    not_null_columns,
)
from app.messages import craft_invite_preview
from app.schemas import (
    AllergyCreate,
    AllergyOut,
    HangoutCreate,
    HangoutOut,
    HangoutUpdate,
    InviteSmsPreviewIn,
    InviteSmsPreviewOut,
    PlaceDetailsOut,
    PlacesAutocompleteOut,
    ProfileCreate,
    ProfileOut,
    ProfileUpdate,
    SetupHangoutRequest,
    TagCreate,
    TagOut,
)
from app.services import (
    COOLDOWN_MINUTE_OPTIONS,
    CONFIRM_GOAL_OPTIONS,
    INTERVAL_HOUR_OPTIONS,
    clamp_choice,
    load_allergies_by_ids,
    load_hangout,
    load_tags_by_ids,
    normalize_allergy_name,
    normalize_tag_name,
    resolve_organizer_phone,
    setup_hangout,
)
from app.sms import is_valid_phone, normalize_phone
from app.tenancy import current_workspace, get_scoped, scoped

router = APIRouter(prefix="/api", tags=["api"])
logger = logging.getLogger(__name__)

_PLACES_BASE_URL = "https://places.googleapis.com/v1"
_AUTOCOMPLETE_FIELD_MASK = (
    "suggestions.placePrediction.placeId,"
    "suggestions.placePrediction.text.text,"
    "suggestions.placePrediction.structuredFormat.mainText.text,"
    "suggestions.placePrediction.structuredFormat.secondaryText.text"
)
_DETAILS_FIELD_MASK = "id,formattedAddress,location.latitude,location.longitude"
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,36}$")
_SAFE_PLACE_ID = re.compile(r"^[^/\x00-\x1f\x7f]+$")
_PLACE_ID_MAX_LENGTH = 4096


class _PlacesUpstreamError(Exception):
    """The upstream Places API did not return a usable response."""

    def __init__(self, status_code: int | None = None):
        super().__init__()
        self.status_code = status_code


def _places_api_key() -> str:
    return (get_settings().google_maps_api_key or "").strip()


def _validate_session_token(session_token: str) -> str:
    token = (session_token or "").strip()
    if token and not _SAFE_TOKEN.fullmatch(token):
        raise HTTPException(422, "Invalid Places session token")
    return token


def _is_safe_place_id(place_id: str) -> bool:
    """Accept Google's opaque IDs with a generous request-size safety bound."""
    return len(place_id) <= _PLACE_ID_MAX_LENGTH and bool(_SAFE_PLACE_ID.fullmatch(place_id))


async def _google_places_request(
    method: str,
    url: str,
    *,
    api_key: str,
    field_mask: str,
    json_body: dict | None = None,
) -> dict:
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": field_mask,
    }
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.request(method, url, headers=headers, json=json_body)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        status_code = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        logger.warning(
            "Google Places request failed: %s %s status=%s",
            method,
            type(exc).__name__,
            status_code or "unknown",
        )
        raise _PlacesUpstreamError(status_code) from exc
    except ValueError as exc:
        logger.warning("Google Places request returned invalid JSON: %s", method)
        raise _PlacesUpstreamError from exc
    if not isinstance(payload, dict):
        logger.warning("Google Places returned a non-object response")
        raise _PlacesUpstreamError
    return payload


@router.get("/places/autocomplete", response_model=PlacesAutocompleteOut)
async def places_autocomplete(
    input: str = Query(default="", max_length=255),
    session_token: str = Query(default="", max_length=36),
) -> dict[str, list[dict[str, str | None]]]:
    """Return a small, safe-to-render subset of Google place predictions."""
    api_key = _places_api_key()
    if not api_key:
        raise HTTPException(404, "Google Places is not configured")

    query = input.strip()
    if len(query) < 3:
        return {"suggestions": []}
    token = _validate_session_token(session_token)
    body: dict[str, str] = {"input": query}
    if token:
        body["sessionToken"] = token

    try:
        payload = await _google_places_request(
            "POST",
            f"{_PLACES_BASE_URL}/places:autocomplete",
            api_key=api_key,
            field_mask=_AUTOCOMPLETE_FIELD_MASK,
            json_body=body,
        )
    except _PlacesUpstreamError as exc:
        if exc.status_code in {401, 403}:
            raise HTTPException(
                503,
                "Google Places is not enabled for the configured API key",
            ) from exc
        raise HTTPException(502, "Google Places is temporarily unavailable") from exc

    suggestions: list[dict[str, str | None]] = []
    raw_suggestions = payload.get("suggestions", [])
    if not isinstance(raw_suggestions, list):
        raw_suggestions = []
    for item in raw_suggestions:
        if not isinstance(item, dict):
            continue
        prediction = item.get("placePrediction")
        if not isinstance(prediction, dict):
            continue
        place_id = prediction.get("placeId")
        text = prediction.get("text")
        structured = prediction.get("structuredFormat")
        if not isinstance(place_id, str):
            continue
        place_id = place_id.strip()
        if not _is_safe_place_id(place_id):
            continue
        if not isinstance(text, dict) or not isinstance(text.get("text"), str):
            continue
        main_text = ""
        secondary_text: str | None = None
        if isinstance(structured, dict):
            main = structured.get("mainText")
            secondary = structured.get("secondaryText")
            if isinstance(main, dict) and isinstance(main.get("text"), str):
                main_text = main["text"]
            if isinstance(secondary, dict) and isinstance(secondary.get("text"), str):
                secondary_text = secondary["text"]
        suggestions.append(
            {
                "place_id": place_id,
                "text": text["text"],
                "main_text": main_text or text["text"],
                "secondary_text": secondary_text,
            }
        )
    return {"suggestions": suggestions}


@router.get("/places/details", response_model=PlaceDetailsOut)
async def places_details(
    place_id: str = Query(..., min_length=1, max_length=_PLACE_ID_MAX_LENGTH),
    session_token: str = Query(default="", max_length=36),
) -> dict[str, str | float | None]:
    """Resolve the selected prediction to the address used by the form."""
    api_key = _places_api_key()
    if not api_key:
        raise HTTPException(404, "Google Places is not configured")

    place_id = place_id.strip()
    if not _is_safe_place_id(place_id):
        raise HTTPException(422, "Invalid Places place ID")
    token = _validate_session_token(session_token)
    url = f"{_PLACES_BASE_URL}/places/{quote(place_id, safe='')}"
    if token:
        # Place Details uses the session token as a query parameter to end the
        # Autocomplete session for billing purposes.
        url += f"?sessionToken={quote(token, safe='')}"

    try:
        payload = await _google_places_request(
            "GET",
            url,
            api_key=api_key,
            field_mask=_DETAILS_FIELD_MASK,
        )
    except _PlacesUpstreamError as exc:
        if exc.status_code in {401, 403}:
            raise HTTPException(
                503,
                "Google Places is not enabled for the configured API key",
            ) from exc
        raise HTTPException(502, "Google Places is temporarily unavailable") from exc

    formatted_address = payload.get("formattedAddress")
    if not isinstance(formatted_address, str) or not formatted_address.strip():
        raise HTTPException(502, "Google Places returned no address")
    location = payload.get("location")
    if not isinstance(location, dict):
        location = {}
    latitude = location.get("latitude")
    longitude = location.get("longitude")
    return {
        "place_id": payload.get("id") if isinstance(payload.get("id"), str) else place_id,
        "formatted_address": formatted_address.strip(),
        "latitude": latitude if isinstance(latitude, (int, float)) else None,
        "longitude": longitude if isinstance(longitude, (int, float)) else None,
    }


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


def _worker_last_tick_age() -> dict:
    """Age of the last background_job.completed audit event, read off the tail
    of the JSONL audit stream (the worker ticks every 5 minutes, so the latest
    completed event is always near the end of the file)."""
    from datetime import datetime, timezone

    path = Path(get_settings().log_file).expanduser()
    latest: float | None = None
    if path.is_file() and path.stat().st_size:
        # Only the tail: the file rotates at 50 MB and the last tick is recent.
        with path.open(encoding="utf-8", errors="replace") as fh:
            if path.stat().st_size > 262_144:
                fh.seek(path.stat().st_size - 262_144)
                fh.readline()  # drop the partial first line
            for line in fh:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if event.get("event") == "background_job.completed":
                    timestamp = event.get("timestamp")
                    if timestamp:
                        try:
                            latest = datetime.fromisoformat(timestamp).timestamp()
                        except ValueError:
                            continue
    if latest is None:
        return {"last_tick": None, "age_seconds": None, "status": "no_tick_observed"}
    age = max(0.0, datetime.now(timezone.utc).timestamp() - latest)
    return {
        "last_tick": datetime.fromtimestamp(latest, timezone.utc).isoformat(),
        "age_seconds": round(age, 1),
        "status": "ok" if age < 3600 else "stale",
    }


@router.get("/health/deep")
def health_deep(db: Session = Depends(get_db)) -> dict:
    """Authenticated deep health: database reachability, pending Alembic
    revision vs head, and worker last-tick age. The shallow /api/health stays
    public for the Cloudflare probe; this one is protected by the auth
    middleware when Clerk is enabled."""
    checks: dict = {}

    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001 — report any failure shape
        checks["database"] = f"error: {type(exc).__name__}"

    try:
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory

        script = ScriptDirectory.from_config(
            Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
        )
        heads = script.get_heads()
        current = MigrationContext.configure(db.connection()).get_current_revision()
        checks["migrations"] = {
            "current": current,
            "head": heads[0] if heads else None,
            "up_to_date": bool(heads) and current == heads[0],
        }
    except Exception as exc:  # noqa: BLE001
        checks["migrations"] = f"error: {type(exc).__name__}"

    checks["worker"] = _worker_last_tick_age()
    return checks


@router.post("/sms/preview-invite", response_model=InviteSmsPreviewOut)
def preview_invite_sms(payload: InviteSmsPreviewIn) -> InviteSmsPreviewOut:
    """Craft an invite SMS body from hangout fields (preview only; does not send)."""
    return InviteSmsPreviewOut(
        body=craft_invite_preview(
            recipient_name=payload.recipient_name,
            day_date=payload.day_date,
            time=payload.time,
            duration=payload.duration,
            location=payload.location,
            motive=payload.motive,
            alcohol_involved=payload.alcohol_involved,
            weed_involved=payload.weed_involved,
            notes=payload.notes,
        )
    )


# --- Tags ---


@router.get("/tags", response_model=list[TagOut])
def list_tags(
    db: Session = Depends(get_db), workspace: Workspace = Depends(current_workspace)
) -> list[Tag]:
    return scoped(db, Tag, workspace).order_by(Tag.name).all()


@router.post("/tags", response_model=TagOut, status_code=201)
def create_tag(
    payload: TagCreate,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> Tag:
    name = normalize_tag_name(payload.name)
    if not name:
        raise HTTPException(400, "Tag name is required")
    if scoped(db, Tag, workspace).filter(Tag.name.ilike(name)).first():
        raise HTTPException(400, "A tag with this name already exists")
    tag = Tag(name=name, workspace_id=workspace.id)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


@router.delete("/tags/{tag_id}", status_code=204, response_class=Response)
def delete_tag(
    tag_id: RowIdPath,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> Response:
    tag = get_scoped(db, Tag, tag_id, workspace)
    if not tag:
        raise HTTPException(404, "Tag not found")
    db.delete(tag)
    db.commit()
    return Response(status_code=204)


# --- Allergies ---


@router.get("/allergies", response_model=list[AllergyOut])
def list_allergies(
    db: Session = Depends(get_db), workspace: Workspace = Depends(current_workspace)
) -> list[Allergy]:
    return scoped(db, Allergy, workspace).order_by(Allergy.name).all()


@router.post("/allergies", response_model=AllergyOut, status_code=201)
def create_allergy(
    payload: AllergyCreate,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> Allergy:
    name = normalize_allergy_name(payload.name)
    if not name:
        raise HTTPException(400, "Allergy name is required")
    if scoped(db, Allergy, workspace).filter(Allergy.name.ilike(name)).first():
        raise HTTPException(400, "An allergy with this name already exists")
    allergy = Allergy(name=name, workspace_id=workspace.id)
    db.add(allergy)
    db.commit()
    db.refresh(allergy)
    return allergy


@router.delete("/allergies/{allergy_id}", status_code=204, response_class=Response)
def delete_allergy(
    allergy_id: RowIdPath,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> Response:
    allergy = get_scoped(db, Allergy, allergy_id, workspace)
    if not allergy:
        raise HTTPException(404, "Allergy not found")
    db.delete(allergy)
    db.commit()
    return Response(status_code=204)


# --- Contacts (invitee directory; table/ORM still Profile) ---
# /api/profiles* remains as a compatibility alias during the rename.


def _contact_query(db: Session, workspace: Workspace):
    return scoped(db, Profile, workspace).options(
        joinedload(Profile.tags),
        joinedload(Profile.allergies),
    )


@router.get("/contacts", response_model=list[ProfileOut])
@router.get("/profiles", response_model=list[ProfileOut], include_in_schema=False)
def list_contacts(
    db: Session = Depends(get_db), workspace: Workspace = Depends(current_workspace)
) -> list[Profile]:
    return _contact_query(db, workspace).order_by(Profile.name).all()


@router.post("/contacts", response_model=ProfileOut, status_code=201)
@router.post("/profiles", response_model=ProfileOut, status_code=201, include_in_schema=False)
def create_contact(
    payload: ProfileCreate,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> Profile:
    phone = normalize_phone(payload.phone)
    if not is_valid_phone(phone):
        raise HTTPException(400, "Phone number is not usable")
    if scoped(db, Profile, workspace).filter(Profile.phone == phone).first():
        raise HTTPException(400, "A contact with this phone already exists")
    profile = Profile(
        name=payload.name.strip(),
        phone=phone,
        drinks=payload.drinks,
        smokes=payload.smokes,
        drive=payload.drive,
        workspace_id=workspace.id,
    )
    profile.tags = load_tags_by_ids(db, payload.tag_ids, workspace)
    profile.allergies = load_allergies_by_ids(db, payload.allergy_ids, workspace)
    db.add(profile)
    db.commit()
    return _contact_query(db, workspace).filter(Profile.id == profile.id).one()


@router.patch("/contacts/{contact_id}", response_model=ProfileOut)
@router.patch("/profiles/{contact_id}", response_model=ProfileOut, include_in_schema=False)
def update_contact(
    contact_id: RowIdPath,
    payload: ProfileUpdate,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> Profile:
    profile = get_scoped(db, Profile, contact_id, workspace)
    if not profile:
        raise HTTPException(404, "Contact not found")
    data = payload.model_dump(exclude_unset=True)
    tag_ids = data.pop("tag_ids", None)
    allergy_ids = data.pop("allergy_ids", None)
    if "phone" in data:
        if not data["phone"] or not str(data["phone"]).strip():
            raise HTTPException(400, "Phone is required")
        data["phone"] = normalize_phone(data["phone"])
        if not is_valid_phone(data["phone"]):
            raise HTTPException(400, "Phone number is not usable")
        clash = (
            scoped(db, Profile, workspace)
            .filter(Profile.phone == data["phone"], Profile.id != contact_id)
            .first()
        )
        if clash:
            raise HTTPException(400, "A contact with this phone already exists")
    if "name" in data:
        if not data["name"] or not str(data["name"]).strip():
            raise HTTPException(400, "Name is required")
        data["name"] = data["name"].strip()
    for k, v in data.items():
        setattr(profile, k, v)
    if tag_ids is not None:
        profile.tags = load_tags_by_ids(db, tag_ids, workspace)
    if allergy_ids is not None:
        profile.allergies = load_allergies_by_ids(db, allergy_ids, workspace)
    db.commit()
    return _contact_query(db, workspace).filter(Profile.id == contact_id).one()


@router.delete("/contacts/{contact_id}", status_code=204, response_class=Response)
@router.delete(
    "/profiles/{contact_id}", status_code=204, response_class=Response, include_in_schema=False
)
def delete_contact(
    contact_id: RowIdPath,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> Response:
    profile = get_scoped(db, Profile, contact_id, workspace)
    if not profile:
        raise HTTPException(404, "Contact not found")
    db.delete(profile)
    db.commit()
    return Response(status_code=204)


# --- Hangouts ---


@router.get("/hangouts", response_model=list[HangoutOut])
def list_hangouts(
    db: Session = Depends(get_db), workspace: Workspace = Depends(current_workspace)
) -> list[Hangout]:
    return (
        scoped(db, Hangout, workspace)
        .options(joinedload(Hangout.invites).joinedload(HangoutInvite.profile))
        .filter(Hangout.deleted_at.is_(None))
        .order_by(Hangout.id.desc())
        .all()
    )


@router.post("/hangouts", response_model=HangoutOut, status_code=201)
def create_hangout(
    request: Request,
    payload: HangoutCreate,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> Hangout:
    from app.users import user_for_request

    org_profile = None
    if payload.organizer_profile_id is not None:
        org_profile = get_scoped(db, Profile, payload.organizer_profile_id, workspace)
        if org_profile is None:
            raise HTTPException(400, "Organizer contact not found")
    org_phone = org_profile.phone if org_profile else user_for_request(db, request).phone
    if payload.notify_enabled and not org_phone:
        raise HTTPException(
            400,
            "Organizer contact or My Profile phone required when notifications are enabled",
        )
    hangout = Hangout(
        day_date=payload.day_date or None,
        time=payload.time or None,
        duration=payload.duration or None,
        location=(payload.location.strip() if payload.location else None) or None,
        motive=payload.motive or None,
        alcohol_involved=payload.alcohol_involved,
        weed_involved=payload.weed_involved,
        notes=payload.notes or None,
        status=HangoutStatus.draft,
        workspace_id=workspace.id,
        organizer_profile_id=org_profile.id if org_profile else None,
        organizer_phone=org_phone,
        notify_enabled=payload.notify_enabled,
        notify_interval=payload.notify_enabled and payload.notify_interval,
        notify_threshold=payload.notify_enabled and payload.notify_threshold,
        notify_interval_hours=clamp_choice(payload.notify_interval_hours, INTERVAL_HOUR_OPTIONS, 6),
        notify_interval_only_if_changed=payload.notify_interval_only_if_changed,
        notify_on_new_confirm=payload.notify_on_new_confirm,
        notify_on_decline=payload.notify_on_decline,
        notify_on_allergy=payload.notify_on_allergy,
        notify_on_ride_needed=payload.notify_on_ride_needed,
        notify_confirm_goal=clamp_choice(payload.notify_confirm_goal, CONFIRM_GOAL_OPTIONS, 0),
        notify_threshold_cooldown_minutes=clamp_choice(
            payload.notify_threshold_cooldown_minutes, COOLDOWN_MINUTE_OPTIONS, 0
        ),
    )
    db.add(hangout)
    db.flush()
    for pid in payload.profile_ids:
        if get_scoped(db, Profile, pid, workspace) is not None:
            db.add(HangoutInvite(hangout_id=hangout.id, profile_id=pid, workspace_id=workspace.id))
    db.commit()
    return load_hangout(db, hangout.id, workspace)  # type: ignore[return-value]


@router.get("/hangouts/{hangout_id}", response_model=HangoutOut)
def get_hangout(
    hangout_id: RowIdPath,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> Hangout:
    hangout = load_hangout(db, hangout_id, workspace)
    if not hangout:
        raise HTTPException(404, "Hangout not found")
    return hangout


@router.patch("/hangouts/{hangout_id}", response_model=HangoutOut)
def update_hangout(
    hangout_id: RowIdPath,
    payload: HangoutUpdate,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> Hangout:
    hangout = get_scoped(db, Hangout, hangout_id, workspace)
    if not hangout:
        raise HTTPException(404, "Hangout not found")
    data = payload.model_dump(exclude_unset=True)
    # Every notify_* setting is optional in the payload but NOT NULL in the
    # table, so an explicit null is a bad request rather than a failed INSERT.
    blanked = not_null_columns(Hangout, [key for key, value in data.items() if value is None])
    if blanked:
        raise HTTPException(400, f"Cannot be null: {', '.join(sorted(blanked))}")
    if "organizer_profile_id" in data:
        org_id = data.pop("organizer_profile_id")
        if org_id is None:
            hangout.organizer_profile_id = None
            hangout.organizer_phone = None
        else:
            org_profile = get_scoped(db, Profile, org_id, workspace)
            if org_profile is None:
                raise HTTPException(400, "Organizer contact not found")
            hangout.organizer_profile_id = org_profile.id
            hangout.organizer_phone = org_profile.phone
    if "notify_interval_hours" in data and data["notify_interval_hours"] is not None:
        data["notify_interval_hours"] = clamp_choice(
            data["notify_interval_hours"], INTERVAL_HOUR_OPTIONS, hangout.notify_interval_hours or 6
        )
    if "notify_confirm_goal" in data and data["notify_confirm_goal"] is not None:
        data["notify_confirm_goal"] = clamp_choice(
            data["notify_confirm_goal"], CONFIRM_GOAL_OPTIONS, hangout.notify_confirm_goal or 0
        )
    if (
        "notify_threshold_cooldown_minutes" in data
        and data["notify_threshold_cooldown_minutes"] is not None
    ):
        data["notify_threshold_cooldown_minutes"] = clamp_choice(
            data["notify_threshold_cooldown_minutes"],
            COOLDOWN_MINUTE_OPTIONS,
            hangout.notify_threshold_cooldown_minutes or 0,
        )
    for k, v in data.items():
        setattr(hangout, k, v)
    if hangout.notify_enabled:
        if not resolve_organizer_phone(db, hangout):
            raise HTTPException(
                400,
                "Organizer contact or hangout organizer phone required when notifications are enabled",
            )
    db.commit()
    return load_hangout(db, hangout_id, workspace)  # type: ignore[return-value]


@router.post("/hangouts/{hangout_id}/setup", response_model=HangoutOut)
def setup_hangout_endpoint(
    hangout_id: RowIdPath,
    payload: SetupHangoutRequest | None = None,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> Hangout:
    hangout = load_hangout(db, hangout_id, workspace)
    if not hangout:
        raise HTTPException(404, "Hangout not found")
    # An omitted body means "reuse the hangout's existing invitees". An
    # explicit empty list is an empty selection and must be rejected by the
    # service instead of silently reusing those invitees.
    profile_ids = payload.profile_ids if payload is not None else None
    try:
        return setup_hangout(db, hangout, profile_ids, workspace)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/hangouts/{hangout_id}/close", response_model=HangoutOut)
def close_hangout(
    hangout_id: RowIdPath,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> Hangout:
    hangout = get_scoped(db, Hangout, hangout_id, workspace)
    if not hangout:
        raise HTTPException(404, "Hangout not found")
    hangout.status = HangoutStatus.closed
    db.commit()
    return load_hangout(db, hangout_id, workspace)  # type: ignore[return-value]
