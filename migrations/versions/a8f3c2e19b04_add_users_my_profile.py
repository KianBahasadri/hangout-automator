"""add users (my profile)

Revision ID: a8f3c2e19b04
Revises: 7c4be1d90a52
Create Date: 2026-08-10 23:20:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a8f3c2e19b04"
down_revision: Union[str, Sequence[str], None] = "7c4be1d90a52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("clerk_user_id", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column("phone_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        # Boolean/int defaults are application-side (same pattern as hangouts.notify_*).
        sa.Column("default_notify_enabled", sa.Boolean(), nullable=False),
        sa.Column("default_notify_interval", sa.Boolean(), nullable=False),
        sa.Column("default_notify_threshold", sa.Boolean(), nullable=False),
        sa.Column("default_notify_interval_hours", sa.Integer(), nullable=False),
        sa.Column("default_notify_interval_only_if_changed", sa.Boolean(), nullable=False),
        sa.Column("default_notify_on_new_confirm", sa.Boolean(), nullable=False),
        sa.Column("default_notify_on_decline", sa.Boolean(), nullable=False),
        sa.Column("default_notify_on_allergy", sa.Boolean(), nullable=False),
        sa.Column("default_notify_on_ride_needed", sa.Boolean(), nullable=False),
        sa.Column("default_notify_confirm_goal", sa.Integer(), nullable=False),
        sa.Column("default_notify_threshold_cooldown_minutes", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("clerk_user_id"),
    )


def downgrade() -> None:
    op.drop_table("users")
