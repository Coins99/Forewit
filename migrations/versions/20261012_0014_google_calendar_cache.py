"""Cache the owner's Google calendars and their events for the Calendar page.

Revision ID: 20261012_0014
Revises: 20261011_0013
Create Date: 2026-10-12

Two new tables, so existing rows are untouched (ADR 0018, phase 2). They hold Google's data,
not the owner's: only calendars shown in Google Calendar, every sync replaces a calendar's
events and disconnecting removes them. Only what the page shows is kept; descriptions,
locations, guests and conference details are never stored. Downgrading drops both tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0014"
down_revision: str | None = "20261011_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TIMING_CHECK = (
    "(all_day = 1 AND start_date IS NOT NULL AND last_date IS NOT NULL"
    " AND last_date >= start_date AND starts_at_utc IS NULL AND ends_at_utc IS NULL)"
    " OR (all_day = 0 AND starts_at_utc IS NOT NULL AND ends_at_utc IS NOT NULL"
    " AND ends_at_utc >= starts_at_utc AND start_date IS NULL AND last_date IS NULL)"
)


def upgrade() -> None:
    op.create_table(
        "google_calendars",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("calendar_id", sa.String(length=1024), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("is_primary", sa.Boolean(), nullable=False),
        sa.Column("synced_from", sa.Date(), nullable=True),
        sa.Column("synced_until", sa.Date(), nullable=True),
        sa.Column("synced_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_google_calendars")),
        sa.UniqueConstraint("calendar_id", name="uq_google_calendars_calendar_id"),
    )
    op.create_table(
        "google_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("calendar_row_id", sa.Integer(), nullable=False),
        sa.Column("event_id", sa.String(length=1024), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("all_day", sa.Boolean(), nullable=False),
        sa.Column("starts_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("last_date", sa.Date(), nullable=True),
        sa.Column("web_link", sa.Text(), nullable=True),
        sa.Column("fetched_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_TIMING_CHECK, name=op.f("ck_google_events_timing_shape")),
        sa.ForeignKeyConstraint(
            ["calendar_row_id"],
            ["google_calendars.id"],
            name=op.f("fk_google_events_calendar_row_id_google_calendars"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_google_events")),
        sa.UniqueConstraint("calendar_row_id", "event_id", name="uq_google_events_calendar_row_id"),
    )
    op.create_index("ix_google_events_starts_at_utc", "google_events", ["starts_at_utc"])
    op.create_index("ix_google_events_start_date", "google_events", ["start_date"])


def downgrade() -> None:
    op.drop_index("ix_google_events_start_date", table_name="google_events")
    op.drop_index("ix_google_events_starts_at_utc", table_name="google_events")
    op.drop_table("google_events")
    op.drop_table("google_calendars")
