from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.database import get_db
from app.ids import RowId, RowIdPath, parse_row_id
from app.models import (
    Allergy,
    Drive,
    Hangout,
    HangoutInvite,
    HangoutStatus,
    Profile,
    Tag,
    Workspace,
    YesNo,
)
from app.tenancy import current_workspace, get_scoped, scoped
from app.messages import format_day_date, format_duration, format_time, preview_message_catalog
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
    setup_hangout,
)
from app.sms import format_phone, is_valid_phone, normalize_phone

router = APIRouter(tags=["web"])
# Resolved from this file so the app does not depend on the working directory.
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))
templates.env.filters["phone"] = format_phone
templates.env.filters["day_date"] = format_day_date
templates.env.filters["time_fmt"] = format_time
templates.env.filters["duration"] = format_duration


def _clerk_enabled() -> bool:
    return get_settings().clerk_enabled


def _clerk_publishable_key() -> str:
    return get_settings().clerk_publishable_key.strip()


def _clerk_frontend_api_url() -> str:
    return get_settings().clerk_frontend_api_url.strip().rstrip("/")


# These globals keep the shared base template consistent without requiring
# every existing route to repeat auth configuration in its context dict.
templates.env.globals.update(
    clerk_enabled=_clerk_enabled,
    clerk_publishable_key=_clerk_publishable_key,
    clerk_frontend_api_url=_clerk_frontend_api_url,
)


def _optional_enum_form(value: str | None, enum_cls):  # type: ignore[no-untyped-def]
    if not value or not str(value).strip():
        return None
    try:
        return enum_cls(value.strip())
    except ValueError:
        return None


def _profiles_with_tags(db: Session, workspace: Workspace) -> list[Profile]:
    return (
        scoped(db, Profile, workspace)
        .options(joinedload(Profile.tags), joinedload(Profile.allergies))
        .order_by(Profile.name)
        .all()
    )


def _all_tags(db: Session, workspace: Workspace) -> list[Tag]:
    return scoped(db, Tag, workspace).order_by(Tag.name).all()


def _all_allergies(db: Session, workspace: Workspace) -> list[Allergy]:
    return scoped(db, Allergy, workspace).order_by(Allergy.name).all()


def _safe_redirect_path(value: str | None) -> str:
    """Keep the post-login destination on this app, never an external URL."""
    value = (value or "").strip()
    # Backslashes are treated as URL separators by browsers. Reject them in
    # addition to scheme-relative URLs so a value such as /\\evil.example
    # cannot become an external redirect after browser URL normalization.
    if value.startswith("/") and not value.startswith("//") and "\\" not in value:
        return value
    return "/"


@router.get("/sign-in", response_class=HTMLResponse)
def sign_in(request: Request) -> Response:
    return templates.TemplateResponse(
        request,
        "sign_in.html",
        {"redirect_url": _safe_redirect_path(request.query_params.get("redirect_url"))},
    )


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> HTMLResponse:
    hangouts = (
        scoped(db, Hangout, workspace)
        .options(joinedload(Hangout.invites).joinedload(HangoutInvite.profile))
        .filter(Hangout.deleted_at.is_(None))
        .order_by(Hangout.id.desc())
        .all()
    )
    profiles = scoped(db, Profile, workspace).order_by(Profile.name).all()
    return templates.TemplateResponse(
        request,
        "index.html",
        {"hangouts": hangouts, "profiles": profiles, "profile_count": len(profiles)},
    )


PROFILE_ERRORS = {
    "bad_phone": "That phone number isn't usable — enter a full number like +1 (555) 123-4567.",
    "duplicate_phone": "A profile with that phone number already exists.",
    "missing_name": "Name is required.",
}

_PROFILE_FIELD_RE = re.compile(r"^profiles\[(\d+)\]\[([a-z_]+)\]$")


def _profile_form_context(
    db: Session, workspace: Workspace, *, error: str | None = None, profile_rows=None
) -> dict:
    return {
        "tags": _all_tags(db, workspace),
        "allergies": _all_allergies(db, workspace),
        "drinks_opts": list(YesNo),
        "smokes_opts": list(YesNo),
        "drive_opts": list(Drive),
        "error": error,
        "profile_rows": profile_rows if profile_rows is not None else [{}],
    }


