"""P1's account-free workflow, activity atomicity, restart and recovery acceptance."""

import asyncio
import json
import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from alembic import command
from sqlalchemy import func, select

from mailbrief.domain.actions import ActionActivityKind as Kind
from mailbrief.domain.actions import ActionEdit, ActionFilter, ActionStatus, StepEdit
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.services.actions import ActionConflictError, ActionService
from mailbrief.services.data import export_writing
from mailbrief.storage.actions import ActionRepository
from mailbrief.storage.backup import create_backup
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import migration_config, upgrade_database
from mailbrief.storage.recovery import restore_backup
from mailbrief.storage.tables import ActionActivityTable, ActionTable
from tests.unit.services.test_actions_service import Clock, seed

EDIT = ActionEdit(
    title="Plan the week",
    ownership=ActionOwnership.MINE,
    effort=None,
    estimate_minutes=90,
    target_date=date(2026, 10, 12),
    notes="Owner notes",
)


@pytest.mark.parametrize("email_derived", [False, True])
async def test_full_workflow_survives_restart_backup_restore_and_export(
    tmp_path: Path, email_derived: bool
) -> None:
    path = tmp_path / "original.sqlite3"
    await asyncio.to_thread(upgrade_database, path)
    database = Database.from_path(path)
    clock = Clock()
    async with database.session() as session:
        service = ActionService(session, clock=clock)
        if email_derived:
            seeded = await seed(session)
            created = await service.accept(seeded.suggestion_ids[0])
        else:
            created = await service.create(
                EDIT, [StepEdit(step_id=None, text="Make list", done=False)]
            )
            assert created.sources == ()
        steps = [StepEdit(step_id=None, text="Check calendar", done=True)]
        edited = await service.save(created.public_id, 1, EDIT, steps)
        clock.advance(hours=1)
        done = await service.complete(edited.public_id, 2)
        completion = done.completed_at_utc
        clock.advance(hours=1)
        opened = await service.reopen(done.public_id, 3)
        assert opened.completed_at_utc is None
        clock.advance(hours=1)
        undone = await service.undo_status(opened.public_id, 4)
        assert undone.completed_at_utc == completion  # Undo never fabricates another completion.
        assert undone.activity[-1].undo_of_revision == 4
        clock.advance(hours=1)
        final = await service.reopen(undone.public_id, 5)
        assert [event.kind for event in final.activity] == [
            Kind.CREATED,
            Kind.EDITED,
            Kind.COMPLETED,
            Kind.REOPENED,
            Kind.UNDO,
            Kind.REOPENED,
        ]
        assert final.activity[2].occurred_at_utc == completion
    await database.dispose()
    restarted = Database.from_path(path)
    async with restarted.session() as session:
        assert await ActionService(session).get(final.public_id) == final
    exported = tmp_path / "writing.json"
    await export_writing(restarted, exported)
    payload = json.loads(exported.read_text(encoding="utf-8"))
    assert [item["estimate_minutes"] for item in payload["records"]["actions"]] == [90]
    assert [item["kind"] for item in payload["records"]["action_activity"]] == [
        event.kind.value for event in final.activity
    ]
    await restarted.dispose()
    backup = tmp_path / "backup.zip"
    await asyncio.to_thread(create_backup, path, backup)
    restored_path = tmp_path / "restored.sqlite3"
    await asyncio.to_thread(restore_backup, backup, restored_path)
    recovered = Database.from_path(restored_path)
    try:
        async with recovered.session() as session:
            assert await ActionService(session).get(final.public_id) == final
    finally:
        await recovered.dispose()


async def test_failed_commit_rolls_back_task_and_activity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database.from_path(tmp_path / "atomic.sqlite3")
    await database.create_schema_for_tests()
    async with database.session() as session:
        monkeypatch.setattr(session, "commit", AsyncMock(side_effect=RuntimeError("disk full")))
        with pytest.raises(RuntimeError, match="disk full"):
            await ActionService(session).create(EDIT)
        assert await session.scalar(select(func.count()).select_from(ActionTable)) == 0
        assert await session.scalar(select(func.count()).select_from(ActionActivityTable)) == 0
    await database.dispose()


