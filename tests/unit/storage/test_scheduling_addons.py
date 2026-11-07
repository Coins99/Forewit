"""Populated upgrades, strict recovery and explicit divergent-candidate conversion."""

import asyncio
import sqlite3
from contextlib import closing
from datetime import timedelta
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import select

from mailbrief.errors import ConfigurationError
from mailbrief.services.availability import AvailabilityService
from mailbrief.services.work_blocks import WorkBlockService
from mailbrief.storage.backup import create_backup
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import migration_config, upgrade_database
from mailbrief.storage.recovery import restore_backup, validate_snapshot
from mailbrief.storage.tables import WorkBlockTable
from scripts.convert_p3_candidate import convert
from tests.unit.services.test_scheduling import DAY, NOW, edit, setup


def test_populated_main_hours_upgrade_preserves_symmetric_buffers(tmp_path: Path) -> None:
    path = tmp_path / "main.sqlite3"
    config = migration_config(path)
    command.upgrade(config, "20261105_0033")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "INSERT INTO working_hours VALUES(1,'[[[540,1020]],[],[],[],["
            "],[],[]]',10,4,'2026-11-06 00:00:00')"
        )
        connection.commit()
    upgrade_database(path)
    validate_snapshot(path, current_schema=True)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute(
            "SELECT buffer_minutes,buffer_before_minutes,buffer_after_min"
            "utes,overrides_json,revision FROM working_hours"
        ).fetchone() == (10, 10, 10, "[]", 4)
        assert connection.execute("SELECT count(*) FROM google_calendar_state").fetchone() == (0,)


async def test_backup_restore_preserves_new_fields_and_receipts(tmp_path: Path) -> None:
    path = tmp_path / "profile.sqlite3"
    await asyncio.to_thread(upgrade_database, path)
    database = Database.from_path(path)
    try:
        async with database.session() as session:
            _, task = await setup(session)
            blocks = WorkBlockService(session)
            result = await blocks.command(
                "11111111-1111-4111-8111-111111111111", "schedule", task, time=edit(task)
            )
            from mailbrief.domain.work_blocks import BlockSaved

            assert isinstance(result, BlockSaved)
            await blocks.set_locked(result.block.public_id, 1, True)
        archive = tmp_path / "backup.zip"
        await asyncio.to_thread(create_backup, path, archive)
    finally:
        await database.dispose()
    destination = tmp_path / "restored.sqlite3"
    await asyncio.to_thread(restore_backup, archive, destination)
    validate_snapshot(destination, current_schema=True)
    with closing(sqlite3.connect(destination)) as connection:
        assert connection.execute("SELECT locked FROM work_blocks").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM block_commands").fetchone() == (1,)


async def test_divergent_candidate_converts_to_a_new_validated_copy(
    database: Database, tmp_path: Path
) -> None:
    async with database.session() as session:
        _, task = await setup(session)
        await WorkBlockService(session).schedule(task, edit(task))
        hours = await AvailabilityService(session).hours()
        stored = (await session.scalars(select(WorkBlockTable))).one()
        public_id = stored.public_id
    source, output = tmp_path / "candidate.sqlite3", tmp_path / "converted.sqlite3"
    original = Path(str(database.engine.url.database))
    with (
        closing(sqlite3.connect(original)) as connection,
        closing(sqlite3.connect(source)) as target,
    ):
        connection.backup(target)
        target.execute(
            "CREATE TABLE legacy_blocks AS SELECT work_blocks.id,work_blo"
            "cks.public_id,actions.public_id AS task_public_id,starts_at_"
            "utc,ends_at_utc,time_zone,locked,work_blocks.revision,work_b"
            "locks.created_at_utc,work_blocks.updated_at_utc,work_blocks."
            "deleted_at_utc FROM work_blocks JOIN actions ON action_id=ac"
            "tions.id"
        )
        target.execute("DROP TABLE work_blocks")
        target.execute("ALTER TABLE legacy_blocks RENAME TO work_blocks")
        target.execute("DROP TABLE working_hours")
        target.execute(
            "CREATE TABLE availability_profiles(id INTEGER,public_id TEXT"
            ",time_zone TEXT,configured INTEGER,buffer_before_minutes INT"
            "EGER,buffer_after_minutes INTEGER,revision INTEGER,created_a"
            "t_utc TEXT,updated_at_utc TEXT)"
        )
        target.execute(
            "INSERT INTO availability_profiles VALUES(1,?,'UTC',1,5,20,1,?,?)",
            (str(public_id), NOW.isoformat(), NOW.isoformat()),
        )
        target.execute(
            "CREATE TABLE availability_windows(profile_id INTEGER,weekday"
            " INTEGER,start_minute INTEGER,end_minute INTEGER)"
        )
        target.executemany(
            "INSERT INTO availability_windows VALUES(1,?,?,?)",
            [
                (index, window.start_minute, window.end_minute)
                for index, day in enumerate(hours.days)
                for window in day
            ],
        )
        target.execute("CREATE TABLE availability_overrides(id INTEGER,day TEXT)")
        target.execute(
            "CREATE TABLE availability_override_windows(override_id INTEG"
            "ER,start_minute INTEGER,end_minute INTEGER)"
        )
        target.execute(
            "INSERT INTO availability_overrides VALUES(1,?)",
            ((DAY + timedelta(days=1)).isoformat(),),
        )
        target.commit()
    before = source.read_bytes()
    with pytest.raises(ConfigurationError, match="unmerged P3"):
        await asyncio.to_thread(upgrade_database, source)
    await asyncio.to_thread(convert, source, output, "UTC")
    assert source.read_bytes() == before
    validate_snapshot(output, current_schema=True)
    with closing(sqlite3.connect(output)) as connection:
        assert connection.execute("SELECT public_id FROM work_blocks").fetchone() == (public_id,)
        assert connection.execute(
            "SELECT buffer_before_minutes,buffer_after_minutes FROM working_hours"
        ).fetchone() == (5, 20)
    with pytest.raises(ValueError):
        await asyncio.to_thread(convert, source, output, "UTC")
    with pytest.raises(ValueError, match="zone"):
        await asyncio.to_thread(convert, source, tmp_path / "wrong-zone.sqlite3", "America/Toronto")
