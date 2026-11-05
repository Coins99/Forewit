"""Create receipts upgrade additively, obey constraints and preserve existing owner work."""

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


async def test_upgrade_downgrade_preserves_existing_tasks(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    config = migration_config(path)
    await asyncio.to_thread(command.upgrade, config, "head")
    db = Database.from_path(path)
    async with db.session() as session:
        original = await ActionService(session).create(EDIT)
    await db.dispose()
    await asyncio.to_thread(command.downgrade, config, "20261101_0025")
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.check, config)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT count(*) FROM task_commands").fetchone() == (0,)
    upgraded = Database.from_path(path)
    async with upgraded.session() as session:
        assert await ActionService(session).get(original.public_id) == original
        # A legacy create is not retroactively assigned a receipt.
        await ActionService(session).create(EDIT, command_id="11111111-1111-4111-8111-111111111111")
    await upgraded.dispose()
    await asyncio.to_thread(command.downgrade, config, "20261101_0025")
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT count(*) FROM actions").fetchone() == (2,)
        assert connection.execute("SELECT count(*) FROM action_activity").fetchone() == (2,)
        assert not connection.execute(
            "SELECT name FROM sqlite_master WHERE name='task_commands'"
        ).fetchall()
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.check, config)


@pytest.mark.parametrize(
    "column,value",
    [
        ("command_id", "short"),
        ("operation", "mail.send"),
        ("input_hash", "a" * 63),
        ("input_hash", "g" * 64),
        ("result_revision", 0),
    ],
)
def test_receipt_constraints(tmp_path: Path, column: str, value: str | int) -> None:
    path = tmp_path / "constraints.sqlite3"
    command.upgrade(migration_config(path), "head")
    fields: dict[str, str | int] = {
        "command_id": "11111111-1111-4111-8111-111111111111",
        "operation": "task.create",
        "input_hash": "a" * 64,
        "result_public_id": "22222222-2222-4222-8222-222222222222",
        "result_revision": 1,
        "created_at_utc": "2026-11-01 12:00:00",
    }
    fields[column] = value
    with closing(sqlite3.connect(path)) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO task_commands(command_id,operation,input_hash,result_public_id,"
            "result_revision,created_at_utc) VALUES(?,?,?,?,?,?)",
            tuple(fields.values()),
        )
