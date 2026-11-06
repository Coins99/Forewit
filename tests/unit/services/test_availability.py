"""Working hours, the next free time and the tasks that still need time (ADR 0029)."""

import asyncio
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import ActionEdit, ActionPriority, DeadlineChange, DeadlineChangeKind
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.events import EventEdit
from mailbrief.domain.work_blocks import BlockTime
from mailbrief.domain.working_hours import SUGGESTED_HOURS, HoursEdit, WorkingWindow
from mailbrief.services.actions import ActionService
from mailbrief.services.availability import AvailabilityService, HoursConflictError
from mailbrief.services.data import export_writing
from mailbrief.services.events import EventService
from mailbrief.services.work_blocks import WorkBlockService
from mailbrief.storage.backup import create_backup
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import upgrade_database
from mailbrief.storage.recovery import restore_backup
from tests.factories import Clock, Ids

NOW = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)  # Monday 09:00 in Toronto.
TORONTO = ZoneInfo("America/Toronto")
MONDAY = date(2026, 11, 2)
TUESDAY = date(2026, 11, 3)
ZONE = "America/Toronto"


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), TORONTO)


def span(day: date, start: int, end: int) -> BlockTime:
    return BlockTime(starts_at_utc=at(day, start), ends_at_utc=at(day, end), time_zone=ZONE)


def task(title: str, **extra: Any) -> ActionEdit:
    values: dict[str, Any] = {
        "title": title,
        "ownership": ActionOwnership.MINE,
        "person": None,
        "effort": None,
        "priority": None,
        "tags": (),
        "estimate_minutes": None,
        "target_date": None,
        "notes": "",
    }
    values.update(extra)
    return ActionEdit(**values)


@pytest.fixture
def clock() -> Clock:
    return Clock(NOW)


@pytest.fixture
def service(session: AsyncSession, clock: Clock) -> AvailabilityService:
    return AvailabilityService(session, clock=clock)


@pytest.fixture
def actions(session: AsyncSession, clock: Clock) -> ActionService:
    return ActionService(session, clock=clock, zone=TORONTO)


@pytest.fixture
def blocks(session: AsyncSession, clock: Clock) -> WorkBlockService:
    return WorkBlockService(session, clock=clock, id_factory=Ids())


async def test_hours_start_unset_and_save_with_their_revision(
    service: AvailabilityService, session: AsyncSession
) -> None:
    unset = await service.hours()
    assert unset.revision == 0 and not unset.is_set
    saved = await service.save_hours(SUGGESTED_HOURS.model_copy(update={"buffer_minutes": 10}), 0)
    assert saved.revision == 1 and saved.is_set and saved.buffer_minutes == 10
    assert saved.updated_at_utc == NOW
    assert await AvailabilityService(session).hours() == saved
    with pytest.raises(HoursConflictError, match="changed since"):
        await service.save_hours(HoursEdit(), 0)
    cleared = await service.save_hours(HoursEdit(), 1)
    assert cleared.revision == 2 and not cleared.is_set
    assert (await EventService(session).list_range(MONDAY, MONDAY, TORONTO)).hours == cleared


async def test_unreadable_hours_count_as_none_and_can_be_replaced(
    service: AvailabilityService, session: AsyncSession
) -> None:
    await service.save_hours(SUGGESTED_HOURS, 0)
    for broken in ('{"mon": 1}', "[[[540, 600, 1]]]", "[[[540, 545]]]", '[[["9", "17"]]]'):
        await session.execute(
            text("UPDATE working_hours SET windows_json = :value"), {"value": broken}
        )
        await session.commit()
        unreadable = await service.hours()
        assert unreadable.revision == 1 and not unreadable.is_set
    await session.execute(text("UPDATE working_hours SET windows_json = 'not json'"))
    await session.commit()
    assert (await service.hours()).revision == 1
    replaced = await service.save_hours(SUGGESTED_HOURS, 1)
    assert replaced.revision == 2 and replaced.days == SUGGESTED_HOURS.days


