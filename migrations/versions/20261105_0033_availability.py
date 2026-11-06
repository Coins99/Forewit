"""Busy or free events, and the owner's working hours (ADR 0029).

Revision ID: 20261105_0033
Revises: 20261105_0032

``calendar_events`` and ``google_events`` gain ``busy``. Existing rows keep what they meant
before: a timed event is busy and an all-day one free, the owner's default rule, and what
P3-1 counted for Google's events until the next sync reads Google's own "Show as". A new
single-row ``working_hours`` table holds the weekly hours and buffer; no row means none were
set. The buffer's choices are this revision's and stay fixed here. Downgrading drops the
table and the columns and keeps every event.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261105_0033"
down_revision: str | None = "20261105_0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BUFFER_CHECK = "buffer_minutes IN (0, 5, 10, 15, 30)"


def upgrade() -> None:
    for table in ("calendar_events", "google_events"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column("busy", sa.Boolean(), nullable=False, server_default=sa.true())
            )
        op.execute(sa.text(f"UPDATE {table} SET busy = 0 WHERE all_day = 1"))
    op.create_table(
        "working_hours",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("windows_json", sa.JSON(), nullable=False),
        sa.Column("buffer_minutes", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_working_hours_single_row")),
        sa.CheckConstraint(_BUFFER_CHECK, name=op.f("ck_working_hours_buffer_minutes_known")),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_working_hours_revision_positive")),
    )


def downgrade() -> None:
    op.drop_table("working_hours")
    for table in ("google_events", "calendar_events"):
        with op.batch_alter_table(table, recreate="always") as batch:
            batch.drop_column("busy")
