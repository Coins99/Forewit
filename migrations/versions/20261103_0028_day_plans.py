"""Persistent daily selection, independent of task dates and account lifecycle.

Revision ID: 20261103_0028
Revises: 20261103_0027
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261103_0028"
down_revision: str | None = "20261103_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "day_plans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("creation_zone", sa.String(128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("public_id", name="uq_day_plans_public_id"),
        sa.UniqueConstraint("local_date", name="uq_day_plans_local_date"),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_day_plans_revision_positive")),
    )
    op.create_table(
        "day_plan_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column("plan_id", sa.Integer(), nullable=False),
        sa.Column("task_public_id", sa.String(36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["plan_id"], ["day_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["task_public_id"], ["actions.public_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("public_id", name="uq_day_plan_items_public_id"),
        sa.UniqueConstraint("plan_id", "task_public_id", name="uq_day_plan_items_plan_task"),
        sa.UniqueConstraint("plan_id", "position", name="uq_day_plan_items_plan_position"),
        sa.CheckConstraint("position >= 0", name=op.f("ck_day_plan_items_position_nonnegative")),
    )
    op.create_index("ix_day_plan_items_task_public_id", "day_plan_items", ["task_public_id"])


def downgrade() -> None:
    op.drop_index("ix_day_plan_items_task_public_id", "day_plan_items")
    op.drop_table("day_plan_items")
    op.drop_table("day_plans")
