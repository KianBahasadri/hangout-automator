from __future__ import annotations

import logging
import re
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi import Query
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.database import get_db
from app.ids import RowIdPath
from app.models import Allergy, Hangout, HangoutInvite, HangoutStatus, Profile, Tag, not_null_columns
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
def list_tags(db: Session = Depends(get_db)) -> list[Tag]:
    return db.query(Tag).order_by(Tag.name).all()


@router.post("/tags", response_model=TagOut, status_code=201)
def create_tag(payload: TagCreate, db: Session = Depends(get_db)) -> Tag:
    name = normalize_tag_name(payload.name)
    if not name:
        raise HTTPException(400, "Tag name is required")
    if db.query(Tag).filter(Tag.name.ilike(name)).first():
        raise HTTPException(400, "A tag with this name already exists")
    tag = Tag(name=name)
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


@router.delete("/tags/{tag_id}", status_code=204, response_class=Response)
def delete_tag(tag_id: RowIdPath, db: Session = Depends(get_db)) -> Response:
    tag = db.get(Tag, tag_id)
    if not tag:
        raise HTTPException(404, "Tag not found")
    db.delete(tag)
    db.commit()
    return Response(status_code=204)


# --- Allergies ---


@router.get("/allergies", response_model=list[AllergyOut])
def list_allergies(db: Session = Depends(get_db)) -> list[Allergy]:
    return db.query(Allergy).order_by(Allergy.name).all()


@router.post("/allergies", response_model=AllergyOut, status_code=201)
def create_allergy(payload: AllergyCreate, db: Session = Depends(get_db)) -> Allergy:
    name = normalize_allergy_name(payload.name)
    if not name:
        raise HTTPException(400, "Allergy name is required")
    if db.query(Allergy).filter(Allergy.name.ilike(name)).first():
        raise HTTPException(400, "An allergy with this name already exists")
    allergy = Allergy(name=name)
    db.add(allergy)
    db.commit()
    db.refresh(allergy)
    return allergy


@router.delete("/allergies/{allergy_id}", status_code=204, response_class=Response)
def delete_allergy(allergy_id: RowIdPath, db: Session = Depends(get_db)) -> Response:
    allergy = db.get(Allergy, allergy_id)
    if not allergy:
        raise HTTPException(404, "Allergy not found")
    db.delete(allergy)
    db.commit()
    return Response(status_code=204)


# --- Profiles ---


def _profile_query(db: Session):
    return db.query(Profile).options(
        joinedload(Profile.tags),
        joinedload(Profile.allergies),
    )


@router.get("/profiles", response_model=list[ProfileOut])
def list_profiles(db: Session = Depends(get_db)) -> list[Profile]:
    return _profile_query(db).order_by(Profile.name).all()


@router.post("/profiles", response_model=ProfileOut, status_code=201)
def create_profile(payload: ProfileCreate, db: Session = Depends(get_db)) -> Profile:
    phone = normalize_phone(payload.phone)
    if not is_valid_phone(phone):
        raise HTTPException(400, "Phone number is not usable")
    if db.query(Profile).filter(Profile.phone == phone).first():
        raise HTTPException(400, "A profile with this phone already exists")
    profile = Profile(
        name=payload.name.strip(),
        phone=phone,
        drinks=payload.drinks,
        smokes=payload.smokes,
        drive=payload.drive,
    )
    profile.tags = load_tags_by_ids(db, payload.tag_ids)
    profile.allergies = load_allergies_by_ids(db, payload.allergy_ids)
    db.add(profile)
    db.commit()
    return _profile_query(db).filter(Profile.id == profile.id).one()


