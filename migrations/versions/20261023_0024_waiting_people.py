"""Waiting and commitments (B08): who a task waits on or is for, and since when it waits.

Revision ID: 20261023_0024
Revises: 20261020_0023
Create Date: 2026-10-23

``actions`` gains ``person``, the owner's text naming who a task waits on or, for one of their
own, who it is for, NULL when unset; ``person_key``, its NFKC case-folded key, NULL exactly when
``person`` is, so the same person matches however it is typed; and ``waiting_since_utc``, set
exactly when the task waits for someone. Existing waiting tasks wait since they were created,
which is what earlier versions showed. ``action_series`` gains ``person``, so later repeats keep
it. Both tables are rebuilt for the CHECK constraints, which keeps their rows, constraints and
cascades; Alembic's connection leaves SQLite foreign keys off, so the rebuild does not cascade.
The bounds below are this revision's and stay fixed here even if the application's change later.

Downgrading drops the columns and keeps every task; who a task waits on is lost on the way down,
and upgrading again counts a waiting task's wait from its creation.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261023_0024"
down_revision: str | None = "20261020_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PERSON_LENGTH = "person IS NULL OR length(person) BETWEEN 1 AND 120"
_ACTION_CHECKS = {
    "ck_actions_person_length": _PERSON_LENGTH,
    "ck_actions_person_key_set": "(person IS NULL) = (person_key IS NULL)",
    "ck_actions_waiting_since_set": (
        "(ownership = 'waiting_for') = (waiting_since_utc IS NOT NULL)"
    ),
}
_SERIES_CHECK = "ck_action_series_person_length"
_INDEX = "ix_actions_person_key"


def upgrade() -> None:
    op.add_column("actions", sa.Column("person", sa.Text(), nullable=True))
    op.add_column("actions", sa.Column("person_key", sa.Text(), nullable=True))
    op.add_column(
        "actions", sa.Column("waiting_since_utc", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        "UPDATE actions SET waiting_since_utc = created_at_utc WHERE ownership = 'waiting_for'"
    )
    with op.batch_alter_table("actions", recreate="always") as batch:
        for name, condition in _ACTION_CHECKS.items():
            batch.create_check_constraint(op.f(name), condition)
    op.create_index(_INDEX, "actions", ["person_key"])
    with op.batch_alter_table("action_series", recreate="always") as batch:
        batch.add_column(sa.Column("person", sa.Text(), nullable=True))
        batch.create_check_constraint(op.f(_SERIES_CHECK), _PERSON_LENGTH)


def downgrade() -> None:
    with op.batch_alter_table("action_series", recreate="always") as batch:
        batch.drop_constraint(op.f(_SERIES_CHECK), type_="check")
        batch.drop_column("person")
    op.drop_index(_INDEX, table_name="actions")
    with op.batch_alter_table("actions", recreate="always") as batch:
        for name in _ACTION_CHECKS:
            batch.drop_constraint(op.f(name), type_="check")
        for column in ("waiting_since_utc", "person_key", "person"):
            batch.drop_column(column)
