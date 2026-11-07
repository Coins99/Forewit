"""Retry-safe add-only proposal application and batch Undo.

Revision ID: 20261106_0035
Revises: 20261106_0034
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261106_0035"
down_revision: str | None = "20261106_0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planning_commands",
        sa.Column("command_id", sa.String(36), primary_key=True),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("results", sa.JSON(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(command_id) = 36", name="command_id_length"),
        sa.CheckConstraint("operation IN ('apply','undo')", name="operation_known"),
        sa.CheckConstraint(
            "length(input_hash) = 64 AND input_hash NOT GLOB '*[^0-9a-f]*'", name="input_hash_valid"
        ),
    )


def downgrade() -> None:
    op.drop_table("planning_commands")
