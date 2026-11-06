"""Workload and conflicts (ADR 0030): exact definitions, explained and never stored."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from mailbrief.domain.actions import ActionStatus
from mailbrief.domain.events import (
    CalendarEvent,
    CalendarRange,
    FocusDay,
    FocusTask,
    ScheduledBlock,
)
from mailbrief.domain.google_calendar import GoogleCoverage, GoogleEvent
from mailbrief.domain.work_blocks import WorkBlock
from mailbrief.domain.working_hours import WEEKDAYS, WorkingHours, WorkingWindow
from mailbrief.domain.workload import (
    PROVISIONAL_AFTER,
    ConflictKind,
    ProvisionalReason,
    block_conflicts,
    conflicts_for,
    google_reason,
    workload,
)

TORONTO = ZoneInfo("America/Toronto")
MONDAY = date(2026, 11, 2)
TUESDAY = date(2026, 11, 3)
ID = "5b1d7c2e-3f4a-4b6c-8d9e-0a1b2c3d4e{:02d}"
TASK_A, TASK_B, TASK_C = ID.format(91), ID.format(92), ID.format(93)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), TORONTO).astimezone(UTC)


EARLY = at(MONDAY, 8)  # Monday 08:00: the day is ahead.


def hours(*windows: tuple[int, int], buffer: int = 0, days: range = range(5)) -> WorkingHours:
    week = tuple(
        tuple(WorkingWindow(start_minute=s * 60, end_minute=e * 60) for s, e in windows)
        if weekday in days
        else ()
        for weekday in range(WEEKDAYS)
    )
    return WorkingHours(days=week, buffer_minutes=buffer, revision=1)


NINE_TO_FIVE = hours((9, 17), buffer=10)


def event(
    start: datetime, end: datetime, *, busy: bool = True, number: int = 1, title: str = "Standup"
) -> CalendarEvent:
    return CalendarEvent(
        public_id=ID.format(number),
        title=title,
        notes="",
        all_day=False,
        starts_at_utc=start,
        ends_at_utc=end,
        start_date=None,
        last_date=None,
        time_zone="America/Toronto",
        revision=1,
        created_at_utc=EARLY,
        updated_at_utc=EARLY,
        busy=busy,
    )


def day_off(day: date) -> CalendarEvent:
    return CalendarEvent(
        public_id=ID.format(50),
        title="Holiday",
        notes="",
        all_day=True,
        starts_at_utc=None,
        ends_at_utc=None,
        start_date=day,
        last_date=day,
        time_zone="America/Toronto",
        revision=1,
        created_at_utc=EARLY,
        updated_at_utc=EARLY,
        busy=True,
    )


def block(
    start: datetime,
    end: datetime,
    *,
    number: int = 80,
    task: str = TASK_A,
    title: str = "Draft report",
    status: ActionStatus = ActionStatus.OPEN,
    **extra: object,
) -> ScheduledBlock:
    stored = WorkBlock(
        public_id=ID.format(number),
        action_public_id=task,
        starts_at_utc=start,
        ends_at_utc=end,
        time_zone="America/Toronto",
        revision=1,
        created_at_utc=EARLY,
        updated_at_utc=EARLY,
    )
    return ScheduledBlock.model_validate(
        {"block": stored, "action_title": title, "action_status": status, **extra}
    )


def shown(
    *,
    events: tuple[CalendarEvent, ...] = (),
    google_events: tuple[GoogleEvent, ...] = (),
    blocks: tuple[ScheduledBlock, ...] = (),
    focus: tuple[FocusDay, ...] = (),
    working: WorkingHours = NINE_TO_FIVE,
    first: date = MONDAY,
    last: date = MONDAY,
    coverage: GoogleCoverage | None = None,
) -> CalendarRange:
    return CalendarRange(
        first_day=first,
        last_day=last,
        events=events,
        markers=(),
        google_events=google_events,
        google_coverage=coverage,
        blocks=blocks,
        hours=working,
        focus=focus,
    )


def test_a_day_holds_its_blocks_and_focus_against_its_available_time() -> None:
    day = shown(
        events=(
            event(at(MONDAY, 10), at(MONDAY, 11)),
            event(at(MONDAY, 10, 30), at(MONDAY, 11, 30), number=2),  # Counted once.
            event(at(MONDAY, 15), at(MONDAY, 16), busy=False, number=3),  # Free: no time.
        ),
        blocks=(
            block(at(MONDAY, 13), at(MONDAY, 15)),
            block(at(MONDAY, 14), at(MONDAY, 16), number=81, task=TASK_B),  # Overlaps.
            block(at(MONDAY, 16), at(MONDAY, 17), number=82, status=ActionStatus.COMPLETED),
        ),
        focus=(
            FocusDay(
                day=MONDAY,
                tasks=(
                    FocusTask(action_public_id=TASK_A, estimate_minutes=240),  # Has a block.
                    FocusTask(action_public_id=TASK_C, estimate_minutes=60),
                    FocusTask(action_public_id=ID.format(94)),  # No estimate.
                ),
            ),
        ),
    )
    (load,) = workload(day, TORONTO, EARLY, google_on=False)
    # 09:00–17:00 less 09:50–11:40 (the meetings once, with their 10-minute buffers).
    assert load.available_minutes == 480 - 110
    assert (load.scheduled_minutes, load.scheduled_total_minutes) == (180, 240)
    assert (load.focus_minutes, load.focus_tasks, load.focus_unestimated) == (60, 2, 1)
    assert load.planned_minutes == 240 and not load.overfilled
    assert load.provisional_reasons == (ProvisionalReason.NO_ESTIMATE,)
    assert load.flagged and load.conflicted_blocks == 2  # The two blocks overlap.


def test_a_day_planned_past_its_time_is_overfilled_until_it_has_passed() -> None:
    day = shown(
        blocks=(block(at(MONDAY, 9), at(MONDAY, 15)),),
        focus=(
            FocusDay(day=MONDAY, tasks=(FocusTask(action_public_id=TASK_B, estimate_minutes=180),)),
        ),
    )
    (load,) = workload(day, TORONTO, EARLY, google_on=False)
    assert load.planned_minutes == 540 and load.over_minutes == 60 and load.overfilled
    (later,) = workload(day, TORONTO, at(TUESDAY, 8), google_on=False)
    assert not later.flagged and not later.overfilled and later.provisional_reasons == ()
    assert later.planned_minutes == 540  # Still shown, never flagged.
    today = workload(day, TORONTO, at(MONDAY, 16), google_on=False)[0]
    assert today.flagged and today.overfilled  # Today counts the whole day.


def test_without_working_hours_nothing_is_available_or_over() -> None:
    day = shown(blocks=(block(at(MONDAY, 9), at(MONDAY, 19)),), working=WorkingHours.unset())
    (load,) = workload(day, TORONTO, EARLY, google_on=False)
    assert load.available_minutes is None and load.scheduled_minutes == 600
    assert load.over_minutes == 0 and not load.overfilled


def test_a_day_off_has_no_time_and_every_block_on_it_conflicts() -> None:
    planned = block(at(MONDAY, 10), at(MONDAY, 11))
    day = shown(events=(day_off(MONDAY),), blocks=(planned,))
    (load,) = workload(day, TORONTO, EARLY, google_on=False)
    assert load.availability.day_off and load.available_minutes == 0
    assert load.over_minutes == 60
    (found,) = block_conflicts(day, TORONTO, EARLY)[planned.public_id]
    assert (found.kind, found.label, found.at) == (ConflictKind.DAY_OFF, "Holiday", at(MONDAY, 0))


def test_dst_days_have_the_hours_the_clock_shows() -> None:
    always = hours((0, 24), days=range(7))
    spring, autumn = date(2026, 3, 8), date(2026, 11, 1)
    first, *_middle, last = workload(
        shown(working=always, first=spring, last=spring + timedelta(days=1)),
        TORONTO,
        at(spring, 0),
        google_on=False,
    )
    assert first.available_minutes == 23 * 60 and last.available_minutes == 24 * 60
    (fallback,) = workload(
        shown(working=always, first=autumn, last=autumn), TORONTO, at(autumn, 0), google_on=False
    )
    assert fallback.available_minutes == 25 * 60


def test_google_makes_a_day_provisional_only_while_it_is_on() -> None:
    now = EARLY
    fresh = GoogleCoverage(first_day=MONDAY, last_day=TUESDAY, synced_at_utc=now)
    old = fresh.model_copy(update={"synced_at_utc": now - PROVISIONAL_AFTER - timedelta(hours=1)})
    elsewhere = fresh.model_copy(update={"first_day": TUESDAY})
    assert google_reason(None, MONDAY, now) is ProvisionalReason.GOOGLE_MISSING
    assert google_reason(elsewhere, MONDAY, now) is ProvisionalReason.GOOGLE_MISSING
    assert google_reason(old, MONDAY, now) is ProvisionalReason.GOOGLE_OLD
    assert google_reason(fresh, MONDAY, now) is None
    edge = fresh.model_copy(update={"synced_at_utc": now - PROVISIONAL_AFTER})
    assert google_reason(edge, MONDAY, now) is None  # Exactly a day old still counts.
    (load,) = workload(shown(coverage=old), TORONTO, now, google_on=True)
    assert load.provisional_reasons == (ProvisionalReason.GOOGLE_OLD,)
    (load,) = workload(shown(coverage=None), TORONTO, now, google_on=False)
    assert not load.provisional


def test_a_block_s_conflicts_name_what_it_clashes_with_in_order() -> None:
    planned = block(
        at(MONDAY, 16, 45),
        at(MONDAY, 18),
        estimate_minutes=60,
        upcoming_minutes=75,
        due_at_utc=at(MONDAY, 17),
    )
    sync = GoogleEvent(
        calendar_id="work@example.com",
        calendar_name="Work",
        event_id="sync",
        title="Sync",
        all_day=False,
        starts_at_utc=at(MONDAY, 17),
        ends_at_utc=at(MONDAY, 17, 30),
        busy=True,
    )
    events: list[CalendarEvent | GoogleEvent] = [
        event(at(MONDAY, 16), at(MONDAY, 16, 40)),  # Ends 5 minutes before: its buffer.
        event(at(MONDAY, 16), at(MONDAY, 16, 30), number=2),  # 15 minutes before: clear.
        event(at(MONDAY, 17, 30), at(MONDAY, 18), busy=False, number=3),  # Free.
        sync,
    ]
    other = block(at(MONDAY, 17, 45), at(MONDAY, 18, 30), number=81, title="Review")
    found = conflicts_for(
        planned, TORONTO, events=events, blocks=[planned, other], hours=NINE_TO_FIVE
    )
    assert [(item.kind, item.label) for item in found] == [
        (ConflictKind.BUFFER, "Standup"),
        (ConflictKind.BUSY, "Sync (Work)"),
        (ConflictKind.BLOCK, "Task · Review"),
        (ConflictKind.OUTSIDE_HOURS, None),
        (ConflictKind.AFTER_DEADLINE, None),
        (ConflictKind.OVER_ESTIMATE, None),
    ]
    no_buffer = hours((9, 17))
    found = conflicts_for(planned, TORONTO, events=events, blocks=[], hours=no_buffer)
    assert ConflictKind.BUFFER not in {item.kind for item in found}


def test_more_time_than_the_estimate_marks_the_task_s_latest_block_only() -> None:
    first = block(at(MONDAY, 9), at(MONDAY, 9, 45), estimate_minutes=60, upcoming_minutes=90)
    second = block(
        at(TUESDAY, 9), at(TUESDAY, 9, 45), number=81, estimate_minutes=60, upcoming_minutes=90
    )
    found = block_conflicts(shown(blocks=(second, first), last=TUESDAY), TORONTO, EARLY)
    assert found[first.public_id] == ()
    assert [item.kind for item in found[second.public_id]] == [ConflictKind.OVER_ESTIMATE]


def test_only_open_blocks_from_today_on_are_checked() -> None:
    past = block(at(MONDAY, 9), at(MONDAY, 10))
    done = block(at(TUESDAY, 9), at(TUESDAY, 10), number=81, status=ActionStatus.COMPLETED)
    ahead = block(at(TUESDAY, 9, 30), at(TUESDAY, 10, 30), number=82)
    week = shown(blocks=(past, done, ahead), last=TUESDAY)
    found = block_conflicts(week, TORONTO, at(TUESDAY, 8))
    assert set(found) == {ahead.public_id}
    (overlap,) = found[ahead.public_id]
    assert overlap.kind is ConflictKind.BLOCK  # A done task's block still takes its time.
