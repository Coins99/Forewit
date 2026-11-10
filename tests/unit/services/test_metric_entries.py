"""Atomic exact numeric logs, corrections, retry, purge and separate-profile recovery."""

import asyncio
import json
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.metric_entries import MeasurementKind, MetricDefinitionSpec, MetricEntryInput
from mailbrief.services.data import (
    CleanupKind,
    CleanupRequest,
    apply_cleanup,
    export_writing,
    preview_cleanup,
)
from mailbrief.services.metric_entries import (
    MetricConflictError,
    MetricEntryService,
    MetricNotFoundError,
)
from mailbrief.storage.backup import create_backup
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import migration_config, upgrade_database
from mailbrief.storage.recovery import BackupValidationError, restore_backup, validate_snapshot
from mailbrief.storage.tables import (
    MetricCommandTable,
    MetricDefinitionTable,
    MetricEntryChangeTable,
    MetricEntryTable,
)
from tests.factories import Clock

NOW = datetime(2026, 11, 8, 15, tzinfo=UTC)
SPEC = MetricDefinitionSpec(name="Reading", unit="minutes", kind=MeasurementKind.AMOUNT, scale=2)
INPUT = MetricEntryInput(value_scaled=1250, local_date=date(2026, 11, 7), zone="America/Toronto")


def command() -> str:
    return str(uuid4())


async def test_create_edit_undo_delete_restore_and_retry(session: AsyncSession) -> None:
    service = MetricEntryService(session, clock=Clock(NOW))
    definition = await service.define(SPEC, command_id=command())
    identity = command()
    entry = await service.create(definition.public_id, INPUT, command_id=identity)
    assert await service.create(definition.public_id, INPUT, command_id=identity.upper()) == entry
    assert entry.value_scaled == 1250
    changed = await service.mutate(
        entry.public_id,
        1,
        "edit",
        entry=INPUT.model_copy(update={"value_scaled": 3500}),
        command_id=command(),
    )
    assert changed.revision == 2
    undone = await service.mutate(entry.public_id, 2, "undo", command_id=command())
    assert undone.value_scaled == 1250
    redone = await service.mutate(entry.public_id, 3, "undo", command_id=command())
    assert redone.value_scaled == 3500
    deleted = await service.mutate(entry.public_id, 4, "delete", command_id=command())
    assert deleted.deleted
    restored = await service.mutate(entry.public_id, 5, "restore", command_id=command())
    assert not restored.deleted
    assert len(await service.history(entry.public_id)) == 6
    assert (await service.entries(definition.public_id)) == (restored,)
    assert await session.scalar(select(func.count()).select_from(MetricEntryChangeTable)) == 6
    assert await service.get_definition(definition.public_id) == definition


async def test_undo_creation_and_undo_again(session: AsyncSession) -> None:
    service = MetricEntryService(session)
    definition = await service.define(SPEC, command_id=command())
    entry = await service.create(definition.public_id, INPUT, command_id=command())
    undone = await service.mutate(entry.public_id, 1, "undo", command_id=command())
    assert undone.deleted
    assert await service.entries(definition.public_id) == ()
    assert len(await service.entries(definition.public_id, include_deleted=True)) == 1
    assert not (await service.mutate(entry.public_id, 2, "undo", command_id=command())).deleted


async def test_definition_rename_archive_semantics_and_retry(session: AsyncSession) -> None:
    service = MetricEntryService(session)
    identity = command()
    definition = await service.define(SPEC, command_id=identity)
    assert await service.define(SPEC, command_id=identity) == definition
    renamed = SPEC.model_copy(update={"name": "Reading time"})
    identity = command()
    saved = await service.configure(
        definition.public_id, 1, renamed, archived=True, command_id=identity
    )
    assert (
        await service.configure(
            definition.public_id, 1, renamed, archived=True, command_id=identity
        )
        == saved
    )
    assert not await service.definitions()
    assert await service.definitions(include_archived=True) == (saved,)
    with pytest.raises(ValueError, match="Restore"):
        await service.create(saved.public_id, INPUT, command_id=command())
    with pytest.raises(ValueError, match="new metric"):
        await service.configure(
            saved.public_id,
            2,
            renamed.model_copy(update={"scale": 3}),
            archived=False,
            command_id=command(),
        )
    with pytest.raises(MetricConflictError):
        await service.configure(saved.public_id, 1, renamed, archived=False, command_id=command())
    await service.configure(saved.public_id, 2, renamed, archived=False, command_id=command())
    assert len(await service.definitions()) == 1


