"""Retry-safe manual task creation receipts.

Revision ID: 20261101_0026
Revises: 20261101_0025

Receipts deliberately outlive task purge, contain no owner text and have no FK.
Downgrade preserves tasks but removes command retry protection.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261101_0026"
down_revision: str | None = "20261101_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_commands",
        sa.Column("command_id", sa.String(36), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("result_public_id", sa.String(36), nullable=False),
        sa.Column("result_revision", sa.Integer(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("command_id", name=op.f("pk_task_commands")),
        sa.CheckConstraint(
            "length(command_id) = 36", name=op.f("ck_task_commands_command_id_length")
        ),
        sa.CheckConstraint(
            "operation = 'task.create'", name=op.f("ck_task_commands_operation_known")
        ),
        sa.CheckConstraint(
            "length(input_hash) = 64 AND input_hash NOT GLOB '*[^0-9a-f]*'",
            name=op.f("ck_task_commands_input_hash_valid"),
        ),
        sa.CheckConstraint(
            "result_revision >= 1", name=op.f("ck_task_commands_result_revision_positive")
        ),
    )


def downgrade() -> None:
    op.drop_table("task_commands")