async def test_next_free_reads_the_calendar_from_the_day_asked(
    service: AvailabilityService,
    session: AsyncSession,
    actions: ActionService,
    blocks: WorkBlockService,
) -> None:
    assert await service.next_free(at(MONDAY, 9), 60, TORONTO) is None  # No hours yet.
    nine_to_noon = (WorkingWindow(start_minute=540, end_minute=720),)
    await service.save_hours(HoursEdit(days=(nine_to_noon,) * 7, buffer_minutes=15), 0)
    events = EventService(session)
    await events.create(
        EventEdit(
            title="Standup",
            all_day=False,
            starts_at_utc=at(MONDAY, 9),
            ends_at_utc=at(MONDAY, 9, 30),
            time_zone=ZONE,
        )
    )
    await events.create(
        EventEdit(
            title="Lunch maybe",
            all_day=False,
            starts_at_utc=at(MONDAY, 10),
            ends_at_utc=at(MONDAY, 11),
            time_zone=ZONE,
            busy=False,
        )
    )
    created = await actions.create(task("Draft report"))
    await blocks.schedule(created.public_id, span(MONDAY, 11, 12))
    # 09:30 plus the buffer is 09:45, and the free event takes nothing.
    assert await service.next_free(at(MONDAY, 8), 60, TORONTO) == at(MONDAY, 9, 45)
    assert await service.next_free(at(MONDAY, 8), 90, TORONTO) == at(TUESDAY, 9)
    assert await service.next_free(at(MONDAY, 8), 60, TORONTO, first_day=TUESDAY) == at(TUESDAY, 9)
    await events.create(
        EventEdit(
            title="Away",
            all_day=True,
            start_date=TUESDAY,
            last_date=TUESDAY,
            time_zone=ZONE,
            busy=True,
        )
    )
    assert await service.next_free(at(MONDAY, 8), 90, TORONTO) == at(date(2026, 11, 4), 9)
    assert await service.next_free(at(MONDAY, 8), 4 * 60, TORONTO) is None


async def test_unscheduled_lists_the_owners_open_tasks_that_need_time(
    service: AvailabilityService,
    session: AsyncSession,
    actions: ActionService,
    blocks: WorkBlockService,
    clock: Clock,
) -> None:
    partly = await actions.create(task("Partly scheduled", estimate_minutes=180))
    await blocks.schedule(partly.public_id, span(MONDAY, 13, 14))
    full = await actions.create(task("Fully scheduled", estimate_minutes=60))
    await blocks.schedule(full.public_id, span(MONDAY, 15, 16))
    loose = await actions.create(task("No estimate"))
    given = await actions.create(task("No estimate, given time"))
    await blocks.schedule(given.public_id, span(TUESDAY, 9, 10))
    stale = await actions.create(task("Scheduled last week", estimate_minutes=60))
    await blocks.schedule(stale.public_id, span(MONDAY - timedelta(days=7), 9, 10))
    unscheduled_block = await actions.create(task("Unscheduled block", estimate_minutes=60))
    removed = await blocks.schedule(unscheduled_block.public_id, span(MONDAY, 16, 17))
    await blocks.unschedule(removed.block.public_id, 1)
    await actions.create(
        task("Waiting on Sam", ownership=ActionOwnership.WAITING_FOR, person="Sam")
    )
    done = await actions.create(task("Done already"))
    await actions.complete(done.public_id, done.revision)
    deleted = await actions.create(task("Deleted"))
    await actions.delete(deleted.public_id, deleted.revision)

    found = await service.unscheduled(MONDAY, TORONTO)
    titles = [item.title for item in found.tasks]
    assert sorted(titles) == [
        "No estimate",
        "Partly scheduled",
        "Scheduled last week",
        "Unscheduled block",
    ]
    assert found.total == 4
    by_title = {item.title: item for item in found.tasks}
    assert by_title["Partly scheduled"].scheduled_minutes == 60
    assert by_title["Partly scheduled"].left_minutes == 120
    assert by_title["Scheduled last week"].scheduled_minutes == 0
    assert by_title["No estimate"].left_minutes is None
    assert by_title["No estimate"].action_public_id == loose.public_id
    # From Tuesday on, Monday's block no longer counts.
    later = await service.unscheduled(TUESDAY, TORONTO)
    assert "Fully scheduled" in [item.title for item in later.tasks]
    assert "No estimate, given time" not in [item.title for item in later.tasks]


