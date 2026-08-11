"""add sms opt outs (permanent DNC)

Revision ID: c3e8f1a92b04
Revises: a8f3c2e19b04
Create Date: 2026-08-11 07:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c3e8f1a92b04"
down_revision: Union[str, Sequence[str], None] = "a8f3c2e19b04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "sms_opt_outs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("phone", sa.String(length=32), nullable=False),
        sa.Column(
            "opted_out_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=120), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("phone"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("sms_opt_outs")
