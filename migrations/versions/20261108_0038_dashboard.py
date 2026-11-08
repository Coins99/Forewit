"""Revisioned singleton dashboard and text-free retry receipts.

Revision ID: 20261108_0038
Revises: 20261108_0037
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261108_0038"
down_revision: str | None = "20261108_0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dashboards",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("layout_json", sa.JSON(), nullable=False),
        sa.CheckConstraint("id = 1", name="singleton"),
        sa.CheckConstraint("revision >= 1", name="revision_valid"),
    )
    op.create_table(
        "dashboard_commands",
        sa.Column("command_id", sa.String(36), primary_key=True),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("result_revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("length(command_id) = 36", name="command_id_length"),
        sa.CheckConstraint("result_revision >= 1", name="revision_valid"),
        sa.CheckConstraint(
            "length(input_hash) = 64 AND input_hash NOT GLOB '*[^0-9a-f]*'", name="hash_valid"
        ),
    )


def downgrade() -> None:
    op.drop_table("dashboard_commands")
    op.drop_table("dashboards")
