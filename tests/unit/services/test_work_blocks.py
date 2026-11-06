"""Work blocks (ADR 0028): scheduling, moving, Undo, the task's lifecycle and warnings.

A block never changes its task, so most tests also check the task's row is untouched.
"""

import asyncio
import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import (
    ActionEdit,
    ActionStatus,
    DeadlineChange,
    DeadlineChangeKind,
)
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.events import EventEdit
from mailbrief.domain.recurrence import RepeatFrequency, RepeatRule
from mailbrief.domain.work_blocks import MAX_BLOCKS_PER_TASK, BlockTime
from mailbrief.services.actions import ActionService
from mailbrief.services.data import (
    CleanupKind,
    CleanupRequest,
    apply_cleanup,
    export_writing,
    preview_cleanup,
)
from mailbrief.services.events import EventService
from mailbrief.services.work_blocks import (
    BlockConflictError,
    BlockNotFoundError,
    WorkBlockService,
)
from mailbrief.storage.backup import create_backup
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import upgrade_database
from mailbrief.storage.recovery import restore_backup
from mailbrief.storage.tables import (
    ActionActivityTable,
    ActionTable,
    GoogleCalendarTable,
    GoogleEventTable,
    WorkBlockTable,
)
from tests.factories import Clock, Ids
from tests.unit.services.test_actions_service import seed

NOW = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)  # Monday 09:00 in Toronto.
TORONTO = ZoneInfo("America/Toronto")
MONDAY = date(2026, 11, 2)
TUESDAY = date(2026, 11, 3)
ZONE = "America/Toronto"


def task(title: str = "Draft report", **extra: Any) -> ActionEdit:
    values: dict[str, Any] = {
        "title": title,
        "ownership": ActionOwnership.MINE,
        "person": None,
        "effort": None,
        "priority": None,
        "tags": (),
        "estimate_minutes": 120,
        "target_date": None,
        "notes": "",
    }
    values.update(extra)
    return ActionEdit(**values)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), TORONTO)


def span(day: date, start: int, end: int) -> BlockTime:
    return BlockTime(starts_at_utc=at(day, start), ends_at_utc=at(day, end), time_zone=ZONE)


@pytest.fixture
def clock() -> Clock:
    return Clock(NOW)


@pytest.fixture
def blocks(session: AsyncSession, clock: Clock) -> WorkBlockService:
    return WorkBlockService(session, clock=clock, id_factory=Ids())


@pytest.fixture
def actions(session: AsyncSession, clock: Clock) -> ActionService:
    return ActionService(session, clock=clock, zone=TORONTO)


async def action_row(session: AsyncSession, public_id: str) -> dict[str, Any]:
    """Every column of the task's row, read fresh, to prove a block left it alone."""
    session.expire_all()
    row = (
        await session.execute(
            select(ActionTable.__table__).where(ActionTable.public_id == public_id)
        )
    ).one()
    return dict(row._mapping)


async def activity_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(ActionActivityTable)) or 0)


async def test_schedule_move_and_unschedule_never_touch_the_task(
    session: AsyncSession, actions: ActionService, blocks: WorkBlockService, clock: Clock
) -> None:
    created = await actions.create(task())
    before = await action_row(session, created.public_id)
    history = await activity_count(session)

    clock.advance(minutes=1)
    saved = await blocks.schedule(created.public_id, span(MONDAY, 10, 12))
    assert saved.block.revision == 1
    assert saved.block.action_public_id == created.public_id
    assert saved.block.minutes == 120
    assert (saved.previous, saved.overlaps, saved.after_deadline) == (None, (), False)

    clock.advance(minutes=1)
    moved = await blocks.move(saved.block.public_id, 1, span(TUESDAY, 13, 14))
    assert moved.block.revision == 2
    assert moved.previous == span(MONDAY, 10, 12)
    assert moved.block.updated_at_utc == NOW + timedelta(minutes=2)

    clock.advance(minutes=1)
    gone = await blocks.unschedule(saved.block.public_id, 2)
    assert gone.revision == 3
    assert await blocks.for_action(created.public_id) == ()

    assert await action_row(session, created.public_id) == before
    assert await activity_count(session) == history


