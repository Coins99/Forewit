"""Dated hours, independent buffers, block locks and manual command receipts.

Revision ID: 20261106_0036
Revises: 20261106_0035
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261106_0036"
down_revision: str | None = "20261106_0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "work_blocks", sa.Column("locked", sa.Boolean(), nullable=False, server_default=sa.false())
    )
    for name in ("buffer_before_minutes", "buffer_after_minutes"):
        op.add_column("working_hours", sa.Column(name, sa.Integer()))
        op.execute(sa.text(f"UPDATE working_hours SET {name} = buffer_minutes"))
    op.add_column(
        "working_hours",
        sa.Column("overrides_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
    )
    op.create_table(
        "block_commands",
        sa.Column("command_id", sa.String(36), primary_key=True),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("result_public_id", sa.String(36), nullable=False),
        sa.Column("result_revision", sa.Integer(), nullable=False),
        sa.Column("result_deleted", sa.Boolean(), nullable=False),
        sa.Column("previous", sa.JSON()),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("block_commands")
    with op.batch_alter_table("working_hours") as batch:
        batch.drop_column("overrides_json")
        batch.drop_column("buffer_after_minutes")
        batch.drop_column("buffer_before_minutes")
    with op.batch_alter_table("work_blocks") as batch:
        batch.drop_column("locked")
