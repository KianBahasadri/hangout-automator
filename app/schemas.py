from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models import Drive, HangoutStatus, InviteStatus, YesNo


class TagOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class AllergyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str


class AllergyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    phone: str = Field(min_length=5, max_length=32)
    drinks: Optional[YesNo] = None
    smokes: Optional[YesNo] = None
    drive: Optional[Drive] = None
    tag_ids: list[int] = Field(default_factory=list)
    allergy_ids: list[int] = Field(default_factory=list)


class ProfileUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=120)
    phone: Optional[str] = Field(default=None, min_length=5, max_length=32)
    drinks: Optional[YesNo] = None
    smokes: Optional[YesNo] = None
    drive: Optional[Drive] = None
    tag_ids: Optional[list[int]] = None
    allergy_ids: Optional[list[int]] = None


class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    phone: str
    drinks: Optional[YesNo]
    smokes: Optional[YesNo]
    drive: Optional[Drive]
    tags: list[TagOut] = Field(default_factory=list)
    allergies: list[AllergyOut] = Field(default_factory=list)
    created_at: datetime


class HangoutCreate(BaseModel):
    day_date: Optional[str] = None
    time: Optional[str] = None
    duration: Optional[str] = None
    motive: Optional[str] = None
    alcohol_involved: Optional[YesNo] = None
    weed_involved: Optional[YesNo] = None
    notes: Optional[str] = None
    profile_ids: list[int] = Field(default_factory=list)
    organizer_profile_id: Optional[int] = None
    notify_enabled: bool = False
    notify_interval: bool = False
    notify_threshold: bool = False
    notify_interval_hours: int = Field(default=6, ge=1, le=168)
    notify_interval_only_if_changed: bool = True
    notify_on_new_confirm: bool = True
    notify_on_decline: bool = False
    notify_on_allergy: bool = True
    notify_on_ride_needed: bool = True
    notify_confirm_goal: int = Field(default=0, ge=0, le=100)
    notify_threshold_cooldown_minutes: int = Field(default=0, ge=0, le=1440)


class HangoutUpdate(BaseModel):
    day_date: Optional[str] = None
    time: Optional[str] = None
    duration: Optional[str] = None
    motive: Optional[str] = None
    alcohol_involved: Optional[YesNo] = None
    weed_involved: Optional[YesNo] = None
    notes: Optional[str] = None
    organizer_profile_id: Optional[int] = None
    notify_enabled: Optional[bool] = None
    notify_interval: Optional[bool] = None
    notify_threshold: Optional[bool] = None
    notify_interval_hours: Optional[int] = Field(default=None, ge=1, le=168)
    notify_interval_only_if_changed: Optional[bool] = None
    notify_on_new_confirm: Optional[bool] = None
    notify_on_decline: Optional[bool] = None
    notify_on_allergy: Optional[bool] = None
    notify_on_ride_needed: Optional[bool] = None
    notify_confirm_goal: Optional[int] = Field(default=None, ge=0, le=100)
    notify_threshold_cooldown_minutes: Optional[int] = Field(default=None, ge=0, le=1440)


class InviteOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hangout_id: int
    profile_id: int
    status: InviteStatus
    followups_sent: int
    last_outbound_at: Optional[datetime]
    responded_at: Optional[datetime]
    profile: ProfileOut


class HangoutOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    day_date: Optional[str]
    time: Optional[str]
    duration: Optional[str]
    motive: Optional[str]
    alcohol_involved: Optional[YesNo]
    weed_involved: Optional[YesNo]
    notes: Optional[str]
    status: HangoutStatus
    organizer_profile_id: Optional[int]
    organizer_phone: Optional[str]
    notify_enabled: bool
    notify_interval: bool
    notify_threshold: bool
    notify_interval_hours: int
    notify_interval_only_if_changed: bool
    notify_on_new_confirm: bool
    notify_on_decline: bool
    notify_on_allergy: bool
    notify_on_ride_needed: bool
    notify_confirm_goal: int
    notify_confirm_goal_sent: bool
    notify_threshold_cooldown_minutes: int
    activated_at: Optional[datetime]
    created_at: datetime
    invites: list[InviteOut] = Field(default_factory=list)


class AppSettingsOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    organizer_phone: Optional[str]


class AppSettingsUpdate(BaseModel):
    organizer_phone: Optional[str] = None


class SetupHangoutRequest(BaseModel):
    profile_ids: list[int] = Field(default_factory=list)