async def test_stale_undo_and_invalid_steps_write_no_history(tmp_path: Path) -> None:
    database = Database.from_path(tmp_path / "conflicts.sqlite3")
    await database.create_schema_for_tests()
    async with database.session() as session:
        service = ActionService(session)
        with pytest.raises(ActionConflictError):
            await service.create(EDIT, [StepEdit(step_id=123, text="Foreign step", done=False)])
        created = await service.create(EDIT)
        done = await service.complete(created.public_id, 1)
        await service.save(done.public_id, 2, EDIT)
        with pytest.raises(ActionConflictError):
            await service.undo_status(done.public_id, 2)
        current = await service.get(done.public_id)
        assert [event.kind for event in current.activity] == [
            Kind.CREATED,
            Kind.COMPLETED,
            Kind.EDITED,
        ]
        assert current.status is ActionStatus.COMPLETED
        with pytest.raises(ActionConflictError):
            await service.undo_status(done.public_id, 3)
    await database.dispose()


async def test_completion_undo_is_recorded_separately(tmp_path: Path) -> None:
    database = Database.from_path(tmp_path / "undo.sqlite3")
    await database.create_schema_for_tests()
    async with database.session() as session:
        service = ActionService(session)
        action = await service.create(EDIT)
        done = await service.complete(action.public_id, 1)
        undone = await service.undo_status(done.public_id, 2)
        assert undone.status is ActionStatus.OPEN
        assert undone.completed_at_utc is None
        assert undone.activity[-1].kind is Kind.UNDO
        assert undone.activity[-1].undo_of_revision == 2
        assert undone.activity[-1].previous_completed_at_utc == done.completed_at_utc
    await database.dispose()


async def test_manual_task_email_addition_can_be_undone_to_zero_sources(tmp_path: Path) -> None:
    database = Database.from_path(tmp_path / "sources.sqlite3")
    await database.create_schema_for_tests()
    async with database.session() as session:
        seeded = await seed(session)
        service = ActionService(session)
        manual = await service.create(EDIT)
        added = await service.accept_into(seeded.suggestion_ids[0], manual.public_id, 1)
        assert len(added.action.sources) == 1
        undone = await service.undo_accept_into(
            seeded.suggestion_ids[0],
            manual.public_id,
            2,
            added.source_added,
            previous=added.previous,
        )
        assert undone.sources == ()
        assert undone.activity[-1].kind is Kind.UNDO
        assert undone.activity[-1].undo_of_revision == 2
    await database.dispose()


async def test_failed_completion_commit_preserves_original_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database.from_path(tmp_path / "atomic-complete.sqlite3")
    await database.create_schema_for_tests()
    async with database.session() as session:
        service = ActionService(session)
        created = await service.create(EDIT)
        monkeypatch.setattr(session, "commit", AsyncMock(side_effect=RuntimeError("disk full")))
        with pytest.raises(RuntimeError):
            await service.complete(created.public_id, 1)
        assert await service.get(created.public_id) == created
    await database.dispose()


@pytest.mark.parametrize("completed", [False, True])
async def test_legacy_deleted_task_restores_without_undoing_completion(
    tmp_path: Path, completed: bool
) -> None:
    path = tmp_path / "legacy.sqlite3"
    await asyncio.to_thread(command.upgrade, migration_config(path), "20261011_0013")
    public_id = "11111111-1111-4111-8111-111111111111"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO actions(public_id,title,ownership,status,revision,created_at_utc,"
            "updated_at_utc,completed_at_utc,deleted_at_utc) VALUES(?,?,?,?,3,?,?,?,?)",
            (
                public_id,
                "Legacy task",
                "mine",
                "completed" if completed else "open",
                "2026-10-01 12:00:00",
                "2026-10-03 12:00:00",
                "2026-10-02 12:00:00" if completed else None,
                "2026-10-03 12:00:00",
            ),
        )
    await asyncio.to_thread(upgrade_database, path)
    database = Database.from_path(path)
    async with database.session() as session:
        restored = await ActionService(session).restore(public_id)
        assert restored.activity[-1].kind is Kind.RESTORED
        assert restored.activity[-1].undo_of_revision is None
        assert (restored.completed_at_utc is not None) == completed
        assert sum(event.kind is Kind.COMPLETED for event in restored.activity) == int(completed)
    await database.dispose()


