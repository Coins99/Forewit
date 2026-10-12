"""Resume full Inbox scans across work budgets without changing earlier migrations."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0017"
down_revision: str | None = "20261014_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sync_runs", sa.Column("inbox_continuation", sa.Text(), nullable=True))
    op.create_table(
        "inbox_scan_messages",
        sa.Column(
            "sync_run_id",
            sa.Integer(),
            sa.ForeignKey("sync_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "message_id",
            sa.Integer(),
            sa.ForeignKey("messages.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("inbox_scan_messages")
    op.drop_column("sync_runs", "inbox_continuation")
