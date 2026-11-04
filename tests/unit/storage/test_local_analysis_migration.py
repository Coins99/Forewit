"""Local preferences preserve old profiles and reject invalid mode/limit values."""

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command

from mailbrief.storage.migrate import migration_config


def test_upgrade_preserves_preferences_and_defaults_to_cloud(tmp_path: Path) -> None:
    path = tmp_path / "existing.sqlite3"
    config = migration_config(path)
    command.upgrade(config, "20261023_0024")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            "INSERT INTO owner_preferences "
            "(id,time_zone,shortlist_limit,excluded_senders_json,revision,updated_at_utc) "
            "VALUES(1,'Asia/Tokyo',3,'[\"@private.example\"]',4,'2026-11-01 12:00:00')"
        )
        connection.commit()
    command.upgrade(config, "head")
    command.check(config)
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute(
            "SELECT time_zone,shortlist_limit,excluded_senders_json,revision,"
            "analysis_mode,local_message_limit FROM owner_preferences"
        ).fetchone() == ("Asia/Tokyo", 3, '["@private.example"]', 4, "cloud", 20)
    command.downgrade(config, "20261023_0024")
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute(
            "SELECT time_zone,shortlist_limit FROM owner_preferences"
        ).fetchone() == ("Asia/Tokyo", 3)
    command.upgrade(config, "head")
    command.check(config)


@pytest.mark.parametrize(
    "values", [("unknown", 20), ("local", 0), ("local", 21), (None, 20), ("local", None)]
)
def test_database_constraints_refuse_invalid_local_preferences(
    tmp_path: Path, values: tuple[str | None, int | None]
) -> None:
    path = tmp_path / "invalid.sqlite3"
    command.upgrade(migration_config(path), "head")
    with closing(sqlite3.connect(path)) as connection, pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO owner_preferences "
            "(id,excluded_senders_json,updated_at_utc,analysis_mode,local_message_limit) "
            "VALUES(1,'[]','2026-11-01 12:00:00',?,?)",
            values,
        )


def test_upgrade_does_not_invent_preferences_or_consent(tmp_path: Path) -> None:
    path = tmp_path / "fresh.sqlite3"
    command.upgrade(migration_config(path), "head")
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT count(*) FROM owner_preferences").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM ai_consents").fetchone() == (0,)
