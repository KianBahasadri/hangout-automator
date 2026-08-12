"""add hangout location structure (place_id, lat, lng)

Revision ID: e4b2a1c90d11
Revises: c3e8f1a92b04
Create Date: 2026-08-12 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e4b2a1c90d11"
down_revision: Union[str, Sequence[str], None] = "c3e8f1a92b04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "hangouts",
        sa.Column("location_place_id", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "hangouts",
        sa.Column("location_latitude", sa.Float(), nullable=True),
    )
    op.add_column(
        "hangouts",
        sa.Column("location_longitude", sa.Float(), nullable=True),
    )
    # Existing hangouts.location text remains the display string; structure null.


def downgrade() -> None:
    op.drop_column("hangouts", "location_longitude")
    op.drop_column("hangouts", "location_latitude")
    op.drop_column("hangouts", "location_place_id")