async def test_stale_revisions_and_missing_blocks_are_refused(
    actions: ActionService, blocks: WorkBlockService
) -> None:
    created = await actions.create(task())
    saved = await blocks.schedule(created.public_id, span(MONDAY, 10, 11))
    with pytest.raises(BlockConflictError, match="changed since it was loaded"):
        await blocks.move(saved.block.public_id, 7, span(MONDAY, 11, 12))
    with pytest.raises(BlockConflictError, match="changed since it was loaded"):
        await blocks.unschedule(saved.block.public_id, 7)
    with pytest.raises(BlockNotFoundError, match="scheduled time was not found"):
        await blocks.move("00000000-0000-4000-8000-0000000000ff", 1, span(MONDAY, 11, 12))
    with pytest.raises(BlockNotFoundError, match="task was not found"):
        await blocks.schedule("00000000-0000-4000-8000-0000000000ff", span(MONDAY, 11, 12))
    with pytest.raises(BlockNotFoundError):
        await blocks.restore("00000000-0000-4000-8000-0000000000ff")
    with pytest.raises(BlockNotFoundError):
        await blocks.get("00000000-0000-4000-8000-0000000000ff")


async def test_undo_move_only_while_the_block_is_as_the_move_left_it(
    actions: ActionService, blocks: WorkBlockService
) -> None:
    created = await actions.create(task())
    saved = await blocks.schedule(created.public_id, span(MONDAY, 10, 11))
    moved = await blocks.move(saved.block.public_id, 1, span(MONDAY, 14, 15))
    assert moved.previous is not None
    undone = await blocks.undo_move(saved.block.public_id, moved.block.revision, moved.previous)
    assert undone.block.time == span(MONDAY, 10, 11)
    assert undone.block.revision == 3

    again = await blocks.move(saved.block.public_id, 3, span(MONDAY, 16, 17))
    await blocks.move(saved.block.public_id, 4, span(MONDAY, 8, 9))
    assert again.previous is not None
    with pytest.raises(BlockConflictError, match="can no longer be undone"):
        await blocks.undo_move(saved.block.public_id, again.block.revision, again.previous)


async def test_unschedule_and_restore_round_trip(
    actions: ActionService, blocks: WorkBlockService
) -> None:
    created = await actions.create(task())
    saved = await blocks.schedule(created.public_id, span(MONDAY, 10, 11))
    gone = await blocks.unschedule(saved.block.public_id, 1)
    with pytest.raises(BlockNotFoundError):
        await blocks.get(saved.block.public_id)
    back = await blocks.restore(saved.block.public_id)
    assert back.block.revision == gone.revision + 1
    assert back.block.time == saved.block.time
    # Restoring a live block changes nothing.
    assert (await blocks.restore(saved.block.public_id)).block == back.block
    shown = await blocks.get(saved.block.public_id)
    assert (shown.action_title, shown.action_status, shown.waiting) == (
        "Draft report",
        ActionStatus.OPEN,
        False,
    )


async def test_only_open_tasks_take_new_or_moved_blocks(
    actions: ActionService, blocks: WorkBlockService
) -> None:
    created = await actions.create(task())
    saved = await blocks.schedule(created.public_id, span(MONDAY, 10, 11))
    done = await actions.complete(created.public_id, created.revision)
    with pytest.raises(BlockConflictError, match="Only an open task"):
        await blocks.schedule(created.public_id, span(MONDAY, 12, 13))
    with pytest.raises(BlockConflictError, match="Only an open task"):
        await blocks.move(saved.block.public_id, 1, span(MONDAY, 12, 13))
    # A completed task's block stays, shown muted, and can still be unscheduled and restored.
    assert [block.public_id for block in await blocks.for_action(created.public_id)] == [
        saved.block.public_id
    ]
    shown = await blocks.get(saved.block.public_id)
    assert shown.action_status is ActionStatus.COMPLETED and not shown.open
    gone = await blocks.unschedule(saved.block.public_id, 1)
    await blocks.restore(gone.public_id)

    reopened = await actions.reopen(created.public_id, done.revision)
    assert reopened.status is ActionStatus.OPEN
    await blocks.move(saved.block.public_id, 3, span(MONDAY, 12, 13))


