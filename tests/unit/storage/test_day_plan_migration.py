"""Day-plan schema upgrades preserve tasks/writing and enforce membership constraints."""

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command

from mailbrief.domain.drafts import DraftKind
from mailbrief.services.actions import ActionService
from mailbrief.services.drafts import DraftService
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import migration_config
from tests.unit.services.test_manual_tasks import EDIT


async def test_upgrade_and_downgrade_preserve_prior_owner_records(tmp_path: Path) -> None:
    path = tmp_path / "upgrade.sqlite3"
    config = migration_config(path)
    await asyncio.to_thread(command.upgrade, config, "head")
    database = Database.from_path(path)
    async with database.session() as session:
        task = await ActionService(session).create(EDIT)
        note = await DraftService(session).create(DraftKind.NOTE)
    await database.dispose()
    await asyncio.to_thread(command.downgrade, config, "20261103_0027")
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.check, config)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT count(*) FROM day_plans").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM day_plan_items").fetchone() == (0,)
    database = Database.from_path(path)
    async with database.session() as session:
        assert await ActionService(session).get(task.public_id) == task
        assert await DraftService(session).get(note.public_id) == note
    await database.dispose()
    await asyncio.to_thread(command.downgrade, config, "20261103_0027")
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT count(*) FROM actions").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM drafts").fetchone() == (1,)
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.check, config)


def test_date_uniqueness_and_foreign_keys(tmp_path: Path) -> None:
    path = tmp_path / "constraints.sqlite3"
    command.upgrade(migration_config(path), "head")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        insert = (
            "INSERT INTO day_plans(public_id,local_date,creation_zone,revision,"
            "created_at_utc,updated_at_utc) VALUES(?,?,'UTC',1,?,?)"
        )
        now = "2026-11-03 12:00:00"
        connection.execute(insert, ("plan-one", "2026-11-03", now, now))
        connection.commit()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(insert, ("plan-two", "2026-11-03", now, now))
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO day_plan_items(public_id,plan_id,task_public_id,position,"
                "created_at_utc,updated_at_utc) VALUES('item',1,'missing-task',0,?,?)",
                (now, now),
            )
