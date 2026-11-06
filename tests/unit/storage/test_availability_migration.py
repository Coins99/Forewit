"""Migration 0033 adds Busy/Free and working hours, keeping every event (ADR 0029)."""

import asyncio
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest
from alembic import command

from mailbrief.services.availability import AvailabilityService
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import migration_config

NOW = "2026-11-05 12:00:00"


def insert_event(connection: sqlite3.Connection, public_id: str, *, all_day: bool) -> None:
    if all_day:
        connection.execute(
            "INSERT INTO calendar_events(public_id,title,notes,all_day,start_date,last_date,"
            "time_zone,created_at_utc,updated_at_utc,revision) "
            "VALUES(?,'Trip','',1,'2026-11-06','2026-11-07','UTC',?,?,1)",
            (public_id, NOW, NOW),
        )
    else:
        connection.execute(
            "INSERT INTO calendar_events(public_id,title,notes,all_day,starts_at_utc,ends_at_utc,"
            "time_zone,created_at_utc,updated_at_utc,revision) "
            "VALUES(?,'Dentist','',0,'2026-11-05 14:00:00','2026-11-05 15:00:00','UTC',?,?,1)",
            (public_id, NOW, NOW),
        )


def insert_google(connection: sqlite3.Connection, event_id: str, *, all_day: bool) -> None:
    times = (
        (None, None, "2026-11-06", "2026-11-06")
        if all_day
        else ("2026-11-05 16:00:00", "2026-11-05 17:00:00", None, None)
    )
    connection.execute(
        "INSERT INTO google_events(calendar_row_id,event_id,title,all_day,starts_at_utc,"
        "ends_at_utc,start_date,last_date,fetched_at_utc) VALUES(1,?,'Sync',?,?,?,?,?,?)",
        (event_id, int(all_day), *times, NOW),
    )


async def test_upgrade_keeps_each_events_meaning_and_downgrade_keeps_events(
    tmp_path: Path,
) -> None:
    path = tmp_path / "upgrade.sqlite3"
    config = migration_config(path)
    await asyncio.to_thread(command.upgrade, config, "20261105_0032")
    with closing(sqlite3.connect(path)) as connection:
        insert_event(connection, "event-timed", all_day=False)
        insert_event(connection, "event-day", all_day=True)
        connection.execute(
            "INSERT INTO google_calendars(id,calendar_id,name,is_primary,updated_at_utc) "
            "VALUES(1,'owner@example.com','Owner',1,?)",
            (NOW,),
        )
        insert_google(connection, "google-timed", all_day=False)
        insert_google(connection, "google-day", all_day=True)
        connection.commit()
    await asyncio.to_thread(command.upgrade, config, "head")
    with closing(sqlite3.connect(path)) as connection:
        events = connection.execute(
            "SELECT public_id, busy FROM calendar_events ORDER BY public_id"
        ).fetchall()
        google = connection.execute(
            "SELECT event_id, busy FROM google_events ORDER BY event_id"
        ).fetchall()
        assert events == [("event-day", 0), ("event-timed", 1)]
        assert google == [("google-day", 0), ("google-timed", 1)]
        assert connection.execute("SELECT count(*) FROM working_hours").fetchone() == (0,)
    database = Database.from_path(path)
    async with database.session() as session:
        assert (await AvailabilityService(session).hours()).revision == 0
    await database.dispose()
    await asyncio.to_thread(command.downgrade, config, "20261105_0032")
    with closing(sqlite3.connect(path)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
        assert "working_hours" not in tables
        assert connection.execute("SELECT count(*) FROM calendar_events").fetchone() == (2,)
        assert connection.execute("SELECT count(*) FROM google_events").fetchone() == (2,)
        columns = {row[1] for row in connection.execute("PRAGMA table_info(calendar_events)")}
        assert "busy" not in columns
    await asyncio.to_thread(command.upgrade, config, "head")
    await asyncio.to_thread(command.check, config)


def test_the_working_hours_row_is_one_bounded_row(tmp_path: Path) -> None:
    path = tmp_path / "constraints.sqlite3"
    command.upgrade(migration_config(path), "head")
    with closing(sqlite3.connect(path)) as connection:

        def insert(row_id: int, buffer: int, revision: int) -> None:
            connection.execute(
                "INSERT INTO working_hours(id,windows_json,buffer_minutes,revision,updated_at_utc)"
                " VALUES(?,'[[],[],[],[],[],[],[]]',?,?,?)",
                (row_id, buffer, revision, NOW),
            )

        with pytest.raises(sqlite3.IntegrityError, match="single_row"):
            insert(2, 0, 1)
        with pytest.raises(sqlite3.IntegrityError, match="buffer_minutes_known"):
            insert(1, 20, 1)
        with pytest.raises(sqlite3.IntegrityError, match="revision_positive"):
            insert(1, 0, 0)
        insert(1, 30, 1)
        assert connection.execute("SELECT buffer_minutes FROM working_hours").fetchone() == (30,)
        with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
            connection.execute("UPDATE calendar_events SET busy = NULL")
            connection.execute(
                "INSERT INTO calendar_events(public_id,title,notes,all_day,start_date,last_date,"
                "time_zone,created_at_utc,updated_at_utc,revision,busy) "
                "VALUES('x','Trip','',1,'2026-11-06','2026-11-07','UTC',?,?,1,NULL)",
                (NOW, NOW),
            )
