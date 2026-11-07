"""Profiles from either published P2 branch converge without losing owner records."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command
from alembic.script import ScriptDirectory

from mailbrief.storage.backup import create_backup
from mailbrief.storage.migrate import migration_config
from mailbrief.storage.recovery import restore_backup
from tests.unit.storage.test_note_project_migration import insert_old_note


@pytest.mark.parametrize("revision", ["20261104_0029", "20261103_0029", "20261103_0030"])
def test_published_profile_converges_and_round_trips(tmp_path: Path, revision: str) -> None:
    path = tmp_path / "profile.sqlite3"
    config = migration_config(path)
    command.upgrade(config, revision)
    insert_old_note(path)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "INSERT INTO projects(public_id,name,notes,created_at_utc,updated_at_utc,revision) "
            "VALUES(?,'Work','','2026-11-03 12:00:00','2026-11-03 12:00:00',1)",
            ("11111111-1111-4111-8111-111111111111",),
        )
        connection.execute(
            "UPDATE drafts SET project_public_id=?", ("11111111-1111-4111-8111-111111111111",)
        )
        if revision == "20261103_0030":
            connection.execute(
                "UPDATE drafts SET capture_origin='quick_capture', "
                "capture_processed_at_utc='2026-11-04 09:30:00'"
            )
        connection.commit()
        before = connection.execute("SELECT * FROM draft_versions").fetchall()
        sources = connection.execute("SELECT * FROM draft_sources").fetchall()

    archive, restored = tmp_path / "published.zip", tmp_path / "restored.sqlite3"
    create_backup(path, archive)
    restore_backup(archive, restored)
    with closing(sqlite3.connect(restored)) as connection:
        assert connection.execute("SELECT * FROM draft_versions").fetchall() == before
        assert connection.execute("SELECT * FROM draft_sources").fetchall() == sources
        assert connection.execute("SELECT project_public_id FROM drafts").fetchone() == (
            "11111111-1111-4111-8111-111111111111",
        )
        assert connection.execute("SELECT * FROM alembic_version").fetchall() == [
            ("20261106_0036",)
        ]

    for _ in range(2):
        command.upgrade(config, "head")
        command.check(config)
        with closing(sqlite3.connect(path)) as connection:
            assert connection.execute("SELECT * FROM alembic_version").fetchall() == [
                ("20261106_0036",)
            ]
            assert connection.execute("SELECT * FROM draft_versions").fetchall() == before
            assert connection.execute("SELECT * FROM draft_sources").fetchall() == sources
            assert connection.execute("SELECT project_public_id FROM drafts").fetchone() == (
                "11111111-1111-4111-8111-111111111111",
            )
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
            if revision == "20261103_0030":
                assert connection.execute(
                    "SELECT capture_origin,capture_processed_at_utc FROM drafts"
                ).fetchone() == (
                    "quick_capture",
                    "2026-11-04 09:30:00",
                )
        command.downgrade(config, revision)

    command.upgrade(config, "head")
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    command.check(config)
    assert ScriptDirectory.from_config(config).get_heads() == ["20261106_0036"]
