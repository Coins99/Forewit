"""Uncertain create retries preserve one owner task, including across recovery and purge."""

import asyncio
import json
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import Action, DeadlineChange, StepEdit
from mailbrief.domain.recurrence import RepeatFrequency, RepeatRule
from mailbrief.services.actions import ActionCreateConflictError, ActionService
from mailbrief.services.data import export_writing
from mailbrief.storage.actions import ActionRepository
from mailbrief.storage.backup import create_backup
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import upgrade_database
from mailbrief.storage.recovery import restore_backup
from mailbrief.storage.tables import (
    ActionActivityTable,
    ActionSeriesTable,
    ActionTable,
    TaskCommandTable,
)
from tests.unit.services.test_manual_tasks import EDIT

COMMAND = "12345678-1234-4234-8234-123456789abc"
STEPS = (StepEdit(step_id=None, text="Private checklist", done=False),)


async def test_same_command_returns_one_task_without_extra_activity(session: AsyncSession) -> None:
    service = ActionService(session)
    first = await service.create(EDIT, STEPS, command_id=COMMAND)
    assert await service.create(EDIT, STEPS, command_id=COMMAND.upper()) == first
    assert await session.scalar(select(func.count()).select_from(ActionTable)) == 1
    assert await session.scalar(select(func.count()).select_from(ActionActivityTable)) == 1
    receipt = await session.get(TaskCommandTable, COMMAND)
    assert receipt is not None
    assert (receipt.result_public_id, receipt.result_revision) == (first.public_id, 1)
    assert len(receipt.input_hash) == 64
    assert set(receipt.__table__.columns.keys()) == {
        "command_id",
        "operation",
        "input_hash",
        "result_public_id",
        "result_revision",
        "created_at_utc",
    }


async def test_legacy_clients_still_create_separate_tasks(session: AsyncSession) -> None:
    service = ActionService(session)
    assert (await service.create(EDIT)).public_id != (await service.create(EDIT)).public_id
    assert await session.scalar(select(func.count()).select_from(TaskCommandTable)) == 0


