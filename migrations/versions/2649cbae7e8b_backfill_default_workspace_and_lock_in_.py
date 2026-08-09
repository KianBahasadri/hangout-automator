"""backfill default workspace and lock in tenancy

Revision ID: 2649cbae7e8b
Revises: d1a0ea50b82e
Create Date: 2026-08-08 20:09:58.336266

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "2649cbae7e8b"
down_revision: Union[str, Sequence[str], None] = "d1a0ea50b82e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# All pre-tenancy rows belong to the seeded default workspace.
_DEFAULT_WORKSPACE = "SELECT id FROM workspaces WHERE slug = 'default'"


def upgrade() -> None:
    """Backfill every existing row into the default workspace and lock it in."""
    op.execute("INSERT INTO workspaces (name, slug) VALUES ('Default', 'default')")

    for table in (
        "tags",
        "allergies",
        "profiles",
        "hangouts",
        "hangout_invites",
        "message_logs",
    ):
        op.execute(f"UPDATE {table} SET workspace_id = ({_DEFAULT_WORKSPACE})")

    # NOT NULL everywhere except message_logs: inbound SMS rows with no matched
    # invite legitimately have no workspace.
    for table in ("tags", "allergies", "profiles", "hangouts", "hangout_invites"):
        op.alter_column(table, "workspace_id", nullable=False)

    # Global uniqueness would stop two workspaces from sharing a person or a
    # tag name; make the constraints composite.
    op.drop_constraint("profiles_phone_key", "profiles", type_="unique")
    op.create_unique_constraint(
        "profiles_workspace_id_phone_key", "profiles", ["workspace_id", "phone"]
    )
    op.drop_constraint("tags_name_key", "tags", type_="unique")
    op.create_unique_constraint("tags_workspace_id_name_key", "tags", ["workspace_id", "name"])
    op.drop_constraint("allergies_name_key", "allergies", type_="unique")
    op.create_unique_constraint(
        "allergies_workspace_id_name_key", "allergies", ["workspace_id", "name"]
    )


def downgrade() -> None:
    """Reverse the tenancy lock-in: restore global uniqueness, drop NOT NULL,
    and remove the default workspace (FK CASCADE nulls workspace_id and drops
    members)."""
    op.drop_constraint("profiles_workspace_id_phone_key", "profiles", type_="unique")
    op.create_unique_constraint("profiles_phone_key", "profiles", ["phone"])
    op.drop_constraint("tags_workspace_id_name_key", "tags", type_="unique")
    op.create_unique_constraint("tags_name_key", "tags", ["name"])
    op.drop_constraint("allergies_workspace_id_name_key", "allergies", type_="unique")
    op.create_unique_constraint("allergies_name_key", "allergies", ["name"])

    for table in ("tags", "allergies", "profiles", "hangouts", "hangout_invites"):
        op.alter_column(table, "workspace_id", nullable=True)

    op.execute("DELETE FROM workspaces WHERE slug = 'default'")