async def test_waiting_tasks_can_be_scheduled(
    actions: ActionService, blocks: WorkBlockService
) -> None:
    created = await actions.create(task(ownership=ActionOwnership.WAITING_FOR, person="Sam"))
    saved = await blocks.schedule(created.public_id, span(MONDAY, 10, 11))
    assert (await blocks.get(saved.block.public_id)).waiting


async def test_a_task_holds_at_most_fifty_live_blocks(
    actions: ActionService, blocks: WorkBlockService
) -> None:
    created = await actions.create(task())
    first = None
    for index in range(MAX_BLOCKS_PER_TASK):
        day = MONDAY + timedelta(days=index)
        saved = await blocks.schedule(created.public_id, span(day, 10, 11))
        first = first or saved
    assert first is not None
    with pytest.raises(BlockConflictError, match="at most 50"):
        await blocks.schedule(created.public_id, span(MONDAY, 12, 13))
    gone = await blocks.unschedule(first.block.public_id, 1)
    extra = await blocks.schedule(created.public_id, span(MONDAY, 12, 13))
    # Bringing back the unscheduled one would make 51.
    with pytest.raises(BlockConflictError, match="at most 50"):
        await blocks.restore(gone.public_id)
    assert extra.block.revision == 1


async def test_deleting_a_task_hides_its_blocks_and_restoring_brings_them_back(
    session: AsyncSession, actions: ActionService, blocks: WorkBlockService
) -> None:
    created = await actions.create(task())
    saved = await blocks.schedule(created.public_id, span(MONDAY, 10, 11))
    events = EventService(session)
    await actions.delete(created.public_id, created.revision)
    assert (await events.list_range(MONDAY, MONDAY, TORONTO)).blocks == ()
    with pytest.raises(BlockNotFoundError):
        await blocks.get(saved.block.public_id)
    with pytest.raises(BlockNotFoundError):
        await blocks.unschedule(saved.block.public_id, 1)
    await actions.restore(created.public_id)
    shown = (await events.list_range(MONDAY, MONDAY, TORONTO)).blocks
    assert [block.public_id for block in shown] == [saved.block.public_id]


async def test_unaccepting_a_task_hides_its_blocks_as_a_delete_does(
    session: AsyncSession, actions: ActionService, blocks: WorkBlockService
) -> None:
    seeded = await seed(session)
    accepted = await actions.accept(seeded.suggestion_ids[0])
    saved = await blocks.schedule(accepted.public_id, span(MONDAY, 10, 11))
    await actions.unaccept(accepted.public_id, accepted.revision)
    assert (await EventService(session).list_range(MONDAY, MONDAY, TORONTO)).blocks == ()
    with pytest.raises(BlockNotFoundError):
        await blocks.get(saved.block.public_id)


async def test_the_next_occurrence_of_a_repeating_task_starts_with_no_blocks(
    session: AsyncSession, actions: ActionService, blocks: WorkBlockService
) -> None:
    created = await actions.create(
        task(target_date=MONDAY), repeat=RepeatRule(frequency=RepeatFrequency.DAILY)
    )
    await blocks.schedule(created.public_id, span(MONDAY, 10, 11))
    await actions.complete(created.public_id, created.revision)
    rows = (await session.scalars(select(ActionTable).order_by(ActionTable.id))).all()
    assert len(rows) == 2
    following = rows[1].public_id
    assert await blocks.for_action(following) == ()
    assert len(await blocks.for_action(created.public_id)) == 1


