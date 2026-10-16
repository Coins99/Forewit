"""Prepare repeat activity for older applications when downgrading past repeating tasks.

Revision ID: 20261016_0022
Revises: 20261015_0021
Create Date: 2026-10-16

0021 has already run on owner databases and must remain immutable. This follow-up has no
schema change: its downgrade normalizes the new activity values before 0021's downgrade
converts skipped tasks to completed and removes recurrence. Upgrade to this revision before
downgrading an existing 0021 database to 0020.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20261016_0022"
down_revision: str | None = "20261015_0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    # A reopening (or its undo) can refer to a skipped previous status. Older models
    # require a completion timestamp whenever that status becomes completed. Resolve
    # the original skip time before converting the activity kinds below.
    op.execute(
        "UPDATE action_activity SET previous_status = 'completed',"
        " previous_completed_at_utc = COALESCE("
        " (SELECT prior.occurred_at_utc FROM action_activity AS prior"
        " WHERE prior.action_id = action_activity.action_id"
        " AND prior.revision < action_activity.revision AND prior.kind = 'skipped'"
        " ORDER BY prior.revision DESC LIMIT 1), occurred_at_utc)"
        " WHERE previous_status = 'skipped'"
    )
    op.execute("UPDATE action_activity SET kind = 'completed' WHERE kind = 'skipped'")
    op.execute("UPDATE action_activity SET kind = 'edited' WHERE kind = 'tag_respelled'")
