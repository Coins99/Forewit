"""Explicit optional capture review state; existing records remain out of the inbox.

Revision ID: 20261103_0030
Revises: 20261103_0029
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261103_0030"
down_revision: str | None = "20261103_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("actions", "drafts"):
        op.add_column(
            table,
            sa.Column("capture_origin", sa.String(16), nullable=False, server_default="legacy"),
        )
        with op.batch_alter_table(table) as batch:
            batch.create_check_constraint(
                op.f(f"ck_{table}_capture_origin_known"),
                "capture_origin IN ('legacy','direct','quick_capture')",
            )
        op.add_column(
            table, sa.Column("capture_processed_at_utc", sa.DateTime(timezone=True), nullable=True)
        )


def downgrade() -> None:
    for table in ("actions", "drafts"):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(op.f(f"ck_{table}_capture_origin_known"), type_="check")
            batch.drop_column("capture_processed_at_utc")
            batch.drop_column("capture_origin")
