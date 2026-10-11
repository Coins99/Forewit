"""P1's account-free workflow, activity atomicity, restart and recovery acceptance."""

import asyncio
import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from mailbrief.domain.actions import ActionActivityKind as Kind
from mailbrief.domain.actions import ActionEdit, ActionStatus, StepEdit
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.services.actions import ActionConflictError, ActionService
from mailbrief.services.data import export_writing
from mailbrief.storage.backup import create_backup
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import upgrade_database
from mailbrief.storage.recovery import restore_backup
from mailbrief.storage.tables import ActionActivityTable, ActionTable
from tests.unit.services.test_actions_service import Clock, seed

EDIT = ActionEdit(
    title="Plan the week",
    ownership=ActionOwnership.MINE,
    effort=None,
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