async def test_actions_carry_their_live_blocks_by_start(
    actions: ActionService, blocks: WorkBlockService
) -> None:
    created = await actions.create(task())
    late = await blocks.schedule(created.public_id, span(TUESDAY, 10, 11))
    early = await blocks.schedule(created.public_id, span(MONDAY, 10, 11))
    gone = await blocks.schedule(created.public_id, span(MONDAY, 15, 16))
    await blocks.unschedule(gone.block.public_id, 1)
    loaded = await actions.get(created.public_id)
    assert [block.public_id for block in loaded.blocks] == [
        early.block.public_id,
        late.block.public_id,
    ]


async def test_overlaps_name_events_google_events_and_other_blocks(
    session: AsyncSession, actions: ActionService, blocks: WorkBlockService
) -> None:
    events = EventService(session)
    edit = EventEdit(
        title="Dentist",
        all_day=False,
        starts_at_utc=at(MONDAY, 10, 30),
        ends_at_utc=at(MONDAY, 11, 30),
        time_zone=ZONE,
    )
    await events.create(edit)
    await events.create(  # An all-day event isn't a clash.
        EventEdit(
            title="Holiday", all_day=True, start_date=MONDAY, last_date=MONDAY, time_zone=ZONE
        )
    )
    await events.create(  # Ending as the block starts isn't a clash.
        edit.model_copy(
            update={
                "title": "Earlier",
                "starts_at_utc": at(MONDAY, 9),
                "ends_at_utc": at(MONDAY, 10),
            }
        )
    )
    calendar = GoogleCalendarTable(
        calendar_id="work@example.com", name="Work", is_primary=False, updated_at_utc=NOW
    )
    session.add(calendar)
    await session.flush()
    session.add(
        GoogleEventTable(
            calendar_row_id=calendar.id,
            event_id="standup",
            title="Standup",
            all_day=False,
            starts_at_utc=at(MONDAY, 10),
            ends_at_utc=at(MONDAY, 10, 15),
            web_link="https://calendar.google.com/calendar/event?eid=x",
            fetched_at_utc=NOW,
        )
    )
    await session.commit()
    other = await actions.create(task("Read contract"))
    await blocks.schedule(other.public_id, span(MONDAY, 11, 12))
    created = await actions.create(task())

    saved = await blocks.schedule(created.public_id, span(MONDAY, 10, 12))
    assert saved.overlaps == ("Standup (Work)", "Dentist", "Task · Read contract")
    assert saved.more_overlaps == 0

    same = await blocks.schedule(created.public_id, span(MONDAY, 11, 12))
    # The same task's other block counts too; labels follow the entries' starts.
    assert same.overlaps == ("Task · Draft report", "Dentist", "Task · Read contract")
    assert same.more_overlaps == 0
    short = BlockTime(starts_at_utc=at(MONDAY, 11), ends_at_utc=at(MONDAY, 11, 20), time_zone=ZONE)
    crowded = await blocks.schedule(created.public_id, short)
    assert len(crowded.overlaps) == 3 and crowded.more_overlaps == 1


async def test_a_block_after_the_deadline_warns_and_moves_nothing(
    session: AsyncSession, actions: ActionService, blocks: WorkBlockService
) -> None:
    created = await actions.create(
        task(target_date=MONDAY),
        deadline=DeadlineChange(kind=DeadlineChangeKind.SET, due_date=MONDAY),
    )
    before = await action_row(session, created.public_id)
    inside = await blocks.schedule(created.public_id, span(MONDAY, 22, 23))
    assert not inside.after_deadline
    after = await blocks.move(inside.block.public_id, 1, span(TUESDAY, 9, 10))
    assert after.after_deadline
    assert await action_row(session, created.public_id) == before


