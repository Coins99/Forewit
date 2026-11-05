"""Converge the two published P2 note migrations without changing either revision.

PRs 101 and 102 independently added the same foreign key after 0028. Alembic must
still record both ancestors of the merge revision, but must execute their shared
column operation only once. This adapter is limited to those two revision IDs.
"""

from collections.abc import Callable, Iterable
from functools import partial, update_wrapper

from alembic import op
from alembic.runtime.migration import MigrationContext, MigrationStep, RevisionStep
from sqlalchemy import inspect

_NOTES = "20261104_0029"
_WORKSPACE = "20261103_0029"
_CAPTURE = "20261103_0030"
_CHECK = "ck_drafts_project_only_for_notes"
_INDEX = "ix_drafts_project_public_id"

type MigrationPlan = Callable[[tuple[str, ...], MigrationContext], Iterable[MigrationStep]]


def _apply(step: RevisionStep, migration: MigrationContext) -> None:
    connection = migration.connection
    if connection is None or migration.as_sql:
        raise RuntimeError("The P2 migration convergence requires an online database connection.")
    revision = step.revision.revision
    inspector = inspect(connection)
    columns = {column["name"]: column for column in inspector.get_columns("drafts")}
    if step.is_upgrade:
        if "project_public_id" not in columns:
            original = step.revision.module.upgrade
            original()
            return
        # Never adopt an unrelated or incomplete column under a known revision ID.
        column = columns["project_public_id"]
        foreign_keys = inspector.get_foreign_keys("drafts")
        if (
            not column["nullable"]
            or getattr(column["type"], "length", None) != 36
            or not any(
                key["constrained_columns"] == ["project_public_id"]
                and key["referred_table"] == "projects"
                and key["referred_columns"] == ["public_id"]
                and key.get("options", {}).get("ondelete") == "SET NULL"
                for key in foreign_keys
            )
        ):
            raise RuntimeError("The existing note project column does not match the P2 schema.")
        if revision == _NOTES:
            checks = {check["name"] for check in inspector.get_check_constraints("drafts")}
            indexes = {index["name"] for index in inspector.get_indexes("drafts")}
            with op.batch_alter_table("drafts", recreate="always") as batch:
                if _INDEX not in indexes:
                    batch.create_index(_INDEX, ["project_public_id"])
                if _CHECK not in checks:
                    batch.create_check_constraint(
                        op.f(_CHECK), "kind = 'note' OR project_public_id IS NULL"
                    )
        return

    heads = set(migration.get_current_heads())
    other_active = bool(heads & {_WORKSPACE, _CAPTURE}) if revision == _NOTES else _NOTES in heads
    if not other_active:
        step.revision.module.downgrade()
    elif revision == _NOTES:
        # The other branch still owns the column; remove only #101's additions.
        with op.batch_alter_table("drafts", recreate="always") as batch:
            batch.drop_constraint(op.f(_CHECK), type_="check")
            batch.drop_index(_INDEX)


def compatible_plan(plan: MigrationPlan) -> MigrationPlan:
    """Wrap the environment's migration planner, leaving all other steps untouched."""

    def steps(heads: tuple[str, ...], migration: MigrationContext) -> Iterable[MigrationStep]:
        for step in plan(heads, migration):
            if isinstance(step, RevisionStep) and step.revision.revision in {_NOTES, _WORKSPACE}:
                step.migration_fn = update_wrapper(
                    partial(_apply, step, migration), step.migration_fn
                )
            yield step

    return steps
