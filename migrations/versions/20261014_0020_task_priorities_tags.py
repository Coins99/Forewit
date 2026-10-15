"""Task priorities and tags: the owner's own ways to order and find work without a deadline.

Revision ID: 20261014_0020
Revises: 20261014_0019
Create Date: 2026-10-14

``actions`` gains ``priority`` (high, medium or low), NULL (no priority) for every existing
task, so rows are kept. The table is rebuilt to add its CHECK constraint, which keeps its rows,
constraints and cascade; Alembic's connection leaves SQLite foreign keys off, so the rebuild
does not cascade. ``action_tags`` holds the owner's tags, one row per task and tag, removed
with its task; tasks share a tag when their ``name_key`` matches. The values and bounds below
are this revision's and stay fixed here even if the application's change later. Downgrading
drops the column and the tags and keeps every task.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261014_0020"
down_revision: str | None = "20261014_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PRIORITY_CHECK = "priority IS NULL OR priority IN ('high', 'medium', 'low')"
_PRIORITY_NAME = "ck_actions_priority_known"


def upgrade() -> None:
    with op.batch_alter_table("actions", recreate="always") as batch:
        batch.add_column(sa.Column("priority", sa.String(16), nullable=True))
        batch.create_check_constraint(op.f(_PRIORITY_NAME), _PRIORITY_CHECK)
    op.create_table(
        "action_tags",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "action_id",
            sa.Integer(),
            sa.ForeignKey("actions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_key", sa.Text(), nullable=False),
        sa.UniqueConstraint("action_id", "name_key", name="uq_action_tags_name"),
        sa.CheckConstraint(
            "length(name) BETWEEN 1 AND 40", name=op.f("ck_action_tags_name_length")
        ),
    )
    op.create_index("ix_action_tags_name_key", "action_tags", ["name_key"])


def downgrade() -> None:
    op.drop_index("ix_action_tags_name_key", table_name="action_tags")
    op.drop_table("action_tags")
    with op.batch_alter_table("actions", recreate="always") as batch:
        batch.drop_constraint(op.f(_PRIORITY_NAME), type_="check")
        batch.drop_column("priority")
