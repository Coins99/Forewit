"""Separate local email analysis mode and message limit (P0-B2 Part 3).

Revision ID: 20261101_0025
Revises: 20261023_0024

Existing profiles retain cloud mode and its unchanged shortlist limit. Neither field
grants consent. Bounds are fixed to this migration rather than imported from live code.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261101_0025"
down_revision: str | None = "20261023_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("owner_preferences", recreate="always") as batch:
        batch.add_column(
            sa.Column("analysis_mode", sa.String(16), nullable=False, server_default="cloud")
        )
        batch.add_column(
            sa.Column(
                "local_message_limit", sa.Integer(), nullable=False, server_default=sa.text("20")
            )
        )
        batch.create_check_constraint(
            op.f("ck_owner_preferences_analysis_mode_known"), "analysis_mode IN ('cloud', 'local')"
        )
        batch.create_check_constraint(
            op.f("ck_owner_preferences_local_message_limit_range"),
            "local_message_limit BETWEEN 1 AND 20",
        )


def downgrade() -> None:
    with op.batch_alter_table("owner_preferences", recreate="always") as batch:
        batch.drop_constraint(op.f("ck_owner_preferences_local_message_limit_range"), type_="check")
        batch.drop_constraint(op.f("ck_owner_preferences_analysis_mode_known"), type_="check")
        batch.drop_column("local_message_limit")
        batch.drop_column("analysis_mode")