async def test_unscheduled_tasks_follow_the_actions_order_and_the_limit(
    service: AvailabilityService, actions: ActionService
) -> None:
    await actions.create(task("Someday"))
    await actions.create(task("Low", priority=ActionPriority.LOW))
    await actions.create(task("High", priority=ActionPriority.HIGH))
    await actions.create(task("Planned Tuesday", target_date=TUESDAY))
    await actions.create(
        task("Due Monday"),
        deadline=DeadlineChange(kind=DeadlineChangeKind.SET, due_date=MONDAY),
    )
    found = await service.unscheduled(MONDAY, TORONTO)
    assert [item.title for item in found.tasks] == [
        "Planned Tuesday",
        "Due Monday",
        "High",
        "Low",
        "Someday",
    ]
    due = next(item for item in found.tasks if item.title == "Due Monday")
    assert due.deadline_day == MONDAY
    planned = next(item for item in found.tasks if item.title == "Planned Tuesday")
    assert planned.target_date == TUESDAY and planned.deadline_day is None
    first_two = await service.unscheduled(MONDAY, TORONTO, limit=2)
    assert [item.title for item in first_two.tasks] == ["Planned Tuesday", "Due Monday"]
    assert first_two.total == 5


async def test_a_timed_deadline_is_shown_on_its_local_day(
    service: AvailabilityService, actions: ActionService
) -> None:
    # 22:30 in Toronto is already Wednesday in UTC; the panel shows the owner's Tuesday.
    await actions.create(
        task("Late call"),
        deadline=DeadlineChange(
            kind=DeadlineChangeKind.SET, due_date=TUESDAY, due_time=time(22, 30)
        ),
    )
    (found,) = (await service.unscheduled(MONDAY, TORONTO)).tasks
    assert found.deadline_day == TUESDAY


async def test_hours_and_free_events_survive_restart_backup_restore_and_export(
    tmp_path: Path,
) -> None:
    path = tmp_path / "original.sqlite3"
    await asyncio.to_thread(upgrade_database, path)
    database = Database.from_path(path)
    async with database.session() as session:
        hours = await AvailabilityService(session, clock=Clock(NOW)).save_hours(
            SUGGESTED_HOURS.model_copy(update={"buffer_minutes": 5}), 0
        )
        free = await EventService(session, clock=Clock(NOW)).create(
            EventEdit(
                title="Lunch maybe",
                all_day=False,
                starts_at_utc=at(MONDAY, 12),
                ends_at_utc=at(MONDAY, 13),
                time_zone=ZONE,
                busy=False,
            )
        )
    await database.dispose()
    exported = tmp_path / "writing.json"
    restarted = Database.from_path(path)
    try:
        async with restarted.session() as session:
            assert await AvailabilityService(session).hours() == hours
            assert not (await EventService(session).get(free.public_id)).busy
        await export_writing(restarted, exported)
    finally:
        await restarted.dispose()
    records = json.loads(exported.read_text(encoding="utf-8"))["records"]
    assert [row["buffer_minutes"] for row in records["working_hours"]] == [5]
    assert [row["busy"] for row in records["calendar_events"]] == [False]
    backup = tmp_path / "backup.zip"
    await asyncio.to_thread(create_backup, path, backup)
    restored_path = tmp_path / "restored.sqlite3"
    await asyncio.to_thread(restore_backup, backup, restored_path)
    recovered = Database.from_path(restored_path)
    try:
        async with recovered.session() as session:
            assert await AvailabilityService(session).hours() == hours
            assert not (await EventService(session).get(free.public_id)).busy
    finally:
        await recovered.dispose()
