"""An optional project link on the owner's notes (ADR 0027), keeping every draft as it is.

Revision ID: 20261104_0029
Revises: 20261103_0028
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261104_0029"
down_revision: str | None = "20261103_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Only a note belongs to a project (ADR 0027); the services keep it so, and so does SQLite.
_NOTES_ONLY = "kind = 'note' OR project_public_id IS NULL"


def upgrade() -> None:
    # Alembic migration connections keep foreign keys off while rebuilding parent tables,
    # so rebuilding drafts leaves its versions and sources where they are.
    with op.batch_alter_table("drafts", recreate="always") as batch:
        batch.add_column(sa.Column("project_public_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_drafts_project_public_id_projects",
            "projects",
            ["project_public_id"],
            ["public_id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_drafts_project_public_id", ["project_public_id"])
        batch.create_check_constraint(op.f("ck_drafts_project_only_for_notes"), _NOTES_ONLY)


def downgrade() -> None:
    with op.batch_alter_table("drafts", recreate="always") as batch:
        batch.drop_constraint(op.f("ck_drafts_project_only_for_notes"), type_="check")
        batch.drop_index("ix_drafts_project_public_id")
        batch.drop_constraint("fk_drafts_project_public_id_projects", type_="foreignkey")
        batch.drop_column("project_public_id")
