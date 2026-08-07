from __future__ import annotations

import enum
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    TypeDecorator,
    func,
)
from sqlalchemy import inspect as sqla_inspect
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class YesNo(str, enum.Enum):
    yes = "yes"
    no = "no"


class Drive(str, enum.Enum):
    yes = "yes"
    no = "no"
    maybe = "maybe"


def _optional_enum_column(enum_cls: type[enum.Enum], *, legacy: dict[str, str] | None = None):
    """Store enum values as strings; blank/unknown/legacy map to NULL in Python."""

    class OptionalEnum(TypeDecorator):
        impl = String(16)
        cache_ok = True

        def process_bind_param(self, value, dialect):  # type: ignore[no-untyped-def]
            if value is None:
                return None
            if isinstance(value, enum_cls):
                return value.value
            text = str(value).strip()
            if text in ("", "unknown"):
                return None
            if legacy and text in legacy:
                text = legacy[text]
            return enum_cls(text).value

        def process_result_value(self, value, dialect):  # type: ignore[no-untyped-def]
            if value in (None, "", "unknown"):
                return None
            text = str(value)
            if legacy and text in legacy:
                text = legacy[text]
            try:
                return enum_cls(text)
            except ValueError:
                return None

    return OptionalEnum()


def not_null_columns(model: type, names: Iterable[str]) -> list[str]:
    """Which of `names` map to a NOT NULL column, so callers can reject `null` early."""
    columns = sqla_inspect(model).columns
    return [name for name in names if name in columns and not columns[name].nullable]


class HangoutStatus(str, enum.Enum):
    draft = "draft"
    active = "active"
    closed = "closed"


class InviteStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    remind = "remind"
    declined = "declined"
    no_response = "no_response"
    failed_send = "failed_send"


class MessageDirection(str, enum.Enum):
    outbound = "outbound"
    inbound = "inbound"


profile_tags = Table(
    "profile_tags",
    Base.metadata,
    Column("profile_id", ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)

profile_allergies = Table(
    "profile_allergies",
    Base.metadata,
    Column("profile_id", ForeignKey("profiles.id", ondelete="CASCADE"), primary_key=True),
    Column("allergy_id", ForeignKey("allergies.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    profiles: Mapped[list[Profile]] = relationship(
        secondary=profile_tags, back_populates="tags"
    )


class Allergy(Base):
    """Catalog of dietary-restriction options; managed in Settings."""

    __tablename__ = "allergies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    profiles: Mapped[list[Profile]] = relationship(
        secondary=profile_allergies, back_populates="allergies"
    )


class Profile(Base):
    __tablename__ = "profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    drinks: Mapped[YesNo | None] = mapped_column(_optional_enum_column(YesNo), nullable=True)
    smokes: Mapped[YesNo | None] = mapped_column(_optional_enum_column(YesNo), nullable=True)
    # Legacy free-text column; kept for SQLite migration. Prefer allergies relationship.
    food_allergies: Mapped[str | None] = mapped_column(Text, nullable=True)
    drive: Mapped[Drive | None] = mapped_column(
        _optional_enum_column(
            Drive, legacy={"can_drive": "yes", "cannot": "no", "can": "yes"}
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    tags: Mapped[list[Tag]] = relationship(
        secondary=profile_tags, back_populates="profiles"
    )
    allergies: Mapped[list[Allergy]] = relationship(
        secondary=profile_allergies, back_populates="profiles"
    )
    # Deleting a profile leaves the database to cascade its invite rows away
    # (and to NULL the message_logs pointing at them, keeping the SMS history).
    # Without passive_deletes the ORM would instead try to NULL the invites'
    # profile_id, which the NOT NULL column rejects.
    invites: Mapped[list[HangoutInvite]] = relationship(
        back_populates="profile", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def food_allergies_label(self) -> str | None:
        if self.allergies:
            return ", ".join(a.name for a in sorted(self.allergies, key=lambda x: x.name.lower()))
        # Fall back to legacy free-text until migrated
        if self.food_allergies and self.food_allergies.strip():
            return self.food_allergies.strip()
        return None

    @property
    def has_allergies(self) -> bool:
        return bool(self.allergies) or bool(self.food_allergies and self.food_allergies.strip())


class Hangout(Base):
    __tablename__ = "hangouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    day_date: Mapped[str | None] = mapped_column(String(64), nullable=True)
    time: Mapped[str | None] = mapped_column(String(64), nullable=True)
    duration: Mapped[str | None] = mapped_column(String(64), nullable=True)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)
    motive: Mapped[str | None] = mapped_column(String(255), nullable=True)
    alcohol_involved: Mapped[YesNo | None] = mapped_column(
        _optional_enum_column(YesNo), nullable=True
    )
    weed_involved: Mapped[YesNo | None] = mapped_column(
        _optional_enum_column(YesNo), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[HangoutStatus] = mapped_column(
        Enum(HangoutStatus, values_callable=lambda x: [e.value for e in x]),
        default=HangoutStatus.draft,
        nullable=False,
    )

    # Optional per-hangout organizer SMS settings
    organizer_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("profiles.id", ondelete="SET NULL"), nullable=True
    )
    organizer_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notify_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notify_interval: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notify_threshold: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Interval digest customization
    notify_interval_hours: Mapped[int] = mapped_column(Integer, default=6, nullable=False)
    notify_interval_only_if_changed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_digest_fingerprint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Threshold alert customization
    notify_on_new_confirm: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_on_decline: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notify_on_allergy: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_on_ride_needed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    notify_confirm_goal: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    notify_confirm_goal_sent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notify_threshold_cooldown_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_organizer_notify_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Soft-delete: set when hidden from the main list (closed hangouts only in UI).
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organizer: Mapped[Profile | None] = relationship(foreign_keys=[organizer_profile_id])
    invites: Mapped[list[HangoutInvite]] = relationship(
        back_populates="hangout", cascade="all, delete-orphan"
    )


class HangoutInvite(Base):
    __tablename__ = "hangout_invites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    hangout_id: Mapped[int] = mapped_column(ForeignKey("hangouts.id", ondelete="CASCADE"), nullable=False)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[InviteStatus] = mapped_column(
        Enum(InviteStatus, values_callable=lambda x: [e.value for e in x]),
        default=InviteStatus.pending,
        nullable=False,
    )
    followups_sent: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_outbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    hangout: Mapped[Hangout] = relationship(back_populates="invites")
    profile: Mapped[Profile] = relationship(back_populates="invites")
    messages: Mapped[list[MessageLog]] = relationship(
        back_populates="invite", cascade="all, delete-orphan"
    )


class MessageLog(Base):
    __tablename__ = "message_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invite_id: Mapped[int | None] = mapped_column(
        ForeignKey("hangout_invites.id", ondelete="SET NULL"), nullable=True
    )
    hangout_id: Mapped[int | None] = mapped_column(
        ForeignKey("hangouts.id", ondelete="SET NULL"), nullable=True
    )
    direction: Mapped[MessageDirection] = mapped_column(
        Enum(MessageDirection, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    phone: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    invite: Mapped[HangoutInvite | None] = relationship(back_populates="messages")
