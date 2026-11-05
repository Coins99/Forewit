"""Optional canonical project context for owner notes.

Revision ID: 20261103_0029
Revises: 20261103_0028
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261103_0029"
down_revision: str | None = "20261103_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("drafts") as batch:
        batch.add_column(sa.Column("project_public_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_drafts_project_public_id_projects",
            "projects",
            ["project_public_id"],
            ["public_id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("drafts") as batch:
        batch.drop_column("project_public_id")
