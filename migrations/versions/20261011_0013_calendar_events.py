"""Add the owner's calendar events.

Revision ID: 20261011_0013
Revises: 20260930_0012
Create Date: 2026-10-11

The change is a new table, so existing rows are untouched (ADR 0018). Events have no
account foreign key: they belong to the owner. A timed event keeps two UTC instants and the
zone it was entered in; an all-day event keeps its first and last local days, and the
CHECK below keeps the two shapes apart. It is this revision's own copy and stays fixed here
even if the application's rules change later. Downgrading drops the table and every event.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261011_0013"
down_revision: str | None = "20260930_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMING_CHECK = (
    "(all_day = 1 AND start_date IS NOT NULL AND last_date IS NOT NULL"
    " AND last_date >= start_date AND starts_at_utc IS NULL AND ends_at_utc IS NULL)"
    " OR (all_day = 0 AND starts_at_utc IS NOT NULL AND ends_at_utc IS NOT NULL"
    " AND ends_at_utc > starts_at_utc AND start_date IS NULL AND last_date IS NULL)"
)


def upgrade() -> None:
    op.create_table(
        "calendar_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("public_id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), server_default="", nullable=False),
        sa.Column("all_day", sa.Boolean(), nullable=False),
        sa.Column("starts_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("last_date", sa.Date(), nullable=True),
        sa.Column("time_zone", sa.String(length=64), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.CheckConstraint(_TIMING_CHECK, name=op.f("ck_calendar_events_timing_shape")),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_calendar_events_revision_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_calendar_events")),
        sa.UniqueConstraint("public_id", name="uq_calendar_events_public_id"),
    )
    op.create_index("ix_calendar_events_starts_at_utc", "calendar_events", ["starts_at_utc"])
    op.create_index("ix_calendar_events_start_date", "calendar_events", ["start_date"])


def downgrade() -> None:
    op.drop_index("ix_calendar_events_start_date", table_name="calendar_events")
    op.drop_index("ix_calendar_events_starts_at_utc", table_name="calendar_events")
    op.drop_table("calendar_events")
