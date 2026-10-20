"""Owner deadlines (B06): who set each action's deadline, and the email's deadline it replaced.

Revision ID: 20261020_0023
Revises: 20261016_0022
Create Date: 2026-10-20

``actions`` gains ``deadline_origin``, 'email' or 'owner' and NULL exactly when the action has
no deadline, and the ``stated_deadline_*`` columns: the email's deadline the owner last replaced
or cleared, stored as the deadline columns store one, with precision 'none' when there is none.
Every existing deadline came from an email, so each is marked 'email'. The table is rebuilt
for the CHECK constraints, which keeps its rows, constraints and cascades; Alembic's connection
leaves SQLite foreign keys off, so the rebuild does not cascade. The values below are this
revision's and stay fixed here even if the application's change later.

Downgrading drops the columns and keeps every task. A deadline the owner set stays an ordinary
dated deadline whose text is its date, which older versions read as they read an email's.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261020_0023"
down_revision: str | None = "20261016_0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CHECKS = {
    "ck_actions_deadline_origin_known": (
        "deadline_origin IS NULL OR deadline_origin IN ('email','owner')"
    ),
    "ck_actions_deadline_origin_set": "(deadline_origin IS NULL) = (deadline_precision = 'none')",
    "ck_actions_stated_deadline_precision_known": (
        "stated_deadline_precision IN ('none','unresolved','date','datetime')"
    ),
}
_COLUMNS = (
    "deadline_origin",
    "stated_deadline_text",
    "stated_deadline_precision",
    "stated_deadline_date",
    "stated_deadline_at_utc",
    "stated_deadline_timezone",
)


def upgrade() -> None:
    op.add_column("actions", sa.Column("deadline_origin", sa.String(8), nullable=True))
    op.add_column("actions", sa.Column("stated_deadline_text", sa.Text(), nullable=True))
    op.add_column(
        "actions",
        sa.Column(
            "stated_deadline_precision", sa.String(16), nullable=False, server_default="none"
        ),
    )
    op.add_column("actions", sa.Column("stated_deadline_date", sa.Date(), nullable=True))
    op.add_column(
        "actions", sa.Column("stated_deadline_at_utc", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("actions", sa.Column("stated_deadline_timezone", sa.String(128), nullable=True))
    op.execute("UPDATE actions SET deadline_origin = 'email' WHERE deadline_precision != 'none'")
    with op.batch_alter_table("actions", recreate="always") as batch:
        for name, condition in _CHECKS.items():
            batch.create_check_constraint(op.f(name), condition)


def downgrade() -> None:
    with op.batch_alter_table("actions", recreate="always") as batch:
        for name in _CHECKS:
            batch.drop_constraint(op.f(name), type_="check")
        for column in reversed(_COLUMNS):
            batch.drop_column(column)
