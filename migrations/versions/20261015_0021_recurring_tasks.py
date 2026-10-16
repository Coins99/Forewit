"""Repeating tasks (B05): a series per repeating task, and its occurrences as ordinary tasks.

Revision ID: 20261015_0021
Revises: 20261014_0020
Create Date: 2026-10-15

``action_series`` holds each repeating task's rule (daily, weekly on chosen weekdays, monthly on
a day or on the last day, with an optional end date) and the template its later occurrences are
made from. ``actions`` gains ``series_id``, ``occurrence_date`` (the occurrence's own date, unique
in its series) and ``follows_action_id`` (the occurrence whose completion or skip made it), all
NULL for every existing task, and its status may now be ``skipped``. The table is rebuilt for the
constraints, which keeps its rows, constraints and cascades; Alembic's connection leaves SQLite
foreign keys off, so the rebuild does not cascade. The values below are this revision's and stay
fixed here even if the application's change later.

Downgrading keeps every task: a skipped occurrence becomes completed at its last change, since
older versions have no skip, and the series go.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261015_0021"
down_revision: str | None = "20261014_0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_NAME = "ck_actions_status_known"
_OLD_STATUS = "status IN ('open','completed')"
_NEW_STATUS = "status IN ('open','completed','skipped')"


def upgrade() -> None:
    op.create_table(
        "action_series",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("public_id", sa.String(36), nullable=False),
        sa.Column("frequency", sa.String(16), nullable=False),
        sa.Column("weekday_mask", sa.Integer(), nullable=False),
        sa.Column("month_day", sa.Integer(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column(
            "project_public_id",
            sa.String(36),
            sa.ForeignKey(
                "projects.public_id",
                ondelete="SET NULL",
                name=op.f("fk_action_series_project_public_id_projects"),
            ),
            nullable=True,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("ownership", sa.String(16), nullable=False),
        sa.Column("priority", sa.String(16), nullable=True),
        sa.Column("effort", sa.String(16), nullable=True),
        sa.Column("estimate_minutes", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("steps_json", sa.JSON(), nullable=False),
        sa.Column("tags_json", sa.JSON(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at_utc", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.UniqueConstraint("public_id", name=op.f("uq_action_series_public_id")),
        sa.CheckConstraint(
            "frequency IN ('daily', 'weekly', 'monthly_day', 'monthly_last')",
            name=op.f("ck_action_series_frequency_known"),
        ),
        sa.CheckConstraint(
            "CASE WHEN frequency = 'weekly' THEN weekday_mask BETWEEN 1 AND 127"
            " ELSE weekday_mask = 0 END",
            name=op.f("ck_action_series_weekdays_weekly"),
        ),
        sa.CheckConstraint(
            "(frequency = 'monthly_day') = (month_day IS NOT NULL)"
            " AND (month_day IS NULL OR month_day BETWEEN 1 AND 31)",
            name=op.f("ck_action_series_month_day_monthly"),
        ),
        sa.CheckConstraint(
            "ownership IN ('mine','waiting_for')", name=op.f("ck_action_series_ownership_known")
        ),
        sa.CheckConstraint(
            "priority IS NULL OR priority IN ('high', 'medium', 'low')",
            name=op.f("ck_action_series_priority_known"),
        ),
        sa.CheckConstraint(
            "estimate_minutes IS NULL OR estimate_minutes BETWEEN 1 AND 6000",
            name=op.f("ck_action_series_estimate_minutes_range"),
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_action_series_revision_positive")),
    )
    with op.batch_alter_table("actions", recreate="always") as batch:
        batch.add_column(sa.Column("series_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("occurrence_date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("follows_action_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_actions_series_id_action_series"),
            "action_series",
            ["series_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_foreign_key(
            op.f("fk_actions_follows_action_id_actions"),
            "actions",
            ["follows_action_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_unique_constraint(
            op.f("uq_actions_series_occurrence"), ["series_id", "occurrence_date"]
        )
        batch.drop_constraint(op.f(_STATUS_NAME), type_="check")
        batch.create_check_constraint(op.f(_STATUS_NAME), _NEW_STATUS)
    op.create_index("ix_actions_follows_action_id", "actions", ["follows_action_id"])


def downgrade() -> None:
    op.execute(
        "UPDATE actions SET status = 'completed', completed_at_utc = updated_at_utc"
        " WHERE status = 'skipped'"
    )
    op.drop_index("ix_actions_follows_action_id", table_name="actions")
    with op.batch_alter_table("actions", recreate="always") as batch:
        batch.drop_constraint(op.f(_STATUS_NAME), type_="check")
        batch.create_check_constraint(op.f(_STATUS_NAME), _OLD_STATUS)
        batch.drop_constraint(op.f("uq_actions_series_occurrence"), type_="unique")
        batch.drop_constraint(op.f("fk_actions_follows_action_id_actions"), type_="foreignkey")
        batch.drop_constraint(op.f("fk_actions_series_id_action_series"), type_="foreignkey")
        batch.drop_column("follows_action_id")
        batch.drop_column("occurrence_date")
        batch.drop_column("series_id")
    op.drop_table("action_series")
