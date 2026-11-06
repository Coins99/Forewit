"""Migration 0032 adds work blocks without touching earlier owner records (ADR 0028)."""

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command

from mailbrief.services.actions import ActionService
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import migration_config
from tests.unit.services.test_manual_tasks import EDIT

NOW = "2026-11-05 12:00:00"


def insert_block(
    connection: sqlite3.Connection, public_id: str, action_id: int, start: str, end: str
) -> None:
    connection.execute(
        "INSERT INTO work_blocks(public_id,action_id,starts_at_utc,ends_at_utc,time_zone,"
        "created_at_utc,updated_at_utc,revision) VALUES(?,?,?,?,'UTC',?,?,1)",
        (public_id, action_id, start, end, NOW, NOW),
    )


async def test_upgrade_and_downgrade_keep_tasks(tmp_path: Path) -> None:
    path = tmp_path / "upgrade.sqlite3"
    config = migration_config(path)
    await asyncio.to_thread(command.upgrade, config, "20261105_0031")
    await asyncio.to_thread(command.upgrade, config, "head")
    database = Database.from_path(path)
    async with database.session() as session:
        task = await ActionService(session).create(EDIT)
    await database.dispose()
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT count(*) FROM work_blocks").fetchone() == (0,)
        (action_id,) = connection.execute("SELECT id FROM actions").fetchone()
        insert_block(
            connection, "block-one", action_id, "2026-11-05 14:00:00", "2026-11-05 15:00:00"
        )
        connection.commit()
    await asyncio.to_thread(command.downgrade, config, "20261105_0031")
    with closing(sqlite3.connect(path)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert "work_blocks" not in tables
        assert connection.execute("SELECT count(*) FROM actions").fetchone() == (1,)
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.check, config)
    database = Database.from_path(path)
    async with database.session() as session:
        assert (await ActionService(session).get(task.public_id)).blocks == ()
    await database.dispose()


def test_checks_uniqueness_and_the_cascade(tmp_path: Path) -> None:
    path = tmp_path / "constraints.sqlite3"
    command.upgrade(migration_config(path), "head")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO actions(public_id,title,ownership,status,deadline_precision,notes,"
            "created_at_utc,updated_at_utc,revision) "
            "VALUES('task-one','Task','mine','open','none','',?,?,1)",
            (NOW, NOW),
        )
        (action_id,) = connection.execute("SELECT id FROM actions").fetchone()
        insert_block(
            connection, "block-one", action_id, "2026-11-05 14:00:00", "2026-11-05 15:00:00"
        )
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            insert_block(
                connection, "block-one", action_id, "2026-11-05 16:00:00", "2026-11-05 17:00:00"
            )
        with pytest.raises(sqlite3.IntegrityError, match="ends_after_start"):
            insert_block(
                connection, "block-two", action_id, "2026-11-05 16:00:00", "2026-11-05 16:00:00"
            )
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            insert_block(
                connection, "block-three", 999, "2026-11-05 16:00:00", "2026-11-05 17:00:00"
            )
        with pytest.raises(sqlite3.IntegrityError, match="revision_positive"):
            connection.execute("UPDATE work_blocks SET revision = 0")
        connection.execute("DELETE FROM actions")
        assert connection.execute("SELECT count(*) FROM work_blocks").fetchone() == (0,)