async def test_list_range_includes_blocks_touching_the_days(
    session: AsyncSession, actions: ActionService, blocks: WorkBlockService
) -> None:
    created = await actions.create(task())
    events = EventService(session)
    overnight = BlockTime(starts_at_utc=at(MONDAY, 23), ends_at_utc=at(TUESDAY, 1), time_zone=ZONE)
    night = await blocks.schedule(created.public_id, overnight)
    midnight = BlockTime(starts_at_utc=at(MONDAY, 22), ends_at_utc=at(TUESDAY, 0), time_zone=ZONE)
    ending = await blocks.schedule(created.public_id, midnight)
    later = await blocks.schedule(created.public_id, span(TUESDAY, 9, 10))
    tuesday = (await events.list_range(TUESDAY, TUESDAY, TORONTO)).blocks
    # A block ending exactly at midnight doesn't touch the next day.
    assert [block.public_id for block in tuesday] == [
        night.block.public_id,
        later.block.public_id,
    ]
    monday = (await events.list_range(MONDAY, MONDAY, TORONTO)).blocks
    assert [block.public_id for block in monday] == [
        ending.block.public_id,
        night.block.public_id,
    ]


async def test_purging_deleted_tasks_counts_and_removes_their_blocks(
    database: Database, session: AsyncSession, actions: ActionService, blocks: WorkBlockService
) -> None:
    created = await actions.create(task())
    await blocks.schedule(created.public_id, span(MONDAY, 10, 11))
    gone = await blocks.schedule(created.public_id, span(MONDAY, 12, 13))
    await blocks.unschedule(gone.block.public_id, 1)
    await actions.delete(created.public_id, created.revision)
    request = CleanupRequest(CleanupKind.DELETED, before=NOW + timedelta(days=1))
    preview = await preview_cleanup(database, request)
    assert preview.work_blocks == 2
    assert "Remove 2 scheduled times of those actions." in preview.summary
    await apply_cleanup(database, preview)
    session.expire_all()
    count = await session.scalar(select(func.count()).select_from(WorkBlockTable))
    assert count == 0


async def test_errors_never_carry_the_task_title(
    actions: ActionService, blocks: WorkBlockService
) -> None:
    created = await actions.create(task("Secret project"))
    await actions.complete(created.public_id, created.revision)
    with pytest.raises(BlockConflictError) as raised:
        await blocks.schedule(created.public_id, span(MONDAY, 10, 11))
    assert "Secret" not in str(raised.value)
    assert "Secret" not in repr(raised.value)


async def test_blocks_survive_restart_backup_restore_and_export(tmp_path: Path) -> None:
    path = tmp_path / "original.sqlite3"
    await asyncio.to_thread(upgrade_database, path)
    database = Database.from_path(path)
    async with database.session() as session:
        created = await ActionService(session, clock=Clock(NOW)).create(task())
        saved = await WorkBlockService(session, clock=Clock(NOW)).schedule(
            created.public_id, span(MONDAY, 10, 11)
        )
    await database.dispose()
    exported = tmp_path / "writing.json"
    restarted = Database.from_path(path)
    try:
        async with restarted.session() as session:
            assert (await ActionService(session).get(created.public_id)).blocks == (saved.block,)
        await export_writing(restarted, exported)
    finally:
        await restarted.dispose()
    records = json.loads(exported.read_text(encoding="utf-8"))["records"]
    assert [item["public_id"] for item in records["work_blocks"]] == [saved.block.public_id]
    backup = tmp_path / "backup.zip"
    await asyncio.to_thread(create_backup, path, backup)
    restored_path = tmp_path / "restored.sqlite3"
    await asyncio.to_thread(restore_backup, backup, restored_path)
    recovered = Database.from_path(restored_path)
    try:
        async with recovered.session() as session:
            assert await WorkBlockService(session).for_action(created.public_id) == (saved.block,)
    finally:
        await recovered.dispose()