async def test_stale_input_reused_commands_invalid_operations_and_pagination(
    session: AsyncSession,
) -> None:
    service = MetricEntryService(session)
    definition = await service.define(SPEC, command_id=command())
    identity = command()
    first = await service.create(definition.public_id, INPUT, command_id=identity)
    second = await service.create(
        definition.public_id,
        INPUT.model_copy(update={"local_date": date(2026, 11, 8)}),
        command_id=command(),
    )
    for operation in ("restore", "unsupported"):
        with pytest.raises(ValueError):
            await service.mutate(first.public_id, 1, operation, command_id=command())
    with pytest.raises(MetricConflictError):
        await service.create(
            definition.public_id, INPUT.model_copy(update={"value_scaled": 99}), command_id=identity
        )
    await service.mutate(first.public_id, 1, "delete", command_id=command())
    with pytest.raises(MetricConflictError):
        await service.create(definition.public_id, INPUT, command_id=identity)
    for revision, operation, entry in [
        (1, "delete", None),
        (2, "delete", None),
        (2, "edit", INPUT),
    ]:
        with pytest.raises(MetricConflictError):
            await service.mutate(
                first.public_id, revision, operation, entry=entry, command_id=command()
            )
    assert await service.entries(
        definition.public_id, after=(INPUT.local_date.isoformat(), first.public_id)
    ) == (second,)
    with pytest.raises(ValueError):
        await service.entries(definition.public_id, after=("bad", first.public_id))
    with pytest.raises(ValueError):
        await service.entries(definition.public_id, limit=0)
    with pytest.raises(ValueError, match="UUID"):
        await service.define(SPEC, command_id="invalid")
    with pytest.raises(MetricNotFoundError):
        await service.get_entry(command())
    with pytest.raises(MetricNotFoundError):
        await service.get_definition(command())


async def test_invalid_sign_and_project_roll_back_entire_entry(session: AsyncSession) -> None:
    service = MetricEntryService(session)
    definition = await service.define(SPEC, command_id=command())
    for entry in (
        INPUT.model_copy(update={"value_scaled": -10}),
        INPUT.model_copy(update={"project_public_id": command()}),
    ):
        with pytest.raises(ValueError):
            await service.create(definition.public_id, entry, command_id=command())
    assert await session.scalar(select(func.count()).select_from(MetricEntryTable)) == 0
    assert await session.scalar(select(func.count()).select_from(MetricEntryChangeTable)) == 0


