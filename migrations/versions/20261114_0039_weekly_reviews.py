"""Weekly review records (P5-1): text-free marks that the owner reviewed a week.

Revision ID: 20261114_0039
Revises: 20261108_0038
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261114_0039"
down_revision: str | None = "20261108_0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "weekly_reviews",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("reviewed_at_utc", sa.DateTime(timezone=True)),
        sa.Column("reflection_note_public_id", sa.String(36)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("week_start", name="uq_weekly_reviews_week_start"),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_weekly_reviews_revision_positive")),
        sa.CheckConstraint(
            "reflection_note_public_id IS NULL OR length(reflection_note_public_id) = 36",
            name=op.f("ck_weekly_reviews_note_id_length"),
        ),
    )


def downgrade() -> None:
    op.drop_table("weekly_reviews")