async def test_commit_succeeded_but_response_failed_then_retry(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = session.commit

    async def lost_response() -> None:
        await commit()
        raise RuntimeError("Synthetic response lost after commit")

    service = ActionService(session)
    with monkeypatch.context() as patch:
        patch.setattr(session, "commit", lost_response)
        with pytest.raises(RuntimeError, match="response lost"):
            await service.create(EDIT, STEPS, command_id=COMMAND)
    retried = await service.create(EDIT, STEPS, command_id=COMMAND)
    assert retried.title == EDIT.title
    assert len(retried.steps) == 1
    assert await session.scalar(select(func.count()).select_from(ActionTable)) == 1
    assert await session.scalar(select(func.count()).select_from(ActionActivityTable)) == 1


async def test_failed_commit_rolls_back_receipt_with_owner_records(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ActionService(session)
    with monkeypatch.context() as patch:
        patch.setattr(session, "commit", AsyncMock(side_effect=RuntimeError("Synthetic disk full")))
        with pytest.raises(RuntimeError):
            await service.create(EDIT, STEPS, command_id=COMMAND)
    for table in (ActionTable, ActionActivityTable, TaskCommandTable):
        assert await session.scalar(select(func.count()).select_from(table)) == 0
    changed = EDIT.model_copy(update={"notes": "Changed after a definite rollback"})
    assert (await service.create(changed, command_id=COMMAND)).notes == changed.notes


@pytest.mark.parametrize("part", ["title", "notes", "steps", "repeat", "deadline"])
async def test_committed_command_cannot_be_reused_with_different_input(
    session: AsyncSession, part: str
) -> None:
    service = ActionService(session)
    first = await service.create(EDIT, command_id=COMMAND)
    edit = (
        EDIT.model_copy(update={part: "Different private input"})
        if part in {"title", "notes"}
        else EDIT
    )
    steps = STEPS if part == "steps" else ()
    repeat = RepeatRule(frequency=RepeatFrequency.DAILY) if part == "repeat" else None
    deadline = DeadlineChange.to(date(2026, 10, 12)) if part == "deadline" else None
    with pytest.raises(ActionCreateConflictError, match="different input"):
        await service.create(edit, steps, repeat, deadline, command_id=COMMAND)
    assert await service.get(first.public_id) == first
    assert await session.scalar(select(func.count()).select_from(ActionTable)) == 1


async def test_replay_preserves_owner_edits_from_another_session(database: Database) -> None:
    async with database.session() as original:
        service = ActionService(original)
        first = await service.create(EDIT, command_id=COMMAND)
        # Keep an ORM row alive to expose stale identity-map reads.
        cached = await ActionRepository(original).get_action(first.public_id)
        assert cached is not None
        async with database.session() as other:
            edited = await ActionService(other).save(
                first.public_id, 1, EDIT.model_copy(update={"notes": "Owner changed this later"})
            )
        assert await service.create(EDIT, command_id=COMMAND) == edited
        assert (await service.history(first.public_id)).complete


@pytest.mark.parametrize("purge", [False, True])
async def test_replay_never_restores_or_recreates_removed_task(
    session: AsyncSession, purge: bool
) -> None:
    service = ActionService(session)
    first = await service.create(EDIT, command_id=COMMAND)
    await service.delete(first.public_id, first.revision)
    if purge:
        row = await ActionRepository(session).get_action(first.public_id, include_deleted=True)
        assert row is not None
        await session.delete(row)
        await session.commit()
    with pytest.raises(ActionCreateConflictError, match="removed"):
        await service.create(EDIT, command_id=COMMAND)
    assert await session.scalar(select(func.count()).select_from(TaskCommandTable)) == 1
    assert await session.scalar(select(func.count()).select_from(ActionTable)) == (
        0 if purge else 1
    )


@pytest.mark.parametrize("repeating", [False, True])
async def test_concurrent_same_command_has_one_effective_task(
    database: Database, monkeypatch: pytest.MonkeyPatch, repeating: bool
) -> None:
    lookup = ActionService._created_command
    both_read = asyncio.Event()
    reads = 0

    async def simultaneous(self: ActionService, command: str, fingerprint: str) -> Action | None:
        nonlocal reads
        found = await lookup(self, command, fingerprint)
        reads += 1
        if reads <= 2:
            if reads == 2:
                both_read.set()
            await asyncio.wait_for(both_read.wait(), timeout=10)
        return found

    monkeypatch.setattr(ActionService, "_created_command", simultaneous)

    async def create() -> Action:
        async with database.session() as session:
            repeat = RepeatRule(frequency=RepeatFrequency.DAILY) if repeating else None
            return await ActionService(session).create(EDIT, STEPS, repeat, command_id=COMMAND)

    first, second = await asyncio.gather(create(), create())
    assert first == second
    async with database.session() as session:
        for table in (ActionTable, ActionActivityTable, TaskCommandTable):
            assert await session.scalar(select(func.count()).select_from(table)) == 1
        assert await session.scalar(select(func.count()).select_from(ActionSeriesTable)) == int(
            repeating
        )


@pytest.mark.parametrize("command", [None, COMMAND])
async def test_unrelated_integrity_failure_is_not_hidden(
    session: AsyncSession, command: str | None
) -> None:
    first = await ActionService(session).create(EDIT)
    service = ActionService(session, id_factory=lambda: first.public_id)
    with pytest.raises(IntegrityError):
        await service.create(EDIT, command_id=command)
    assert await session.scalar(select(func.count()).select_from(ActionTable)) == 1
    assert await session.scalar(select(func.count()).select_from(TaskCommandTable)) == 0


async def test_invalid_command_is_refused_before_writing(session: AsyncSession) -> None:
    with pytest.raises(ValueError):
        await ActionService(session).create(EDIT, command_id="not-a-command")
    assert await session.scalar(select(func.count()).select_from(ActionTable)) == 0


async def test_existing_receipt_never_hides_an_unrelated_commit_failure(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ActionService(session)
    await service.create(EDIT, command_id=COMMAND)
    failure = IntegrityError("Synthetic unrelated statement", {}, RuntimeError("constraint failed"))
    monkeypatch.setattr(session, "commit", AsyncMock(side_effect=failure))
    with pytest.raises(IntegrityError):
        await service.create(EDIT, command_id=COMMAND)


async def test_receipt_survives_restart_backup_restore_but_is_not_writing(tmp_path: Path) -> None:
    path = tmp_path / "owner.sqlite3"
    await asyncio.to_thread(upgrade_database, path)
    db = Database.from_path(path)
    async with db.session() as session:
        first = await ActionService(session).create(EDIT, STEPS, command_id=COMMAND)
    export = tmp_path / "writing.json"
    await export_writing(db, export)
    assert "task_commands" not in json.loads(export.read_text("utf-8"))["records"]
    await db.dispose()
    backup, restored = tmp_path / "backup.zip", tmp_path / "restored.sqlite3"
    await asyncio.to_thread(create_backup, path, backup)
    await asyncio.to_thread(restore_backup, backup, restored)
    for source in (path, restored):
        opened = Database.from_path(source)
        try:
            async with opened.session() as session:
                assert await ActionService(session).create(EDIT, STEPS, command_id=COMMAND) == first
                assert await session.scalar(select(func.count()).select_from(ActionTable)) == 1
                # A new command still means a genuinely new task.
                assert (
                    await ActionService(session).create(EDIT, command_id=str(uuid4()))
                ).public_id != first.public_id
        finally:
            await opened.dispose()
