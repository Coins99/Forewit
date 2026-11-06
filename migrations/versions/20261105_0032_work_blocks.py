"""Work blocks: the owner's planned time for one task (ADR 0028).

Revision ID: 20261105_0032
Revises: 20261105_0031
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261105_0032"
down_revision: str | None = "20261105_0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "work_blocks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column("action_id", sa.Integer(), nullable=False),
        sa.Column("starts_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("time_zone", sa.String(64), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.ForeignKeyConstraint(["action_id"], ["actions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("public_id", name="uq_work_blocks_public_id"),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_work_blocks_revision_positive")),
        sa.CheckConstraint(
            "ends_at_utc > starts_at_utc", name=op.f("ck_work_blocks_ends_after_start")
        ),
    )
    op.create_index("ix_work_blocks_starts_at_utc", "work_blocks", ["starts_at_utc"])
    op.create_index("ix_work_blocks_action_id", "work_blocks", ["action_id"])


def downgrade() -> None:
    op.drop_index("ix_work_blocks_action_id", "work_blocks")
    op.drop_index("ix_work_blocks_starts_at_utc", "work_blocks")
    op.drop_table("work_blocks")
