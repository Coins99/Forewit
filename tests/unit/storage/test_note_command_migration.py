"""Upgrade preserves writing; receipt constraints protect retry control metadata."""

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command

from mailbrief.services.drafts import DraftService
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import migration_config
from tests.unit.storage.test_note_project_migration import (
    OLD_NOTE_ID,
    assert_old_note,
    insert_old_note,
)


async def test_upgrade_preserves_notes_and_downgrade_preserves_writing(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    config = migration_config(path)
    await asyncio.to_thread(command.upgrade, config, "20261101_0026")
    insert_old_note(path)  # Today's ORM writes columns this revision doesn't have yet.
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.check, config)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT count(*) FROM note_commands").fetchone() == (0,)
    upgraded = Database.from_path(path)
    async with upgraded.session() as session:
        assert_old_note(await DraftService(session).get(OLD_NOTE_ID))
    await upgraded.dispose()
    await asyncio.to_thread(command.downgrade, config, "20261101_0026")
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT count(*) FROM drafts").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM draft_versions").fetchone() == (1,)
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.check, config)


@pytest.mark.parametrize(
    "column,value",
    [
        ("command_id", "short"),
        ("operation", "mail.send"),
        ("input_hash", "g" * 64),
        ("result_revision", 0),
    ],
)
def test_receipt_constraints(tmp_path: Path, column: str, value: str | int) -> None:
    path = tmp_path / "constraints.sqlite3"
    command.upgrade(migration_config(path), "head")
    fields: dict[str, str | int] = {
        "command_id": "11111111-1111-4111-8111-111111111111",
        "operation": "note.create",
        "input_hash": "a" * 64,
        "result_public_id": "22222222-2222-4222-8222-222222222222",
        "result_revision": 1,
        "created_at_utc": "2026-11-03 12:00:00",
    }
    fields[column] = value
    with closing(sqlite3.connect(path)) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO note_commands(command_id,operation,input_hash,result_public_id,"
            "result_revision,created_at_utc) VALUES(?,?,?,?,?,?)",
            tuple(fields.values()),
        )
