"""Ranking signals: Gmail's categories, stars and bulk mail, important senders, wanted mail.

Revision ID: 20261013_0018
Revises: 20261012_0017
Create Date: 2026-10-13

Every change adds a column, so existing rows are kept (ADR 0020). ``messages`` gains Gmail's
category tab (NULL, unknown, until the message is next read), whether it is starred, whether
it came from a mailing list or as bulk mail, and when the owner said they wanted it in a
brief the automatic selection didn't give it. ``owner_preferences`` gains the owner's
important senders, none for a saved row. Downgrading drops them and keeps every row.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261013_0018"
down_revision: str | None = "20261012_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages", sa.Column("review_wanted_at_utc", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("messages", sa.Column("category", sa.String(length=16), nullable=True))
    op.add_column(
        "messages",
        sa.Column("is_starred", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "messages",
        sa.Column("is_bulk", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "owner_preferences",
        sa.Column(
            "important_senders_json", sa.JSON(), server_default=sa.text("'[]'"), nullable=False
        ),
    )


def downgrade() -> None:
    with op.batch_alter_table("owner_preferences") as batch:
        batch.drop_column("important_senders_json")
    with op.batch_alter_table("messages") as batch:
        batch.drop_column("is_bulk")
        batch.drop_column("is_starred")
        batch.drop_column("category")
        batch.drop_column("review_wanted_at_utc")