def _profile_rows_from_form(form) -> tuple[list[dict], bool]:  # type: ignore[no-untyped-def]
    """Read indexed profile cards, retaining legacy single-profile submissions."""
    indexes: set[int] = set()
    for key, _ in form.multi_items():
        match = _PROFILE_FIELD_RE.match(str(key))
        if match:
            indexes.add(int(match.group(1)))

    if not indexes:
        if "name" not in form and "phone" not in form:
            return [], False
        return [
            {
                "name": str(form.get("name") or ""),
                "phone": str(form.get("phone") or ""),
                "drinks": str(form.get("drinks") or ""),
                "smokes": str(form.get("smokes") or ""),
                "drive": str(form.get("drive") or ""),
                "tag_ids": [str(value) for value in form.getlist("tag_ids")],
                "allergy_ids": [str(value) for value in form.getlist("allergy_ids")],
            }
        ], False

    rows: list[dict] = []
    for index in sorted(indexes):
        prefix = f"profiles[{index}]"

        def first(field: str) -> str:
            values = form.getlist(f"{prefix}[{field}]")
            return str(values[0]) if values else ""

        def many(field: str) -> list[str]:
            return [str(value) for value in form.getlist(f"{prefix}[{field}]")]

        rows.append(
            {
                "name": first("name"),
                "phone": first("phone"),
                "drinks": first("drinks"),
                "smokes": first("smokes"),
                "drive": first("drive"),
                "tag_ids": many("tag_ids"),
                "allergy_ids": many("allergy_ids"),
            }
        )
    return rows, True


@router.get("/profiles", response_class=HTMLResponse)
def profiles_page(
    request: Request,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "profiles.html",
        {
            "profiles": _profiles_with_tags(db, workspace),
            "tags": _all_tags(db, workspace),
            "allergies": _all_allergies(db, workspace),
            "drinks_opts": list(YesNo),
            "smokes_opts": list(YesNo),
            "drive_opts": list(Drive),
            "error": PROFILE_ERRORS.get(request.query_params.get("error", "")),
        },
    )


@router.get("/profiles/new", response_class=HTMLResponse)
def profiles_new_page(
    request: Request,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "profiles_new.html",
        _profile_form_context(
            db,
            workspace,
            error=PROFILE_ERRORS.get(request.query_params.get("error", "")),
        ),
    )


