"""AI task breakdown history (P5-2, ADR 0033): text-free records of each generation.

Revision ID: 20261114_0040
Revises: 20261114_0039
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261114_0040"
down_revision: str | None = "20261114_0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_breakdowns",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column("task_public_id", sa.String(36), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("parts_json", sa.JSON(), nullable=False),
        sa.Column("proposed_count", sa.Integer(), nullable=False),
        sa.Column("applied_count", sa.Integer(), nullable=False),
        sa.Column("edited_count", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("read_revision", sa.Integer(), nullable=False),
        sa.Column("result_revision", sa.Integer()),
        sa.Column("added_step_ids_json", sa.JSON()),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at_utc", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("public_id", name="uq_task_breakdowns_public_id"),
        sa.CheckConstraint(
            "length(public_id) = 36", name=op.f("ck_task_breakdowns_public_id_length")
        ),
        sa.CheckConstraint(
            "length(task_public_id) = 36", name=op.f("ck_task_breakdowns_task_id_length")
        ),
        sa.CheckConstraint(
            "outcome IN ('proposed','applied','dismissed','undone','declined','cancelled',"
            "'failed','no_steps')",
            name=op.f("ck_task_breakdowns_outcome_known"),
        ),
        sa.CheckConstraint(
            "proposed_count >= 0 AND applied_count >= 0 AND edited_count >= 0 "
            "AND applied_count <= proposed_count AND edited_count <= applied_count",
            name=op.f("ck_task_breakdowns_counts_valid"),
        ),
        sa.CheckConstraint(
            "schema_version >= 1", name=op.f("ck_task_breakdowns_schema_version_positive")
        ),
        sa.CheckConstraint(
            "read_revision >= 1", name=op.f("ck_task_breakdowns_read_revision_positive")
        ),
        sa.CheckConstraint(
            "result_revision IS NULL OR result_revision > read_revision",
            name=op.f("ck_task_breakdowns_result_revision_later"),
        ),
        sa.CheckConstraint(
            "(error_code IS NOT NULL) = (outcome = 'failed')",
            name=op.f("ck_task_breakdowns_error_code_for_failure"),
        ),
    )
    op.create_index("ix_task_breakdowns_task_public_id", "task_breakdowns", ["task_public_id"])


def downgrade() -> None:
    op.drop_index("ix_task_breakdowns_task_public_id", table_name="task_breakdowns")
    op.drop_table("task_breakdowns")