@pytest.mark.parametrize("suggestion_restore", [False, True])
async def test_restore_only_undoes_a_known_deletion(
    tmp_path: Path, suggestion_restore: bool
) -> None:
    database = Database.from_path(tmp_path / "restore.sqlite3")
    await database.create_schema_for_tests()
    async with database.session() as session:
        seeded = await seed(session)
        service = ActionService(session)
        action = await service.accept(seeded.suggestion_ids[0])
        await service.delete(action.public_id, 1)
        if suggestion_restore:
            await service.restore_suggestion(seeded.suggestion_ids[0])
            restored = await service.get(action.public_id)
        else:
            restored = await service.restore(action.public_id)
        assert restored.activity[-1].kind is Kind.UNDO
        assert restored.activity[-1].undo_of_revision == 2
        await service.delete(action.public_id, 3)
        # A migration-era deletion has no reliable event to undo.
        row = await session.scalar(select(ActionTable))
        assert row is not None
        latest = await ActionRepository(session).latest_activity(row.id)
        assert latest is not None
        latest.legacy = True
        await session.commit()
        if suggestion_restore:
            await service.restore_suggestion(seeded.suggestion_ids[0])
            restored = await service.get(action.public_id)
        else:
            restored = await service.restore(action.public_id)
        assert restored.activity[-1].kind is Kind.RESTORED
        assert restored.activity[-1].undo_of_revision is None
    await database.dispose()


async def test_display_history_is_bounded_but_full_history_is_exported(tmp_path: Path) -> None:
    database = Database.from_path(tmp_path / "history.sqlite3")
    await database.create_schema_for_tests()
    async with database.session() as session:
        service = ActionService(session)
        action = await service.create(EDIT)
        for _ in range(25):
            action = await service.save(action.public_id, action.revision, EDIT)
        action = await service.complete(action.public_id, action.revision)
        undone = await service.undo_status(action.public_id, action.revision)
        assert len(undone.activity) == 20
        assert [event.revision for event in undone.activity] == list(range(9, 29))
        assert undone.activity[-1].undo_of_revision == 27
        row = await session.scalar(select(ActionTable))
        assert row is not None
        assert len(await ActionRepository(session).activity_rows(row.id)) == 28
        short = await service.create(EDIT)
        for _ in range(4):
            short = await service.save(short.public_id, short.revision, EDIT)
        listed = {item.public_id: item for item in await service.list_actions(ActionFilter.OPEN)}
        assert listed[undone.public_id].activity == undone.activity
        assert len(listed[short.public_id].activity) == 5
    exported = tmp_path / "history.json"
    await export_writing(database, exported)
    payload = json.loads(exported.read_text(encoding="utf-8"))
    assert len(payload["records"]["action_activity"]) == 33
    await database.dispose()


async def test_an_estimate_is_set_changed_and_cleared_one_revision_at_a_time(
    tmp_path: Path,
) -> None:
    database = Database.from_path(tmp_path / "estimate.sqlite3")
    await database.create_schema_for_tests()
    async with database.session() as session:
        service = ActionService(session)
        created = await service.create(EDIT)
        assert (created.estimate_minutes, created.revision) == (90, 1)
        changed = await service.save(
            created.public_id, 1, EDIT.model_copy(update={"estimate_minutes": 6_000})
        )
        assert (changed.estimate_minutes, changed.revision) == (6_000, 2)
        with pytest.raises(ActionConflictError):  # A stale screen never overwrites it.
            await service.save(
                created.public_id, 1, EDIT.model_copy(update={"estimate_minutes": 5})
            )
        cleared = await service.save(
            created.public_id, 2, EDIT.model_copy(update={"estimate_minutes": None})
        )
        assert (cleared.estimate_minutes, cleared.revision) == (None, 3)
        assert [event.kind for event in cleared.activity] == [
            Kind.CREATED,
            Kind.EDITED,
            Kind.EDITED,
        ]
        assert await service.get(created.public_id) == cleared
    await database.dispose()
