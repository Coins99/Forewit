"""Separate successful Google coverage from attempts; never promote old checked data.

Revision ID: 20261106_0034
Revises: 20261105_0033
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261106_0034"
down_revision: str | None = "20261105_0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for name in ("successful_from", "successful_until"):
        op.add_column("google_calendars", sa.Column(name, sa.Date()))
    op.add_column("google_calendars", sa.Column("successful_at_utc", sa.DateTime(timezone=True)))
    op.add_column("google_calendars", sa.Column("successful_zone", sa.String(64)))
    op.add_column(
        "google_calendars",
        sa.Column("read_failed", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "google_calendar_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("generation", sa.String(36), nullable=False),
        sa.Column("list_succeeded", sa.Boolean(), nullable=False),
        sa.Column("listed_at_utc", sa.DateTime(timezone=True)),
        sa.Column("first_day", sa.Date()),
        sa.Column("last_day", sa.Date()),
        sa.Column("time_zone", sa.String(64)),
        sa.CheckConstraint("id = 1", name="singleton"),
        sa.CheckConstraint("list_succeeded IN (0,1)", name="list_succeeded_boolean"),
    )


def downgrade() -> None:
    op.drop_table("google_calendar_state")
    for name in (
        "read_failed",
        "successful_zone",
        "successful_at_utc",
        "successful_until",
        "successful_from",
    ):
        op.drop_column("google_calendars", name)
