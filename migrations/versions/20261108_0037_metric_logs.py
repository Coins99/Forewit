"""Exact owner metric definitions, entries, correction history and retry receipts.

Revision ID: 20261108_0037
Revises: 20261106_0036
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261108_0037"
down_revision: str | None = "20261106_0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "metric_definitions",
        sa.Column("public_id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("unit", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("scale", sa.Integer(), nullable=False),
        sa.Column("allow_negative", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(public_id) = 36", name="public_id_length"),
        sa.CheckConstraint("length(name) BETWEEN 1 AND 120", name="name_length"),
        sa.CheckConstraint("length(unit) BETWEEN 1 AND 32", name="unit_length"),
        sa.CheckConstraint("kind IN ('amount','point_value')", name="kind_known"),
        sa.CheckConstraint("scale BETWEEN 0 AND 6", name="scale_valid"),
        sa.CheckConstraint("version = 1 AND revision >= 1", name="versions_valid"),
        sa.CheckConstraint("allow_negative IN (0,1) AND archived IN (0,1)", name="flags_valid"),
    )
    op.create_table(
        "metric_entries",
        sa.Column("public_id", sa.String(36), primary_key=True),
        sa.Column(
            "definition_id",
            sa.String(36),
            sa.ForeignKey("metric_definitions.public_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("value_scaled", sa.Integer(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("zone", sa.String(64), nullable=False),
        sa.Column(
            "project_public_id",
            sa.String(36),
            sa.ForeignKey("projects.public_id", ondelete="RESTRICT"),
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at_utc", sa.DateTime(timezone=True)),
        sa.CheckConstraint("length(public_id) = 36", name="public_id_length"),
        sa.CheckConstraint("definition_version = 1 AND revision >= 1", name="versions_valid"),
        sa.CheckConstraint(
            "typeof(value_scaled) = 'integer' AND "
            "value_scaled BETWEEN -1000000000000000 AND 1000000000000000",
            name="value_valid",
        ),
    )
    op.create_index(
        "ix_metric_entries_definition_date",
        "metric_entries",
        ["definition_id", "local_date", "public_id"],
    )
    op.create_table(
        "metric_entry_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "entry_id",
            sa.String(36),
            sa.ForeignKey("metric_entries.public_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("state_json", sa.JSON(), nullable=False),
        sa.Column("corrects_revision", sa.Integer()),
        sa.Column("recorded_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("entry_id", "revision", name="uq_metric_entry_changes_revision"),
        sa.CheckConstraint("revision >= 1", name="revision_valid"),
        sa.CheckConstraint(
            "operation IN ('create','edit','delete','restore','undo')", name="operation_known"
        ),
        sa.CheckConstraint(
            "corrects_revision IS NULL OR "
            "(corrects_revision >= 1 AND corrects_revision < revision)",
            name="correction_valid",
        ),
    )
    op.create_table(
        "metric_commands",
        sa.Column("command_id", sa.String(36), primary_key=True),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("result_kind", sa.String(16), nullable=False),
        sa.Column("result_public_id", sa.String(36), nullable=False),
        sa.Column("result_revision", sa.Integer(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(command_id) = 36", name="command_id_length"),
        sa.CheckConstraint(
            "length(result_public_id) = 36 AND result_revision >= 1", name="result_valid"
        ),
        sa.CheckConstraint("result_kind IN ('definition','entry')", name="result_kind_known"),
        sa.CheckConstraint(
            "operation IN ('define','configure','create','edit','delete','restore','undo')",
            name="operation_known",
        ),
        sa.CheckConstraint(
            "length(input_hash) = 64 AND input_hash NOT GLOB '*[^0-9a-f]*'", name="hash_valid"
        ),
    )


def downgrade() -> None:
    for table in (
        "metric_commands",
        "metric_entry_changes",
        "metric_entries",
        "metric_definitions",
    ):
        op.drop_table(table)