@router.patch("/profiles/{profile_id}", response_model=ProfileOut)
def update_profile(profile_id: RowIdPath, payload: ProfileUpdate, db: Session = Depends(get_db)) -> Profile:
    profile = db.get(Profile, profile_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    data = payload.model_dump(exclude_unset=True)
    tag_ids = data.pop("tag_ids", None)
    allergy_ids = data.pop("allergy_ids", None)
    if "phone" in data:
        if not data["phone"] or not str(data["phone"]).strip():
            raise HTTPException(400, "Phone is required")
        data["phone"] = normalize_phone(data["phone"])
        if not is_valid_phone(data["phone"]):
            raise HTTPException(400, "Phone number is not usable")
        clash = db.query(Profile).filter(Profile.phone == data["phone"], Profile.id != profile_id).first()
        if clash:
            raise HTTPException(400, "A profile with this phone already exists")
    if "name" in data:
        if not data["name"] or not str(data["name"]).strip():
            raise HTTPException(400, "Name is required")
        data["name"] = data["name"].strip()
    for k, v in data.items():
        setattr(profile, k, v)
    if tag_ids is not None:
        profile.tags = load_tags_by_ids(db, tag_ids)
    if allergy_ids is not None:
        profile.allergies = load_allergies_by_ids(db, allergy_ids)
    db.commit()
    return _profile_query(db).filter(Profile.id == profile_id).one()


@router.delete("/profiles/{profile_id}", status_code=204, response_class=Response)
def delete_profile(profile_id: RowIdPath, db: Session = Depends(get_db)) -> Response:
    profile = db.get(Profile, profile_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    db.delete(profile)
    db.commit()
    return Response(status_code=204)


# --- Hangouts ---


@router.get("/hangouts", response_model=list[HangoutOut])
def list_hangouts(db: Session = Depends(get_db)) -> list[Hangout]:
    return (
        db.query(Hangout)
        .options(joinedload(Hangout.invites).joinedload(HangoutInvite.profile))
        .filter(Hangout.deleted_at.is_(None))
        .order_by(Hangout.id.desc())
        .all()
    )


@router.post("/hangouts", response_model=HangoutOut, status_code=201)
def create_hangout(payload: HangoutCreate, db: Session = Depends(get_db)) -> Hangout:
    org_profile = None
    if payload.organizer_profile_id is not None:
        org_profile = db.get(Profile, payload.organizer_profile_id)
        if org_profile is None:
            raise HTTPException(400, "Organizer profile not found")
    org_phone = org_profile.phone if org_profile else None
    if payload.notify_enabled and not org_phone:
        raise HTTPException(400, "Organizer profile required when notifications are enabled")
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
        organizer_profile_id=org_profile.id if org_profile else None,
        organizer_phone=org_phone,
        notify_enabled=payload.notify_enabled,
        notify_interval=payload.notify_enabled and payload.notify_interval,
        notify_threshold=payload.notify_enabled and payload.notify_threshold,
        notify_interval_hours=clamp_choice(
            payload.notify_interval_hours, INTERVAL_HOUR_OPTIONS, 6
        ),
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
        if db.get(Profile, pid):
            db.add(HangoutInvite(hangout_id=hangout.id, profile_id=pid))
    db.commit()
    return load_hangout(db, hangout.id)  # type: ignore[return-value]


@router.get("/hangouts/{hangout_id}", response_model=HangoutOut)
def get_hangout(hangout_id: RowIdPath, db: Session = Depends(get_db)) -> Hangout:
    hangout = load_hangout(db, hangout_id)
    if not hangout:
        raise HTTPException(404, "Hangout not found")
    return hangout


@router.patch("/hangouts/{hangout_id}", response_model=HangoutOut)
def update_hangout(hangout_id: RowIdPath, payload: HangoutUpdate, db: Session = Depends(get_db)) -> Hangout:
    hangout = db.get(Hangout, hangout_id)
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
            org_profile = db.get(Profile, org_id)
            if org_profile is None:
                raise HTTPException(400, "Organizer profile not found")
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
    if "notify_threshold_cooldown_minutes" in data and data["notify_threshold_cooldown_minutes"] is not None:
        data["notify_threshold_cooldown_minutes"] = clamp_choice(
            data["notify_threshold_cooldown_minutes"],
            COOLDOWN_MINUTE_OPTIONS,
            hangout.notify_threshold_cooldown_minutes or 0,
        )
    for k, v in data.items():
        setattr(hangout, k, v)
    if hangout.notify_enabled:
        if not resolve_organizer_phone(db, hangout):
            raise HTTPException(400, "Organizer profile required when notifications are enabled")
    db.commit()
    return load_hangout(db, hangout_id)  # type: ignore[return-value]


@router.post("/hangouts/{hangout_id}/setup", response_model=HangoutOut)
def setup_hangout_endpoint(
    hangout_id: RowIdPath,
    payload: SetupHangoutRequest | None = None,
    db: Session = Depends(get_db),
) -> Hangout:
    hangout = load_hangout(db, hangout_id)
    if not hangout:
        raise HTTPException(404, "Hangout not found")
    # An omitted body means "reuse the hangout's existing invitees". An
    # explicit empty list is an empty selection and must be rejected by the
    # service instead of silently reusing those invitees.
    profile_ids = payload.profile_ids if payload is not None else None
    try:
        return setup_hangout(db, hangout, profile_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/hangouts/{hangout_id}/close", response_model=HangoutOut)
def close_hangout(hangout_id: RowIdPath, db: Session = Depends(get_db)) -> Hangout:
    hangout = db.get(Hangout, hangout_id)
    if not hangout:
        raise HTTPException(404, "Hangout not found")
    hangout.status = HangoutStatus.closed
    db.commit()
    return load_hangout(db, hangout_id)  # type: ignore[return-value]
