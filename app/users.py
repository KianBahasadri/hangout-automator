"""Account-holder (My Profile) helpers.

`User` rows are personal, instance-wide settings for the signed-in organizer.
They are not the workspace-scoped contact directory (`profiles`).
"""

from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import User
from app.services import (
    COOLDOWN_MINUTE_OPTIONS,
    CONFIRM_GOAL_OPTIONS,
    INTERVAL_HOUR_OPTIONS,
    clamp_choice,
)
from app.sms import is_valid_phone, normalize_phone

# When Clerk is off, every local request shares one account-holder row so My
# Profile and hangout prefill still work without a session subject.
LOCAL_USER_ID = "local-dev"


def identity_for_request(request: Request) -> str:
    """Stable id for the account-holder of this request."""
    if get_settings().clerk_enabled:
        clerk_user_id = getattr(request.state, "clerk_user_id", None)
        if not clerk_user_id:
            # Protected routes should never reach here without a subject; fall
            # back to local so a misconfigured test still gets a row rather
            # than a 500. Auth middleware already refuses the empty case.
            return LOCAL_USER_ID
        return str(clerk_user_id)
    return LOCAL_USER_ID


def get_or_create_user(
    db: Session,
    clerk_user_id: str,
    *,
    email: str | None = None,
) -> User:
    """Return the account-holder row, creating an empty one on first use."""
    user = db.query(User).filter(User.clerk_user_id == clerk_user_id).first()
    if user is not None:
        if email and not user.email:
            user.email = email
            db.flush()
        return user

    user = User(clerk_user_id=clerk_user_id, email=email)
    db.add(user)
    db.flush()
    return user


def user_for_request(db: Session, request: Request) -> User:
    """Load or create the account-holder for the current request identity."""
    email = getattr(request.state, "clerk_email", None)
    return get_or_create_user(db, identity_for_request(request), email=email)


def apply_user_form(
    user: User,
    *,
    display_name: str,
    phone: str,
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
) -> str | None:
    """Mutate `user` from My Profile form fields. Returns an error string or None."""
    name = display_name.strip() or None
    if name is not None:
        name = name[:120]
    raw_phone = phone.strip()
    if raw_phone:
        normalized = normalize_phone(raw_phone)
        if not is_valid_phone(normalized):
            return "invalid-phone"
        new_phone = normalized[:32]
    else:
        new_phone = None

    if new_phone != user.phone:
        # Phone OTP (KIAN-527) will re-verify; clear any prior verification now.
        user.phone_verified_at = None
    user.display_name = name
    user.phone = new_phone

    user.default_notify_enabled = notify_enabled is not None
    user.default_notify_interval = (
        user.default_notify_enabled and notify_interval is not None
    )
    user.default_notify_threshold = (
        user.default_notify_enabled and notify_threshold is not None
    )
    user.default_notify_interval_hours = clamp_choice(
        notify_interval_hours, INTERVAL_HOUR_OPTIONS, 6
    )
    user.default_notify_interval_only_if_changed = notify_interval_only_if_changed is not None
    user.default_notify_on_new_confirm = notify_on_new_confirm is not None
    user.default_notify_on_decline = notify_on_decline is not None
    user.default_notify_on_allergy = notify_on_allergy is not None
    user.default_notify_on_ride_needed = notify_on_ride_needed is not None
    user.default_notify_confirm_goal = clamp_choice(
        notify_confirm_goal, CONFIRM_GOAL_OPTIONS, 0
    )
    user.default_notify_threshold_cooldown_minutes = clamp_choice(
        notify_threshold_cooldown_minutes, COOLDOWN_MINUTE_OPTIONS, 0
    )
    return None
