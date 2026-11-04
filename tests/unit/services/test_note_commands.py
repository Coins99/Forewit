"""One effective note/version across uncertain create retries, cleanup and recovery."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.drafts import Draft, DraftEdit
from mailbrief.services.data import (
    CleanupKind,
    CleanupRequest,
    apply_cleanup,
    export_writing,
    preview_cleanup,
)
from mailbrief.services.drafts import DraftService, NoteCreateConflictError
from mailbrief.storage.backup import create_backup
from mailbrief.storage.database import Database
from mailbrief.storage.drafts import DraftRepository
from mailbrief.storage.migrate import upgrade_database
from mailbrief.storage.recovery import restore_backup
from mailbrief.storage.tables import DraftTable, DraftVersionTable, NoteCommandTable

COMMAND = "12345678-1234-4234-8234-123456789abc"
CONTENT = DraftEdit(title="  Private note  ", body="Owner text\nSecond line  ")


async def test_initial_text_and_first_version_commit_once(session: AsyncSession) -> None:
    service = DraftService(session)
    first = await service.create_note(CONTENT, command_id=COMMAND)
    assert await service.create_note(CONTENT, command_id=COMMAND.upper()) == first
    assert first.content() == CONTENT
    versions = await service.versions(first.public_id)
    assert len(versions) == 1
    for table in (DraftTable, DraftVersionTable, NoteCommandTable):
        assert await session.scalar(select(func.count()).select_from(table)) == 1
    receipt = await session.get(NoteCommandTable, COMMAND)
    assert receipt is not None and receipt.result_public_id == first.public_id
    assert set(receipt.__table__.columns.keys()) == {
        "command_id",
        "operation",
        "input_hash",
        "result_public_id",
        "result_revision",
        "created_at_utc",
    }


async def test_lost_response_after_commit_reuses_one_note(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    commit = session.commit

    async def lost() -> None:
        await commit()
        raise RuntimeError("Synthetic response lost")

    service = DraftService(session)
    with monkeypatch.context() as patch:
        patch.setattr(session, "commit", lost)
        with pytest.raises(RuntimeError):
            await service.create_note(CONTENT, command_id=COMMAND)
    assert (await service.create_note(CONTENT, command_id=COMMAND)).content() == CONTENT
    for table in (DraftTable, DraftVersionTable, NoteCommandTable):
        assert await session.scalar(select(func.count()).select_from(table)) == 1


async def test_failed_write_rolls_back_all_three_records(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    with monkeypatch.context() as patch:
        patch.setattr(session, "commit", AsyncMock(side_effect=RuntimeError("Synthetic disk full")))
        with pytest.raises(RuntimeError):
            await DraftService(session).create_note(CONTENT, command_id=COMMAND)
    for table in (DraftTable, DraftVersionTable, NoteCommandTable):
        assert await session.scalar(select(func.count()).select_from(table)) == 0
    assert (await DraftService(session).create_note(CONTENT, command_id=COMMAND)).revision == 1


@pytest.mark.parametrize("changed", [DraftEdit(title="Different"), DraftEdit(body="Changed")])
async def test_changed_input_is_refused(session: AsyncSession, changed: DraftEdit) -> None:
    service = DraftService(session)
    await service.create_note(CONTENT, command_id=COMMAND)
    with pytest.raises(NoteCreateConflictError, match="different input"):
        await service.create_note(changed, command_id=COMMAND)


@pytest.mark.parametrize(
    "content",
    [
        DraftEdit(),
        DraftEdit(title=" "),
        DraftEdit(body="\n"),
        DraftEdit(body="Text", to_text="owner@example.com"),
    ],
)
async def test_invalid_note_input_leaves_no_receipt(
    session: AsyncSession, content: DraftEdit
) -> None:
    with pytest.raises(ValueError):
        await DraftService(session).create_note(content, command_id=COMMAND)
    assert await session.scalar(select(func.count()).select_from(NoteCommandTable)) == 0


async def test_replay_loads_later_owner_edits_from_another_session(database: Database) -> None:
    async with database.session() as original:
        service = DraftService(original)
        first = await service.create_note(CONTENT, command_id=COMMAND)
        cached = await DraftRepository(original).get_draft(first.public_id)
        assert cached is not None
        async with database.session() as other:
            updated = await DraftService(other).autosave(
                first.public_id, first.revision, DraftEdit(body="Later owner writing")
            )
        assert await service.create_note(CONTENT, command_id=COMMAND) == updated


@pytest.mark.parametrize("purge", [False, True])
async def test_removed_note_is_never_recreated(session: AsyncSession, purge: bool) -> None:
    service = DraftService(session)
    first = await service.create_note(CONTENT, command_id=COMMAND)
    await service.delete(first.public_id, first.revision)
    if purge:
        row = await DraftRepository(session).get_draft(first.public_id, include_deleted=True)
        assert row is not None
        await session.delete(row)
        await session.commit()
    with pytest.raises(NoteCreateConflictError, match="removed"):
        await service.create_note(CONTENT, command_id=COMMAND)
    assert await session.scalar(select(func.count()).select_from(NoteCommandTable)) == 1


async def test_concurrent_same_command_has_one_note_and_version(
    database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    lookup = DraftService._created_note
    both_read = asyncio.Event()
    reads = 0

    async def simultaneous(self: DraftService, command: str, fingerprint: str) -> Draft | None:
        nonlocal reads
        previous = await lookup(self, command, fingerprint)
        reads += 1
        if reads <= 2:
            if reads == 2:
                both_read.set()
            await asyncio.wait_for(both_read.wait(), timeout=10)
        return previous

    monkeypatch.setattr(DraftService, "_created_note", simultaneous)

    async def create() -> Draft:
        async with database.session() as session:
            return await DraftService(session).create_note(CONTENT, command_id=COMMAND)

    first, second = await asyncio.gather(create(), create())
    assert first == second
    async with database.session() as session:
        for table in (DraftTable, DraftVersionTable, NoteCommandTable):
            assert await session.scalar(select(func.count()).select_from(table)) == 1


async def test_unrelated_integrity_failure_is_not_hidden(session: AsyncSession) -> None:
    first = await DraftService(session).create_note(CONTENT, command_id=COMMAND)
    service = DraftService(session, id_factory=lambda: first.public_id)
    with pytest.raises(IntegrityError):
        await service.create_note(CONTENT, command_id="11111111-1111-4111-8111-111111111111")


async def test_backup_restore_export_and_previewed_purge(tmp_path: Path) -> None:
    path = tmp_path / "owner.sqlite3"
    await asyncio.to_thread(upgrade_database, path)
    database = Database.from_path(path)
    async with database.session() as session:
        first = await DraftService(session).create_note(CONTENT, command_id=COMMAND)
    export = tmp_path / "writing.json"
    await export_writing(database, export)
    records = json.loads(export.read_text("utf-8"))["records"]
    assert "note_commands" not in records
    assert records["drafts"][0]["body"] == CONTENT.body
    archive = tmp_path / "backup.zip"
    await database.dispose()
    await asyncio.to_thread(create_backup, path, archive)
    restored_path = tmp_path / "restored.sqlite3"
    await asyncio.to_thread(restore_backup, archive, restored_path)
    restored = Database.from_path(restored_path)
    try:
        async with restored.session() as session:
            service = DraftService(session)
            assert await service.create_note(CONTENT, command_id=COMMAND) == first
            await service.delete(first.public_id, first.revision)
        request = CleanupRequest(CleanupKind.DELETED, before=datetime.now(UTC) + timedelta(days=1))
        preview = await preview_cleanup(restored, request)
        await apply_cleanup(restored, preview)
        async with restored.session() as session:
            assert await session.scalar(select(func.count()).select_from(NoteCommandTable)) == 1
            with pytest.raises(NoteCreateConflictError, match="removed"):
                await DraftService(session).create_note(CONTENT, command_id=COMMAND)
    finally:
        await restored.dispose()
