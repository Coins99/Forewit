"""Optional owner project/area organization, preserving all work and existing activity."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261014_0016"
down_revision: str | None = "20261013_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table, extra in (
        ("life_areas", [sa.Column("deleted_at_utc", sa.DateTime(timezone=True))]),
        (
            "projects",
            [
                sa.Column("archived_at_utc", sa.DateTime(timezone=True)),
                sa.Column(
                    "area_public_id",
                    sa.String(36),
                    sa.ForeignKey("life_areas.public_id", ondelete="SET NULL"),
                ),
            ],
        ),
    ):
        op.create_table(
            table,
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("public_id", sa.String(36), nullable=False),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("notes", sa.Text(), nullable=False),
            sa.Column("revision", sa.Integer(), nullable=False),
            sa.Column("created_at_utc", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at_utc", sa.DateTime(timezone=True), nullable=False),
            *extra,
            sa.UniqueConstraint("public_id", name=f"uq_{table}_public_id"),
            sa.CheckConstraint("revision >= 1", name=op.f(f"ck_{table}_revision_positive")),
        )
    # Alembic migration connections keep foreign keys off while rebuilding parent tables.
    with op.batch_alter_table("actions", recreate="always") as batch:
        batch.add_column(sa.Column("project_public_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_actions_project_public_id_projects",
            "projects",
            ["project_public_id"],
            ["public_id"],
            ondelete="SET NULL",
        )
    op.add_column("action_activity", sa.Column("project_public_id", sa.String(36), nullable=True))
    op.add_column("action_activity", sa.Column("area_public_id", sa.String(36), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("action_activity") as batch:
        batch.drop_column("area_public_id")
        batch.drop_column("project_public_id")
    with op.batch_alter_table("actions", recreate="always") as batch:
        batch.drop_constraint("fk_actions_project_public_id_projects", type_="foreignkey")
        batch.drop_column("project_public_id")
    op.drop_table("projects")
    op.drop_table("life_areas")