@router.post("/tags")
def tags_create(
    name: str = Form(..., max_length=64),
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> RedirectResponse:
    cleaned = normalize_tag_name(name)
    if cleaned:
        existing = scoped(db, Tag, workspace).filter(Tag.name.ilike(cleaned)).first()
        if not existing:
            db.add(Tag(name=cleaned, workspace_id=workspace.id))
            db.commit()
    return RedirectResponse("/profiles", status_code=303)


@router.post("/tags/{tag_id}/delete")
def tags_delete(
    tag_id: RowIdPath,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> RedirectResponse:
    tag = get_scoped(db, Tag, tag_id, workspace)
    if tag:
        db.delete(tag)
        db.commit()
    return RedirectResponse("/profiles", status_code=303)


@router.post("/profiles", response_model=None)
async def profiles_create(
    request: Request,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> RedirectResponse | HTMLResponse:
    form = await request.form()
    rows, is_batch = _profile_rows_from_form(form)
    if not rows:
        return RedirectResponse("/profiles?error=missing_name", status_code=303)

    errors: list[str] = []
    validated_rows: list[dict] = []
    seen_phones: set[str] = set()
    for position, row in enumerate(rows, start=1):
        name = row["name"].strip()
        phone = row["phone"].strip()
        phone_n = normalize_phone(phone)
        prefix = f"Profile {position}: " if is_batch else ""
        if not name:
            errors.append(f"{prefix}Name is required.")
        if not is_valid_phone(phone_n):
            errors.append(
                f"{prefix}That phone number isn't usable — enter a full number like +1 (555) 123-4567."
            )
        elif phone_n in seen_phones or (
            scoped(db, Profile, workspace).filter(Profile.phone == phone_n).first()
        ):
            errors.append(f"{prefix}A profile with that phone number already exists.")
        else:
            seen_phones.add(phone_n)
        validated_rows.append(
            {
                **row,
                "name": name,
                "phone": phone,
                "phone_normalized": phone_n,
            }
        )

    if errors:
        if not is_batch:
            if any("phone number isn't usable" in error for error in errors):
                error_key = "bad_phone"
            elif any("already exists" in error for error in errors):
                error_key = "duplicate_phone"
            else:
                error_key = "missing_name"
            return RedirectResponse(f"/profiles?error={error_key}", status_code=303)
        return templates.TemplateResponse(
            request,
            "profiles_new.html",
            _profile_form_context(
                db, workspace, error=" ".join(errors), profile_rows=validated_rows
            ),
            status_code=400,
        )

    for row in validated_rows:
        profile = Profile(
            name=row["name"],
            phone=row["phone_normalized"],
            drinks=_optional_enum_form(row["drinks"], YesNo),
            smokes=_optional_enum_form(row["smokes"], YesNo),
            drive=_optional_enum_form(row["drive"], Drive),
            workspace_id=workspace.id,
        )
        tag_ids = [
            parsed for value in row["tag_ids"] if (parsed := parse_row_id(value)) is not None
        ]
        allergy_ids = [
            parsed for value in row["allergy_ids"] if (parsed := parse_row_id(value)) is not None
        ]
        profile.tags = load_tags_by_ids(db, tag_ids, workspace)
        profile.allergies = load_allergies_by_ids(db, allergy_ids, workspace)
        db.add(profile)
    db.commit()
    return RedirectResponse("/profiles", status_code=303)


@router.post("/profiles/{profile_id}/delete")
def profiles_delete(
    profile_id: RowIdPath,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> RedirectResponse:
    profile = get_scoped(db, Profile, profile_id, workspace)
    if profile:
        db.delete(profile)
        db.commit()
    return RedirectResponse("/profiles", status_code=303)


@router.get("/hangouts/new", response_class=HTMLResponse)
def hangout_new(
    request: Request,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "hangout_new.html",
        _hangout_form_context(db, workspace),
    )


def _hangout_form_context(
    db: Session,
    workspace: Workspace,
    *,
    hangout: Hangout | None = None,
    error: str | None = None,
) -> dict:
    return {
        "hangout": hangout,
        "profiles": _profiles_with_tags(db, workspace),
        "tags": _all_tags(db, workspace),
        "invited_ids": {invite.profile_id for invite in hangout.invites} if hangout else set(),
        "error": error,
        "google_places_enabled": bool(get_settings().google_maps_api_key.strip()),
        "alcohol_opts": list(YesNo),
        "weed_opts": list(YesNo),
        "interval_hour_opts": INTERVAL_HOUR_OPTIONS,
        "cooldown_minute_opts": COOLDOWN_MINUTE_OPTIONS,
        "confirm_goal_opts": CONFIRM_GOAL_OPTIONS,
    }


def _apply_hangout_form(
    db: Session,
    workspace: Workspace,
    hangout: Hangout,
    *,
    day_date: str,
    time: str,
    duration: str,
    location: str,
    motive: str,
    alcohol_involved: str,
    weed_involved: str,
    notes: str,
    organizer_profile_id: str,
    notify_enabled: str | None,
    notify_interval: str | None,
    notify_threshold: str | None,
    notify_interval_hours: str,
    notify_interval_only_if_changed: str | None,
    notify_on_new_confirm: str | None,
    notify_on_decline: str | None,
    notify_on_allergy: str | None,
    notify_on_ride_needed: str | None,
    notify_confirm_goal: str,
    notify_threshold_cooldown_minutes: str,
) -> None:
    """Apply the shared new/edit form fields to a draft hangout."""
    organizer_id = parse_row_id(organizer_profile_id)
    org_profile = (
        get_scoped(db, Profile, organizer_id, workspace) if organizer_id is not None else None
    )
    notify = notify_enabled is not None
    # Notifications require an organizer profile with a phone.
    if notify and not (org_profile and org_profile.phone):
        notify = False

    hangout.day_date = day_date.strip() or None
    hangout.time = time.strip() or None
    hangout.duration = duration.strip() or None
    hangout.location = location.strip() or None
    hangout.motive = motive.strip() or None
    hangout.alcohol_involved = _optional_enum_form(alcohol_involved, YesNo)
    hangout.weed_involved = _optional_enum_form(weed_involved, YesNo)
    hangout.notes = notes.strip() or None
    hangout.organizer = org_profile
    hangout.organizer_profile_id = org_profile.id if org_profile else None
    hangout.organizer_phone = org_profile.phone if org_profile else None
    hangout.notify_enabled = notify
    hangout.notify_interval = notify and notify_interval is not None
    hangout.notify_threshold = notify and notify_threshold is not None
    hangout.notify_interval_hours = clamp_choice(notify_interval_hours, INTERVAL_HOUR_OPTIONS, 6)
    hangout.notify_interval_only_if_changed = notify_interval_only_if_changed is not None
    hangout.notify_on_new_confirm = notify_on_new_confirm is not None
    hangout.notify_on_decline = notify_on_decline is not None
    hangout.notify_on_allergy = notify_on_allergy is not None
    hangout.notify_on_ride_needed = notify_on_ride_needed is not None
    hangout.notify_confirm_goal = clamp_choice(notify_confirm_goal, CONFIRM_GOAL_OPTIONS, 0)
    hangout.notify_threshold_cooldown_minutes = clamp_choice(
        notify_threshold_cooldown_minutes, COOLDOWN_MINUTE_OPTIONS, 0
    )


def _valid_profile_ids(
    db: Session, workspace: Workspace, profile_ids: list[RowId] | None
) -> list[int]:
    return list(
        dict.fromkeys(
            pid for pid in profile_ids or [] if get_scoped(db, Profile, pid, workspace) is not None
        )
    )


def _sync_draft_invitees(
    db: Session, workspace: Workspace, hangout: Hangout, profile_ids: list[int]
) -> None:
    """Make a draft's selected invitees match its edit form."""
    selected_ids = set(profile_ids)
    existing_ids = {invite.profile_id for invite in hangout.invites}
    for invite in list(hangout.invites):
        if invite.profile_id not in selected_ids:
            db.delete(invite)
    for profile_id in profile_ids:
        if profile_id not in existing_ids:
            db.add(
                HangoutInvite(
                    hangout_id=hangout.id,
                    profile_id=profile_id,
                    workspace_id=workspace.id,
                )
            )


@router.post("/hangouts/new")
def hangout_create(
    day_date: str = Form(""),
    time: str = Form(""),
    duration: str = Form(""),
    location: str = Form(""),
    motive: str = Form(""),
    alcohol_involved: str = Form(""),
    weed_involved: str = Form(""),
    notes: str = Form(""),
    organizer_profile_id: str = Form(""),
    notify_enabled: str | None = Form(None),
    notify_interval: str | None = Form(None),
    notify_threshold: str | None = Form(None),
    notify_interval_hours: str = Form("6"),
    notify_interval_only_if_changed: str | None = Form(None),
    notify_on_new_confirm: str | None = Form(None),
    notify_on_decline: str | None = Form(None),
    notify_on_allergy: str | None = Form(None),
    notify_on_ride_needed: str | None = Form(None),
    notify_confirm_goal: str = Form("0"),
    notify_threshold_cooldown_minutes: str = Form("0"),
    profile_ids: Annotated[list[RowId] | None, Form()] = None,
    action: str = Form("draft"),
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> RedirectResponse:
    hangout = Hangout(status=HangoutStatus.draft, workspace_id=workspace.id)
    _apply_hangout_form(
        db,
        workspace,
        hangout,
        day_date=day_date,
        time=time,
        duration=duration,
        location=location,
        motive=motive,
        alcohol_involved=alcohol_involved,
        weed_involved=weed_involved,
        notes=notes,
        organizer_profile_id=organizer_profile_id,
        notify_enabled=notify_enabled,
        notify_interval=notify_interval,
        notify_threshold=notify_threshold,
        notify_interval_hours=notify_interval_hours,
        notify_interval_only_if_changed=notify_interval_only_if_changed,
        notify_on_new_confirm=notify_on_new_confirm,
        notify_on_decline=notify_on_decline,
        notify_on_allergy=notify_on_allergy,
        notify_on_ride_needed=notify_on_ride_needed,
        notify_confirm_goal=notify_confirm_goal,
        notify_threshold_cooldown_minutes=notify_threshold_cooldown_minutes,
    )
    db.add(hangout)
    db.flush()
    ids = _valid_profile_ids(db, workspace, profile_ids)
    _sync_draft_invitees(db, workspace, hangout, ids)
    db.commit()

    if action == "setup":
        hangout = load_hangout(db, hangout.id, workspace)  # type: ignore[assignment]
        try:
            setup_hangout(db, hangout, ids, workspace)
        except ValueError:
            return RedirectResponse(
                f"/hangouts/{hangout.id}/edit?error=need_profiles", status_code=303
            )

    return RedirectResponse(f"/hangouts/{hangout.id}/edit", status_code=303)


@router.get("/hangouts/{hangout_id}/edit", response_class=HTMLResponse)
def hangout_edit(
    request: Request,
    hangout_id: RowIdPath,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> HTMLResponse:
    hangout = load_hangout(db, hangout_id, workspace)
    if not hangout or hangout.status != HangoutStatus.draft or hangout.deleted_at is not None:
        return RedirectResponse(f"/hangouts/{hangout_id}" if hangout else "/", status_code=303)
    return templates.TemplateResponse(
        request,
        "hangout_new.html",
        _hangout_form_context(
            db,
            workspace,
            hangout=hangout,
            error=request.query_params.get("error"),
        ),
    )


@router.post("/hangouts/{hangout_id}/edit")
def hangout_update_draft(
    hangout_id: RowIdPath,
    request: Request,
    day_date: str = Form(""),
    time: str = Form(""),
    duration: str = Form(""),
    location: str = Form(""),
    motive: str = Form(""),
    alcohol_involved: str = Form(""),
    weed_involved: str = Form(""),
    notes: str = Form(""),
    organizer_profile_id: str = Form(""),
    notify_enabled: str | None = Form(None),
    notify_interval: str | None = Form(None),
    notify_threshold: str | None = Form(None),
    notify_interval_hours: str = Form("6"),
    notify_interval_only_if_changed: str | None = Form(None),
    notify_on_new_confirm: str | None = Form(None),
    notify_on_decline: str | None = Form(None),
    notify_on_allergy: str | None = Form(None),
    notify_on_ride_needed: str | None = Form(None),
    notify_confirm_goal: str = Form("0"),
    notify_threshold_cooldown_minutes: str = Form("0"),
    profile_ids: Annotated[list[RowId] | None, Form()] = None,
    action: str = Form("draft"),
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> Response:
    hangout = load_hangout(db, hangout_id, workspace)
    if not hangout:
        return RedirectResponse("/", status_code=303)
    if hangout.status != HangoutStatus.draft or hangout.deleted_at is not None:
        return RedirectResponse(f"/hangouts/{hangout_id}", status_code=303)

    _apply_hangout_form(
        db,
        workspace,
        hangout,
        day_date=day_date,
        time=time,
        duration=duration,
        location=location,
        motive=motive,
        alcohol_involved=alcohol_involved,
        weed_involved=weed_involved,
        notes=notes,
        organizer_profile_id=organizer_profile_id,
        notify_enabled=notify_enabled,
        notify_interval=notify_interval,
        notify_threshold=notify_threshold,
        notify_interval_hours=notify_interval_hours,
        notify_interval_only_if_changed=notify_interval_only_if_changed,
        notify_on_new_confirm=notify_on_new_confirm,
        notify_on_decline=notify_on_decline,
        notify_on_allergy=notify_on_allergy,
        notify_on_ride_needed=notify_on_ride_needed,
        notify_confirm_goal=notify_confirm_goal,
        notify_threshold_cooldown_minutes=notify_threshold_cooldown_minutes,
    )
    ids = _valid_profile_ids(db, workspace, profile_ids)
    _sync_draft_invitees(db, workspace, hangout, ids)
    db.commit()

    if action == "setup":
        hangout = load_hangout(db, hangout_id, workspace)  # type: ignore[assignment]
        try:
            setup_hangout(db, hangout, ids, workspace)
        except ValueError:
            return RedirectResponse(
                f"/hangouts/{hangout_id}/edit?error=need_profiles", status_code=303
            )
        return RedirectResponse(f"/hangouts/{hangout_id}", status_code=303)

    if "application/json" in request.headers.get("accept", ""):
        return Response(status_code=204)
    return RedirectResponse(f"/hangouts/{hangout_id}/edit", status_code=303)


@router.get("/hangouts/{hangout_id}", response_class=HTMLResponse)
def hangout_detail(
    request: Request,
    hangout_id: RowIdPath,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> HTMLResponse:
    hangout = load_hangout(db, hangout_id, workspace)
    if not hangout:
        return RedirectResponse("/", status_code=303)
    if hangout.status == HangoutStatus.draft and hangout.deleted_at is None:
        error = request.query_params.get("error")
        suffix = "?error=need_profiles" if error == "need_profiles" else ""
        return RedirectResponse(f"/hangouts/{hangout_id}/edit{suffix}", status_code=303)
    invited_ids = {i.profile_id for i in hangout.invites}
    return templates.TemplateResponse(
        request,
        "hangout_detail.html",
        {
            "hangout": hangout,
            "all_profiles": _profiles_with_tags(db, workspace),
            "tags": _all_tags(db, workspace),
            "invited_ids": invited_ids,
            "error": request.query_params.get("error"),
        },
    )


@router.post("/hangouts/{hangout_id}/setup")
def hangout_setup(
    hangout_id: RowIdPath,
    profile_ids: Annotated[list[RowId] | None, Form()] = None,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> RedirectResponse:
    hangout = load_hangout(db, hangout_id, workspace)
    if not hangout:
        return RedirectResponse("/", status_code=303)
    ids = list(profile_ids or [])
    try:
        setup_hangout(db, hangout, ids if ids else None, workspace)
    except ValueError:
        return RedirectResponse(f"/hangouts/{hangout_id}?error=need_profiles", status_code=303)
    return RedirectResponse(f"/hangouts/{hangout_id}", status_code=303)


@router.post("/hangouts/{hangout_id}/close")
def hangout_close(
    hangout_id: RowIdPath,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> RedirectResponse:
    hangout = get_scoped(db, Hangout, hangout_id, workspace)
    if hangout and hangout.deleted_at is None:
        hangout.status = HangoutStatus.closed
        db.commit()
    return RedirectResponse(f"/hangouts/{hangout_id}", status_code=303)


@router.post("/hangouts/{hangout_id}/delete")
def hangout_soft_delete(
    hangout_id: RowIdPath,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> RedirectResponse:
    """Hide a closed hangout from the main list (soft delete)."""
    from app.services import utcnow

    hangout = get_scoped(db, Hangout, hangout_id, workspace)
    if hangout and hangout.status == HangoutStatus.closed and hangout.deleted_at is None:
        hangout.deleted_at = utcnow()
        db.commit()
        return RedirectResponse("/?toast=hangout_deleted", status_code=303)
    if hangout:
        return RedirectResponse(f"/hangouts/{hangout_id}", status_code=303)
    return RedirectResponse("/", status_code=303)


@router.post("/hangouts/{hangout_id}/restore")
def hangout_restore(
    hangout_id: RowIdPath,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> RedirectResponse:
    """Un-hide a soft-deleted hangout."""
    hangout = get_scoped(db, Hangout, hangout_id, workspace)
    if hangout and hangout.deleted_at is not None:
        hangout.deleted_at = None
        db.commit()
    if hangout:
        return RedirectResponse(f"/hangouts/{hangout_id}", status_code=303)
    return RedirectResponse("/settings/deleted-hangouts", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "settings.html",
        {"allergies": _all_allergies(db, workspace)},
    )


@router.get("/settings/deleted-hangouts", response_class=HTMLResponse)
def deleted_hangouts_page(
    request: Request,
    q: str = "",
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> HTMLResponse:
    query = (
        scoped(db, Hangout, workspace)
        .options(joinedload(Hangout.invites).joinedload(HangoutInvite.profile))
        .filter(Hangout.deleted_at.isnot(None))
    )
    term = (q or "").strip()
    if term:
        like = f"%{term}%"
        query = query.filter(Hangout.motive.ilike(like))
    hangouts = query.order_by(Hangout.deleted_at.desc(), Hangout.id.desc()).all()
    return templates.TemplateResponse(
        request,
        "deleted_hangouts.html",
        {"hangouts": hangouts, "q": term},
    )


@router.get("/settings/sms-simulator", response_class=HTMLResponse)
def sms_simulator_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "sms_simulator.html",
        {"messages": preview_message_catalog()},
    )


@router.get("/settings/logs", response_class=FileResponse)
def download_logs() -> FileResponse:
    """Serve the active JSONL audit log as a downloadable file."""
    log_path = Path(get_settings().log_file).expanduser()
    # Flush so the download includes events still buffered in handlers.
    for handler in logging.root.handlers:
        try:
            handler.flush()
        except OSError:
            pass
    if not log_path.is_file():
        raise HTTPException(404, "Log file not found")
    return FileResponse(
        path=log_path,
        filename=log_path.name,
        media_type="application/x-ndjson",
    )


@router.post("/allergies")
def allergies_create(
    name: str = Form(..., max_length=64),
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> RedirectResponse:
    cleaned = normalize_allergy_name(name)
    if cleaned:
        existing = scoped(db, Allergy, workspace).filter(Allergy.name.ilike(cleaned)).first()
        if not existing:
            db.add(Allergy(name=cleaned, workspace_id=workspace.id))
            db.commit()
    return RedirectResponse("/settings", status_code=303)


@router.post("/allergies/{allergy_id}/delete")
def allergies_delete(
    allergy_id: RowIdPath,
    db: Session = Depends(get_db),
    workspace: Workspace = Depends(current_workspace),
) -> RedirectResponse:
    allergy = get_scoped(db, Allergy, allergy_id, workspace)
    if allergy:
        db.delete(allergy)
        db.commit()
    return RedirectResponse("/settings", status_code=303)
