"""P1 task activity. Existing rows retain only known creation/current completion facts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261013_0015"
down_revision: str | None = "20261012_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "action_activity",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "action_id",
            sa.Integer(),
            sa.ForeignKey("actions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("occurred_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_status", sa.String(16), nullable=True),
        sa.Column("previous_completed_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("undo_of_revision", sa.Integer(), nullable=True),
        sa.Column("previous_source_count", sa.Integer(), nullable=True),
        sa.Column("legacy", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("action_id", "revision", name="uq_action_activity_revision"),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_action_activity_revision_positive")),
    )
    op.create_index("ix_action_activity_action_id", "action_activity", ["action_id"])
    op.execute(
        sa.text(
            "INSERT INTO action_activity(action_id, revision, kind, occurred_at_utc, legacy) "
            "SELECT id, 1, 'created', created_at_utc, 1 FROM actions"
        )
    )
    # Do not invent erased completions or the intermediate revisions of legacy tasks.
    op.execute(
        sa.text(
            "INSERT INTO action_activity(action_id, revision, kind, occurred_at_utc, legacy) "
            "SELECT id, revision, 'completed', completed_at_utc, 1 FROM actions "
            "WHERE completed_at_utc IS NOT NULL AND revision > 1"
        )
    )


def downgrade() -> None:
    op.drop_table("action_activity")
