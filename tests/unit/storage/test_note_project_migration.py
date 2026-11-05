"""Migration 0029 adds the note's project link and keeps every draft, version and source."""

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Final

import pytest
from alembic import command

from mailbrief.domain.drafts import Draft, DraftKind
from mailbrief.services.drafts import DraftService
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import migration_config

OLD_NOTE_ID: Final = "0b0b0b0b-0000-4000-8000-000000000001"
_PROJECT_ID: Final = "0c0c0c0c-0000-4000-8000-000000000002"
_NOW: Final = "2026-11-03 12:00:00"


def insert_old_note(path: Path) -> None:
    """A note with one version and one source, written as revisions 0007 to 0028 hold one.

    Migration tests at older revisions use this instead of DraftService, whose ORM writes
    columns those revisions don't have yet.
    """
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        draft_id = connection.execute(
            "INSERT INTO drafts(public_id,kind,title,to_text,cc_text,body,created_at_utc,"
            "updated_at_utc,revision) VALUES(?,'note','Budget','','','Line one\n',?,?,1)",
            (OLD_NOTE_ID, _NOW, _NOW),
        ).lastrowid
        connection.execute(
            "INSERT INTO draft_versions(draft_id,number,origin,title,to_text,cc_text,body,"
            "created_at_utc) VALUES(?,1,'created','Budget','','','Line one\n',?)",
            (draft_id, _NOW),
        )
        connection.execute(
            "INSERT INTO draft_sources(draft_id,message_id,provider_message_id,subject,"
            "sender_address,web_link,received_at_utc) VALUES(?,NULL,'message-1','Subject',"
            "'sender@example.com','https://mail.google.com/mail/u/0/#all/1',?)",
            (draft_id, _NOW),
        )
        connection.commit()


def assert_old_note(draft: Draft) -> None:
    """What insert_old_note() wrote, read back after an upgrade."""
    assert draft.public_id == OLD_NOTE_ID
    assert draft.kind is DraftKind.NOTE
    assert (draft.title, draft.body, draft.revision) == ("Budget", "Line one\n", 1)
    assert [source.provider_message_id for source in draft.sources] == ["message-1"]
    assert draft.project_public_id is None
    assert draft.project_name is None


def _query(path: Path, sql: str) -> list[tuple[object, ...]]:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(sql).fetchall()


async def test_upgrade_keeps_writing_and_downgrade_removes_only_the_link(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite3"
    config = migration_config(path)
    await asyncio.to_thread(command.upgrade, config, "20261103_0028")
    insert_old_note(path)
    versions = _query(path, "SELECT * FROM draft_versions")
    sources = _query(path, "SELECT * FROM draft_sources")

    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.check, config)
    assert _query(path, "SELECT project_public_id FROM drafts") == [(None,)]
    assert _query(path, "SELECT * FROM draft_versions") == versions
    assert _query(path, "SELECT * FROM draft_sources") == sources
    assert _query(path, "PRAGMA foreign_key_check") == []
    links = _query(path, "PRAGMA foreign_key_list(drafts)")
    assert {(row[2], row[3], row[4], row[6]) for row in links} == {
        ("actions", "action_id", "id", "SET NULL"),
        ("projects", "project_public_id", "public_id", "SET NULL"),
    }
    indexes = {row[1] for row in _query(path, "PRAGMA index_list(drafts)")}
    assert {"ix_drafts_project_public_id", "ix_drafts_action_id"} <= indexes
    database = Database.from_path(path)
    async with database.session() as session:
        assert_old_note(await DraftService(session).get(OLD_NOTE_ID))
    await database.dispose()

    await asyncio.to_thread(command.downgrade, config, "20261103_0028")
    columns = {row[1] for row in _query(path, "PRAGMA table_info(drafts)")}
    assert "project_public_id" not in columns
    assert _query(path, "SELECT public_id, title, body FROM drafts") == [
        (OLD_NOTE_ID, "Budget", "Line one\n")
    ]
    assert _query(path, "SELECT * FROM draft_versions") == versions
    assert _query(path, "SELECT * FROM draft_sources") == sources
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.check, config)


def test_project_link_is_checked_and_cleared_when_the_project_goes(tmp_path: Path) -> None:
    path = tmp_path / "constraints.sqlite3"
    command.upgrade(migration_config(path), "head")
    insert_old_note(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE drafts SET project_public_id = 'missing-project' WHERE public_id = ?",
                (OLD_NOTE_ID,),
            )
        connection.execute(
            "INSERT INTO projects(public_id,name,notes,revision,created_at_utc,updated_at_utc)"
            " VALUES(?,'Home','',1,?,?)",
            (_PROJECT_ID, _NOW, _NOW),
        )
        connection.execute(
            "UPDATE drafts SET project_public_id = ? WHERE public_id = ?",
            (_PROJECT_ID, OLD_NOTE_ID),
        )
        connection.execute("DELETE FROM projects")
        connection.commit()
        assert connection.execute("SELECT project_public_id, title FROM drafts").fetchall() == [
            (None, "Budget")
        ]