async def test_failed_commit_and_lost_response(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = MetricEntryService(session)
    with monkeypatch.context() as patch:
        patch.setattr(session, "commit", AsyncMock(side_effect=RuntimeError("Synthetic full disk")))
        with pytest.raises(RuntimeError):
            await service.define(SPEC, command_id=command())
    assert await session.scalar(select(func.count()).select_from(MetricDefinitionTable)) == 0
    commit = session.commit

    async def lost() -> None:
        await commit()
        raise RuntimeError("Synthetic response lost")

    identity = command()
    with monkeypatch.context() as patch:
        patch.setattr(session, "commit", lost)
        with pytest.raises(RuntimeError):
            await service.define(SPEC, command_id=identity)
    definition = await service.define(SPEC, command_id=identity)
    assert definition.revision == 1
    assert await session.scalar(select(func.count()).select_from(MetricCommandTable)) == 1


async def test_concurrent_same_command_creates_one_result(database: Database) -> None:
    identity = command()

    async def create() -> str:
        async with database.session() as session:
            return (await MetricEntryService(session).define(SPEC, command_id=identity)).public_id

    first, second = await asyncio.gather(create(), create())
    assert first == second


async def test_history_corruption_refuses_mutations(session: AsyncSession) -> None:
    service = MetricEntryService(session)
    definition = await service.define(SPEC, command_id=command())
    entry = await service.create(definition.public_id, INPUT, command_id=command())
    change = await session.scalar(select(MetricEntryChangeTable))
    assert change is not None
    change.state_json = {**change.state_json, "value_scaled": 1}
    await session.commit()
    with pytest.raises(ValueError, match="history"):
        await service.mutate(entry.public_id, 1, "delete", command_id=command())


async def test_upgrade_export_recovery_purge_and_corrupt_backups(tmp_path: Path) -> None:
    from alembic import command as alembic

    path = tmp_path / "profile.sqlite3"
    await asyncio.to_thread(alembic.upgrade, migration_config(path), "20261106_0036")
    await asyncio.to_thread(upgrade_database, path)
    await asyncio.to_thread(validate_snapshot, path, current_schema=True)
    database = Database.from_path(path)
    try:
        async with database.session() as session:
            service = MetricEntryService(session, clock=Clock(NOW))
            definition = await service.define(SPEC, command_id=command())
            identity = command()
            entry = await service.create(definition.public_id, INPUT, command_id=identity)
            entry = await service.mutate(entry.public_id, 1, "undo", command_id=command())
        output = tmp_path / "writing.json"
        await export_writing(database, output)
        records = json.loads(output.read_text(encoding="utf-8"))["records"]
        assert len(records["metric_definitions"]) == 1
        assert len(records["metric_entry_changes"]) == 2
        assert "metric_commands" not in records
        archive, restored = tmp_path / "backup.zip", tmp_path / "restored.sqlite3"
        await asyncio.to_thread(create_backup, path, archive)
        await asyncio.to_thread(restore_backup, archive, restored)
        recovered = Database.from_path(restored)
        try:
            async with recovered.session() as session:
                service = MetricEntryService(session)
                assert await service.get_entry(entry.public_id) == entry
                assert len(await service.history(entry.public_id)) == 2
        finally:
            await recovered.dispose()
        preview = await preview_cleanup(
            database, CleanupRequest(CleanupKind.DELETED, before=NOW + timedelta(days=1))
        )
        assert (preview.metric_entries, preview.metric_changes) == (1, 2)
        assert "metric" in preview.summary
        await apply_cleanup(database, preview)
        async with database.session() as session:
            assert await session.scalar(select(func.count()).select_from(MetricCommandTable)) == 3
            with pytest.raises(MetricNotFoundError):
                await MetricEntryService(session).create(
                    definition.public_id, INPUT, command_id=identity
                )
    finally:
        await database.dispose()
    with closing(sqlite3.connect(restored)) as connection, connection:
        connection.execute("UPDATE metric_entry_changes SET state_json = '{}' WHERE revision = 1")
    with pytest.raises(BackupValidationError):
        await asyncio.to_thread(validate_snapshot, restored)


async def test_packaged_metric_scenario_from_source(tmp_path: Path) -> None:
    from mailbrief.diagnostics.metric_smoke import check_metrics

    await check_metrics(tmp_path / "metric-package-scenario")


async def test_a_saved_definition_is_archived_and_reactivated_as_it_was_read(
    session: AsyncSession,
) -> None:
    """The desktop hands back the definition it read, which carries its identity, revision
    and dates as well as the five fields of a spec."""
    service = MetricEntryService(session, clock=Clock(NOW))
    definition = await service.define(SPEC, command_id=command())
    archived = await service.configure(
        definition.public_id, definition.revision, definition, archived=True, command_id=command()
    )
    assert archived.archived and archived.revision == 2 and archived.name == SPEC.name
    active = await service.configure(
        archived.public_id, archived.revision, archived, archived=False, command_id=command()
    )
    assert not active.archived and active.revision == 3


async def test_newest_first_pages_run_back_from_the_latest_date(session: AsyncSession) -> None:
    service = MetricEntryService(session, clock=Clock(NOW))
    definition = await service.define(SPEC, command_id=command())
    made = [
        await service.create(
            definition.public_id,
            INPUT.model_copy(update={"local_date": INPUT.local_date - timedelta(days=back)}),
            command_id=command(),
        )
        for back in (3, 0, 2, 0, 1)
    ]
    expected = sorted(made, key=lambda entry: (entry.local_date, entry.public_id), reverse=True)
    pages = []
    after = None
    while page := await service.entries(
        definition.public_id, newest_first=True, limit=2, after=after
    ):
        pages.append(page)
        after = (page[-1].local_date.isoformat(), page[-1].public_id)
    assert [len(page) for page in pages] == [2, 2, 1]
    assert [entry for page in pages for entry in page] == expected
    assert pages[0][0].local_date == INPUT.local_date
    # The default stays oldest first, for every other reader.
    assert list(await service.entries(definition.public_id)) == expected[::-1]
