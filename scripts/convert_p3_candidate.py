"""Convert the divergent unmerged P3 development schema into a separate current DB.

Never edits the source or replaces an existing destination. Run with the desktop closed:
python -m scripts.convert_p3_candidate SOURCE DESTINATION --owner-zone America/Toronto
The explicit zone must match the candidate's saved hours; incompatible windows fail.
"""

import argparse
import asyncio
import json
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from mailbrief.domain.preferences import PreferencesEdit
from mailbrief.domain.work_blocks import BlockTime
from mailbrief.domain.working_hours import DatedHours, HoursEdit, WorkingWindow
from mailbrief.infra.files import publish_exclusively
from mailbrief.services.preferences import PreferencesService
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import upgrade_database
from mailbrief.storage.recovery import validate_snapshot
from mailbrief.storage.tables import Base


def convert(source: Path, destination: Path, owner_zone: str) -> None:
    """Preserve owner rows/IDs and receipts, validating before publishing a new file."""
    zone = ZoneInfo(owner_zone)
    if destination.exists() or not source.is_file():
        raise ValueError("Choose an existing source and a new destination.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix="p3-conversion-") as work:
        snapshot, output = Path(work) / "source.sqlite3", Path(work) / "converted.sqlite3"
        with (
            closing(sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)) as original,
            closing(sqlite3.connect(snapshot)) as copy,
        ):
            original.backup(copy)
        upgrade_database(output)
        with closing(sqlite3.connect(snapshot)) as old, closing(sqlite3.connect(output)) as new:
            old.row_factory = sqlite3.Row
            columns = {row[1] for row in old.execute("PRAGMA table_info(work_blocks)")}
            if "task_public_id" not in columns or "action_id" in columns:
                raise ValueError("This source is not the divergent local P3 candidate.")
            profiles = old.execute("SELECT * FROM availability_profiles").fetchall()
            hours = None
            if profiles:
                profile = profiles[0]
                if profile["time_zone"] != zone.key:
                    raise ValueError(
                        "The explicit owner zone must match the candidate's working-hours zone."
                    )
                days: list[list[WorkingWindow]] = [[] for _ in range(7)]
                for row in old.execute(
                    "SELECT * FROM availability_windows ORDER BY weekday,start_minute"
                ):
                    days[row["weekday"]].append(
                        WorkingWindow(
                            start_minute=row["start_minute"], end_minute=row["end_minute"]
                        )
                    )
                overrides = []
                for row in old.execute("SELECT * FROM availability_overrides ORDER BY day"):
                    windows = tuple(
                        WorkingWindow(
                            start_minute=item["start_minute"], end_minute=item["end_minute"]
                        )
                        for item in old.execute(
                            (
                                "SELECT * FROM availability_override_windows WHERE override_i"
                                "d=? ORDER BY start_minute"
                            ),
                            (row["id"],),
                        )
                    )
                    overrides.append(DatedHours(day=row["day"], windows=windows))
                hours = HoursEdit(
                    days=tuple(tuple(day) for day in days),
                    buffer_before_minutes=profile["buffer_before_minutes"],
                    buffer_after_minutes=profile["buffer_after_minutes"],
                    overrides=tuple(overrides),
                )
            source_tables = {
                row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            legacy_tables = {
                "availability_profiles",
                "availability_windows",
                "availability_overrides",
                "availability_override_windows",
                "scheduling_commands",
                "alembic_version",
                "sqlite_sequence",
            }
            if source_tables - set(Base.metadata.tables) - legacy_tables or len(profiles) > 1:
                raise ValueError("The development database has an unsupported schema.")
            ignored = {
                "work_blocks",
                "working_hours",
                "google_calendars",
                "google_events",
                "google_calendar_state",
            }
            new.execute("PRAGMA foreign_keys=OFF")
            for table in Base.metadata.sorted_tables:
                if table.name in ignored or table.name not in source_tables:
                    continue
                target_columns = {column.name for column in table.columns}
                source_columns = {
                    row[1] for row in old.execute(f'PRAGMA table_info("{table.name}")')
                }
                if source_columns - target_columns:
                    raise ValueError("The development database contains unsupported record fields.")
                for row in old.execute(f'SELECT * FROM "{table.name}"'):
                    values = {key: row[key] for key in dict(row) if key in target_columns}
                    if table.name == "calendar_events" and "busy" not in values:
                        values["busy"] = not row["all_day"]
                    if table.name == "owner_preferences" and profiles:
                        if values.get("time_zone") not in (None, zone.key):
                            raise ValueError(
                                "Owner preferences and candidate hours use different zones; r"
                                "econcile them in the old app first."
                            )
                        values["time_zone"] = zone.key
                    names = ",".join(f'"{key}"' for key in values)
                    placeholders = ",".join("?" for _ in values)
                    new.execute(
                        f'INSERT INTO "{table.name}" ({names}) VALUES ({placeholders})',
                        tuple(values.values()),
                    )
            tasks = dict(old.execute("SELECT public_id,id FROM actions"))
            for row in old.execute("SELECT * FROM work_blocks"):
                # Main's indexed range reads rely on its twelve-hour maximum.
                start = datetime.fromisoformat(row["starts_at_utc"])
                end = datetime.fromisoformat(row["ends_at_utc"])
                BlockTime(
                    starts_at_utc=start.replace(tzinfo=UTC) if start.tzinfo is None else start,
                    ends_at_utc=end.replace(tzinfo=UTC) if end.tzinfo is None else end,
                    time_zone=row["time_zone"],
                )
                values = dict(row)
                values["action_id"] = tasks[values.pop("task_public_id")]
                names = ",".join(f'"{key}"' for key in values)
                new.execute(
                    f"INSERT INTO work_blocks ({names}) VALUES ({','.join('?' for _ in values)})",
                    tuple(values.values()),
                )
            if hours is not None:
                profile = profiles[0]
                new.execute(
                    (
                        "INSERT INTO working_hours(id,windows_json,buffer_minutes,buf"
                        "fer_before_minutes,buffer_after_minutes,overrides_json,revis"
                        "ion,updated_at_utc) VALUES(1,?,0,?,?,?,?,?)"
                    ),
                    (
                        json.dumps(
                            [
                                [[window.start_minute, window.end_minute] for window in day]
                                for day in hours.days
                            ]
                        ),
                        hours.before_minutes,
                        hours.after_minutes,
                        json.dumps([item.model_dump(mode="json") for item in hours.overrides]),
                        profile["revision"],
                        profile["updated_at_utc"],
                    ),
                )
            if "scheduling_commands" in source_tables:
                for row in old.execute("SELECT * FROM scheduling_commands"):
                    # Retain command identities; old hashes cannot authorize new-protocol replay.
                    new.execute(
                        (
                            "INSERT INTO block_commands(command_id,operation,input_hash,r"
                            "esult_public_id,result_revision,result_deleted,created_at_ut"
                            "c) VALUES(?,?,?,?,?,?,?)"
                        ),
                        tuple(
                            row[key]
                            for key in (
                                "command_id",
                                "operation",
                                "input_hash",
                                "result_public_id",
                                "result_revision",
                                "result_deleted",
                                "created_at_utc",
                            )
                        ),
                    )
            new.commit()
            if new.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("The converted candidate has broken record references.")
        if profiles:
            asyncio.run(_set_zone(output, zone.key))
        validate_snapshot(output, current_schema=True)
        publish_exclusively(str(output), destination)


async def _set_zone(path: Path, zone: str) -> None:
    database = Database.from_path(path)
    try:
        async with database.session() as session:
            service = PreferencesService(session)
            saved = await service.get()
            if saved.time_zone != zone:
                edit = PreferencesEdit.model_validate(
                    saved.model_dump(include=set(PreferencesEdit.model_fields))
                    | {"time_zone": zone}
                )
                await service.save(edit, saved.revision)
    finally:
        await database.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--owner-zone", required=True)
    args = parser.parse_args()
    convert(args.source, args.destination, args.owner_zone)
    print("Converted and validated a separate database. Original preserved.")


if __name__ == "__main__":
    main()
