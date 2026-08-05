from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import Allergy, Hangout, HangoutInvite, HangoutStatus, Profile, Tag
from app.schemas import (
    AllergyCreate,
    AllergyOut,
    AppSettingsOut,
    AppSettingsUpdate,
    HangoutCreate,
    HangoutOut,
    HangoutUpdate,
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
    get_or_create_app_settings,
    load_allergies_by_ids,
    load_hangout,
    load_tags_by_ids,
    normalize_allergy_name,
    normalize_tag_name,
    resolve_organizer_phone,
    setup_hangout,
)
from app.sms import normalize_phone

router = APIRouter(prefix="/api", tags=["api"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


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
def delete_tag(tag_id: int, db: Session = Depends(get_db)) -> Response:
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
def delete_allergy(allergy_id: int, db: Session = Depends(get_db)) -> Response:
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
def update_profile(profile_id: int, payload: ProfileUpdate, db: Session = Depends(get_db)) -> Profile:
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
def delete_profile(profile_id: int, db: Session = Depends(get_db)) -> Response:
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
        settings = get_or_create_app_settings(db)
        if not settings.organizer_phone:
            raise HTTPException(400, "Organizer profile required when notifications are enabled")
    hangout = Hangout(
        day_date=payload.day_date or None,
        time=payload.time or None,
        duration=payload.duration or None,
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
def get_hangout(hangout_id: int, db: Session = Depends(get_db)) -> Hangout:
    hangout = load_hangout(db, hangout_id)
    if not hangout:
        raise HTTPException(404, "Hangout not found")
    return hangout


@router.patch("/hangouts/{hangout_id}", response_model=HangoutOut)
def update_hangout(hangout_id: int, payload: HangoutUpdate, db: Session = Depends(get_db)) -> Hangout:
    hangout = db.get(Hangout, hangout_id)
    if not hangout:
        raise HTTPException(404, "Hangout not found")
    data = payload.model_dump(exclude_unset=True)
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
    hangout_id: int,
    payload: SetupHangoutRequest | None = None,
    db: Session = Depends(get_db),
) -> Hangout:
    hangout = load_hangout(db, hangout_id)
    if not hangout:
        raise HTTPException(404, "Hangout not found")
    profile_ids = payload.profile_ids if payload and payload.profile_ids else None
    try:
        return setup_hangout(db, hangout, profile_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/hangouts/{hangout_id}/close", response_model=HangoutOut)
def close_hangout(hangout_id: int, db: Session = Depends(get_db)) -> Hangout:
    hangout = db.get(Hangout, hangout_id)
    if not hangout:
        raise HTTPException(404, "Hangout not found")
    hangout.status = HangoutStatus.closed
    db.commit()
    return load_hangout(db, hangout_id)  # type: ignore[return-value]


# --- App settings ---


@router.get("/settings", response_model=AppSettingsOut)
def get_settings_endpoint(db: Session = Depends(get_db)) -> AppSettingsOut:
    row = get_or_create_app_settings(db)
    return AppSettingsOut(organizer_phone=row.organizer_phone)


@router.put("/settings", response_model=AppSettingsOut)
def update_settings(payload: AppSettingsUpdate, db: Session = Depends(get_db)) -> AppSettingsOut:
    row = get_or_create_app_settings(db)
    if payload.organizer_phone is not None:
        row.organizer_phone = normalize_phone(payload.organizer_phone) if payload.organizer_phone else None
    db.commit()
    db.refresh(row)
    return AppSettingsOut(organizer_phone=row.organizer_phone)
