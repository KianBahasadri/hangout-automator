#!/usr/bin/env bash
# Seed the *development* database with many contact (profile) variations
# for UI filter / invitee-picker / SMS simulator testing.
#
# Seeds into a real user's workspace (not the legacy shared `default` tenant).
#
# Usage:
#   ./scripts/seed_dev_contacts.sh --email you@example.com
#   ./scripts/seed_dev_contacts.sh --email you@example.com --reset
#   SEED_EMAIL=you@example.com ./scripts/seed_dev_contacts.sh
#   ./scripts/seed_dev_contacts.sh --force   # allow non-development HANGOUT_ENV
#
# Looks up the Clerk user by email (needs CLERK_SECRET_KEY), ensures a
# workspace + owner membership, then upserts seed contacts there.
set -euo pipefail

cd "$(dirname "$0")/.."

RESET=0
FORCE=0
EMAIL="${SEED_EMAIL:-}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --reset) RESET=1; shift ;;
    --force) FORCE=1; shift ;;
    --email)
      EMAIL="${2:-}"
      if [[ -z "$EMAIL" ]]; then
        echo "--email requires an address" >&2
        exit 2
      fi
      shift 2
      ;;
    -h|--help)
      sed -n '2,18p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown option: $1 (try --help)" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$EMAIL" ]]; then
  echo "Pass --email you@example.com (or set SEED_EMAIL). Seed no longer uses the default workspace." >&2
  exit 2
fi

ENV_NAME="${HANGOUT_ENV:-development}"
if [[ "$ENV_NAME" == "production" && "$FORCE" -ne 1 ]]; then
  echo "Refusing to seed: HANGOUT_ENV=production (pass --force if you really mean it)." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required (run from the repo with the project toolchain)." >&2
  exit 1
fi

export HANGOUT_ENV="$ENV_NAME"
export SEED_RESET="$RESET"
export SEED_EMAIL="$EMAIL"

echo "Seeding contacts for $EMAIL (HANGOUT_ENV=$HANGOUT_ENV, reset=$RESET)…"

uv run python - <<'PY'
from __future__ import annotations

import os
import sys

import httpx
from sqlalchemy.exc import IntegrityError

from app.access import normalize_email
from app.config import get_settings
from app.database import SessionLocal
from app.models import (
    AccessGrant,
    AccessRole,
    Allergy,
    Drive,
    Profile,
    Tag,
    Workspace,
    WorkspaceMember,
    WorkspaceRole,
    YesNo,
)
from app.sms import is_valid_phone, normalize_phone

SEED_PREFIX = "+15551001"
EMAIL = normalize_email(os.environ["SEED_EMAIL"])


def yn(value: str | None) -> YesNo | None:
    if value is None:
        return None
    return YesNo(value)


def drive_val(value: str | None) -> Drive | None:
    if value is None:
        return None
    return Drive(value)


def get_or_create_tag(db, workspace: Workspace, name: str) -> Tag:
    row = (
        db.query(Tag)
        .filter(Tag.workspace_id == workspace.id, Tag.name == name)
        .one_or_none()
    )
    if row:
        return row
    row = Tag(name=name, workspace_id=workspace.id)
    db.add(row)
    db.flush()
    return row


def get_or_create_allergy(db, workspace: Workspace, name: str) -> Allergy:
    row = (
        db.query(Allergy)
        .filter(Allergy.workspace_id == workspace.id, Allergy.name == name)
        .one_or_none()
    )
    if row:
        return row
    row = Allergy(name=name, workspace_id=workspace.id)
    db.add(row)
    db.flush()
    return row


