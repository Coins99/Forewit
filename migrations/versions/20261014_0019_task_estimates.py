"""Task estimates: the owner's own estimate of a task's length, in whole minutes.

Revision ID: 20261014_0019
Revises: 20261013_0018
Create Date: 2026-10-14

``actions`` gains ``estimate_minutes``, NULL (no estimate) for every existing task, so rows are
kept. It is entered by the owner only and is never derived from the AI's coarse effort. The
table is rebuilt to add its CHECK constraint, which keeps its rows, constraints and cascade;
Alembic's connection leaves SQLite foreign keys off, so the rebuild does not cascade. The
bound below is this revision's and stays fixed here even if the application's limit changes
later. Downgrading drops the column and keeps every row.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261014_0019"
down_revision: str | None = "20261013_0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ESTIMATE_CHECK = "estimate_minutes IS NULL OR estimate_minutes BETWEEN 1 AND 6000"
_ESTIMATE_NAME = "ck_actions_estimate_minutes_range"


def upgrade() -> None:
    with op.batch_alter_table("actions", recreate="always") as batch:
        batch.add_column(sa.Column("estimate_minutes", sa.Integer(), nullable=True))
        batch.create_check_constraint(op.f(_ESTIMATE_NAME), _ESTIMATE_CHECK)


def downgrade() -> None:
    with op.batch_alter_table("actions", recreate="always") as batch:
        batch.drop_constraint(op.f(_ESTIMATE_NAME), type_="check")
        batch.drop_column("estimate_minutes")