def clerk_user_id_for_email(settings, email: str) -> str:
    secret = (settings.clerk_secret_key or "").strip()
    if not secret:
        raise SystemExit(
            "CLERK_SECRET_KEY is required to resolve the workspace owner by email."
        )
    response = httpx.get(
        "https://api.clerk.com/v1/users",
        params={"email_address": [email]},
        headers={"Authorization": f"Bearer {secret}"},
        timeout=30.0,
    )
    response.raise_for_status()
    users = response.json()
    if not isinstance(users, list) or not users:
        raise SystemExit(f"No Clerk user found for {email!r}.")
    user_id = users[0].get("id")
    if not isinstance(user_id, str) or not user_id:
        raise SystemExit(f"Clerk response missing user id for {email!r}.")
    return user_id


def ensure_workspace(db, clerk_user_id: str, email: str) -> Workspace:
    member = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.clerk_user_id == clerk_user_id)
        .one_or_none()
    )
    if member is not None:
        if not member.email:
            member.email = email
            db.commit()
        workspace = db.get(Workspace, member.workspace_id)
        if workspace is None:
            raise SystemExit("Membership points at a missing workspace.")
        return workspace

    slug = f"user-{clerk_user_id}"[:64]
    workspace = Workspace(name="My workspace", slug=slug)
    db.add(workspace)
    db.flush()
    db.add(
        WorkspaceMember(
            workspace_id=workspace.id,
            clerk_user_id=clerk_user_id,
            email=email,
            role=WorkspaceRole.owner,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        member = (
            db.query(WorkspaceMember)
            .filter(WorkspaceMember.clerk_user_id == clerk_user_id)
            .one()
        )
        return db.get(Workspace, member.workspace_id)
    db.refresh(workspace)
    return workspace


def ensure_access_grant(db, email: str) -> None:
    existing = db.query(AccessGrant).filter(AccessGrant.email == email).one_or_none()
    if existing is not None:
        return
    db.add(AccessGrant(email=email, role=AccessRole.admin, created_by=None))
    db.commit()
    print(f"Added access grant: {email} (admin)")


CONTACTS: list[tuple] = [
    ("Alex Minimal", 0, None, None, None, [], []),
    ("Blake Blank", 1, None, None, None, [], []),
    ("Casey Drives", 2, "yes", "no", "yes", ["friends"], []),
    ("Dana Needs Ride", 3, "yes", "no", "no", ["friends"], []),
    ("Elliot Maybe Drive", 4, "no", "no", "maybe", ["friends"], []),
    ("Finley Drive Only", 5, None, None, "yes", [], []),
    ("Gray Drinks", 6, "yes", None, None, ["nightlife"], []),
    ("Harper No Drinks", 7, "no", None, None, ["nightlife"], []),
    ("Indigo Smokes", 8, None, "yes", None, ["nightlife"], []),
    ("Jules No Smoke", 9, None, "no", None, [], []),
    ("Kai Party", 10, "yes", "yes", "maybe", ["nightlife", "friends"], []),
    ("Logan Sober Driver", 11, "no", "no", "yes", ["friends"], []),
    ("Morgan Meat Allergy", 12, "yes", "no", "yes", ["family"], ["meat"]),
    ("Noah Pork Allergy", 13, "yes", "no", "no", ["family"], ["pork"]),
    ("Oakley Multi Allergy", 14, "no", "no", "yes", ["family"], ["meat", "pork"]),
    ("Parker Nuts", 15, "yes", "no", "maybe", ["work"], ["tree nuts"]),
    ("Quinn Gluten", 16, "no", "no", "no", ["work"], ["gluten"]),
    ("Riley Dairy", 17, "yes", "yes", "yes", ["work"], ["dairy"]),
    (
        "Sam Everything Dietary",
        18,
        "no",
        "no",
        "no",
        ["family", "work"],
        ["meat", "pork", "gluten", "dairy"],
    ),
    ("Taylor Work", 19, "yes", "no", "yes", ["work"], []),
    ("Uri College", 20, "yes", "yes", "maybe", ["college"], []),
    ("Val Family", 21, "no", "no", "yes", ["family"], []),
    ("Wren Neighbors", 22, "yes", "no", "no", ["neighbors"], []),
    ("Xander Outdoors", 23, "no", "no", "yes", ["outdoors", "friends"], []),
    ("Yael Multi Tags", 24, "yes", "no", "yes", ["work", "college", "friends"], []),
    ("Zara Double-Barrel Name", 25, "yes", "no", "maybe", ["friends"], []),
    ("Avery O'Connor", 26, "no", "yes", "no", ["college"], []),
    ("Jordan 李", 27, "yes", "no", "yes", ["work"], []),
    ("Remy Full Stack", 28, "yes", "yes", "yes", ["friends", "nightlife"], ["dairy"]),
    ("Sage Needs Ride + Allergy", 29, "yes", "no", "no", ["friends"], ["gluten"]),
    ("Tess Maybe + Nuts", 30, "no", "yes", "maybe", ["outdoors"], ["tree nuts"]),
    ("Umber Sober + No Drive", 31, "no", "no", "no", ["family"], []),
    ("Vesper Driver + Meat", 32, "yes", "no", "yes", ["work", "friends"], ["meat"]),
    ("Wynn Blank Drive Drinks", 33, None, "yes", None, ["nightlife"], []),
    ("Yves Only Smokes Drive", 34, None, "yes", "yes", [], []),
    ("Zoe Quiet", 35, "no", "no", "maybe", ["neighbors"], ["dairy", "gluten"]),
]


def main() -> int:
    settings = get_settings()
    url = settings.database_url
    print(f"Database: {url.split('@')[-1] if '@' in url else url}")

    clerk_user_id = clerk_user_id_for_email(settings, EMAIL)
    print(f"Clerk user: {clerk_user_id}")

    db = SessionLocal()
    try:
        ensure_access_grant(db, EMAIL)
        workspace = ensure_workspace(db, clerk_user_id, EMAIL)
        print(
            f"Workspace: id={workspace.id} slug={workspace.slug!r} name={workspace.name!r}"
        )

        if os.environ.get("SEED_RESET") == "1":
            doomed = (
                db.query(Profile)
                .filter(
                    Profile.workspace_id == workspace.id,
                    Profile.phone.like(f"{SEED_PREFIX}%"),
                )
                .all()
            )
            for p in doomed:
                db.delete(p)
            db.commit()
            print(f"Removed {len(doomed)} prior seed contact(s) from this workspace.")

        tag_names = sorted({t for row in CONTACTS for t in row[5]})
        allergy_names = sorted({a for row in CONTACTS for a in row[6]})
        tags = {n: get_or_create_tag(db, workspace, n) for n in tag_names}
        allergies = {n: get_or_create_allergy(db, workspace, n) for n in allergy_names}
        db.flush()

        created = updated = 0
        for name, suffix, drinks, smokes, drive, tag_list, allergy_list in CONTACTS:
            phone = normalize_phone(f"{SEED_PREFIX}{suffix:03d}")
            if not is_valid_phone(phone):
                print(f"skip invalid phone for {name}: {phone}", file=sys.stderr)
                continue
            profile = (
                db.query(Profile)
                .filter(Profile.workspace_id == workspace.id, Profile.phone == phone)
                .one_or_none()
            )
            if profile is None:
                profile = Profile(
                    name=name,
                    phone=phone,
                    workspace_id=workspace.id,
                )
                db.add(profile)
                created += 1
            else:
                profile.name = name
                updated += 1
            profile.drinks = yn(drinks)
            profile.smokes = yn(smokes)
            profile.drive = drive_val(drive)
            profile.tags = [tags[t] for t in tag_list]
            profile.allergies = [allergies[a] for a in allergy_list]

        db.commit()
        print(
            f"Done for {EMAIL}: {created} created, {updated} updated, "
            f"{len(CONTACTS)} seed rows."
        )
        print(f"Tags: {', '.join(tag_names)}")
        print(f"Dietary: {', '.join(allergy_names)}")
        print(f"Phones: {SEED_PREFIX}000–{SEED_PREFIX}{len(CONTACTS) - 1:03d}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
PY
