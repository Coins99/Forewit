"""Free time (ADR 0029): working hours less busy events, buffers and open tasks' blocks."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from mailbrief.domain.actions import ActionStatus
from mailbrief.domain.availability import (
    UnscheduledTask,
    availability,
    busy_spans,
    day_availability,
    day_off,
    local_moment,
    next_free,
    step_up,
    working_spans,
)
from mailbrief.domain.events import CalendarEvent, CalendarRange, ScheduledBlock
from mailbrief.domain.google_calendar import GoogleEvent
from mailbrief.domain.work_blocks import WorkBlock
from mailbrief.domain.working_hours import WEEKDAYS, WorkingHours, WorkingWindow

TORONTO = ZoneInfo("America/Toronto")
MONDAY = date(2026, 11, 2)
TUESDAY = date(2026, 11, 3)
NOW = datetime(2026, 11, 2, 12, tzinfo=UTC)
ID = "5b1d7c2e-3f4a-4b6c-8d9e-0a1b2c3d4e{:02d}"


def at(day: date, hour: int, minute: int = 0, zone: ZoneInfo = TORONTO) -> datetime:
    return datetime.combine(day, time(hour, minute), zone).astimezone(UTC)


def hours(*windows: tuple[int, int], buffer: int = 0, days: range = range(5)) -> WorkingHours:
    week = tuple(
        tuple(WorkingWindow(start_minute=s * 60, end_minute=e * 60) for s, e in windows)
        if weekday in days
        else ()
        for weekday in range(WEEKDAYS)
    )
    return WorkingHours(days=week, buffer_minutes=buffer, revision=1)


def event(start: datetime, end: datetime, *, busy: bool = True, number: int = 1) -> CalendarEvent:
    return CalendarEvent(
        public_id=ID.format(number),
        title="Meeting",
        notes="",
        all_day=False,
        starts_at_utc=start,
        ends_at_utc=end,
        start_date=None,
        last_date=None,
        time_zone="America/Toronto",
        revision=1,
        created_at_utc=NOW,
        updated_at_utc=NOW,
        busy=busy,
    )


def all_day(first: date, last: date, *, busy: bool | None = None) -> CalendarEvent:
    return CalendarEvent(
        public_id=ID.format(50),
        title="Away",
        notes="",
        all_day=True,
        starts_at_utc=None,
        ends_at_utc=None,
        start_date=first,
        last_date=last,
        time_zone="America/Toronto",
        revision=1,
        created_at_utc=NOW,
        updated_at_utc=NOW,
        busy=busy,
    )


def google(start: datetime, end: datetime, *, busy: bool) -> GoogleEvent:
    return GoogleEvent(
        calendar_id="work@example.com",
        calendar_name="Work",
        event_id=f"g{start.hour}",
        title="Sync",
        all_day=False,
        starts_at_utc=start,
        ends_at_utc=end,
        busy=busy,
    )


def block(
    start: datetime, end: datetime, *, status: ActionStatus = ActionStatus.OPEN, number: int = 80
) -> ScheduledBlock:
    stored = WorkBlock(
        public_id=ID.format(number),
        action_public_id=ID.format(99),
        starts_at_utc=start,
        ends_at_utc=end,
        time_zone="America/Toronto",
        revision=1,
        created_at_utc=NOW,
        updated_at_utc=NOW,
    )
    return ScheduledBlock(block=stored, action_title="Draft report", action_status=status)


def shown(
    *,
    events: tuple[CalendarEvent, ...] = (),
    google_events: tuple[GoogleEvent, ...] = (),
    blocks: tuple[ScheduledBlock, ...] = (),
    first: date = MONDAY,
    last: date = MONDAY,
) -> CalendarRange:
    return CalendarRange(
        first_day=first,
        last_day=last,
        events=events,
        markers=(),
        google_events=google_events,
        blocks=blocks,
    )


def test_working_spans_are_wall_clock_times_through_dst() -> None:
    week = hours((1, 3), (22, 24), days=range(7))
    spring = date(2026, 3, 8)  # 02:00 jumps to 03:00 in Toronto.
    autumn = date(2026, 11, 1)  # 02:00 falls back to 01:00.
    assert working_spans(week, MONDAY, TORONTO) == (
        (at(MONDAY, 1), at(MONDAY, 3)),
        (at(MONDAY, 22), at(TUESDAY, 0)),
    )
    (early, _late) = working_spans(week, spring, TORONTO)
    assert early[1] - early[0] == timedelta(hours=1)
    (early, _late) = working_spans(week, autumn, TORONTO)
    assert early[1] - early[0] == timedelta(hours=3)
    assert local_moment(MONDAY, 1440, TORONTO) == at(TUESDAY, 0)


def test_free_time_takes_busy_events_buffers_and_open_blocks_once() -> None:
    week = hours((9, 12), (13, 17), buffer=15)
    day = shown(
        events=(
            event(at(MONDAY, 10), at(MONDAY, 11)),
            event(at(MONDAY, 10, 30), at(MONDAY, 11), number=2),  # Overlaps: counted once.
            event(at(MONDAY, 12), at(MONDAY, 13), busy=False, number=3),  # Free: no time.
        ),
        google_events=(
            google(at(MONDAY, 14), at(MONDAY, 15), busy=True),
            google(at(MONDAY, 15), at(MONDAY, 16), busy=False),
        ),
        blocks=(
            block(at(MONDAY, 16), at(MONDAY, 17)),
            block(at(MONDAY, 11, 30), at(MONDAY, 12), status=ActionStatus.COMPLETED, number=81),
        ),
    )
    found = day_availability(day, week, MONDAY, TORONTO)
    assert found.hours_set and not found.day_off
    assert found.free == (
        (at(MONDAY, 9), at(MONDAY, 9, 45)),
        (at(MONDAY, 11, 15), at(MONDAY, 12)),
        (at(MONDAY, 13), at(MONDAY, 13, 45)),
        (at(MONDAY, 15, 15), at(MONDAY, 16)),
    )
    assert (found.working_minutes, found.busy_minutes) == (420, 180)
    assert (found.scheduled_minutes, found.free_minutes) == (60, 180)


def test_an_all_day_busy_event_is_a_day_off_and_a_free_one_is_nothing() -> None:
    week = hours((9, 17))
    off = shown(events=(all_day(MONDAY, TUESDAY, busy=True),), last=TUESDAY)
    monday, tuesday = availability(off, week, TORONTO)
    assert monday.day_off and tuesday.day_off
    assert monday.free == () and monday.busy_minutes == monday.working_minutes == 480
    holiday = shown(events=(all_day(MONDAY, MONDAY),))  # Free, as all-day events are.
    assert not day_off(holiday.events, MONDAY)
    assert day_availability(holiday, week, MONDAY, TORONTO).free_minutes == 480


def test_without_working_hours_there_is_no_free_time_but_days_off_still_show() -> None:
    off = shown(events=(all_day(MONDAY, MONDAY, busy=True),))
    found = day_availability(off, WorkingHours.unset(), MONDAY, TORONTO)
    assert not found.hours_set and found.day_off
    assert found.free == () and found.working_minutes == 0
    weekend = day_availability(shown(), hours((9, 17)), date(2026, 11, 7), TORONTO)
    assert weekend.hours_set and weekend.free == () and weekend.working_minutes == 0


def test_busy_spans_widen_timed_events_by_the_buffer() -> None:
    spans = busy_spans(
        (event(at(MONDAY, 10), at(MONDAY, 11)), all_day(TUESDAY, TUESDAY, busy=True)),
        TORONTO,
        buffer_minutes=10,
    )
    assert spans == (
        (at(MONDAY, 9, 50), at(MONDAY, 11, 10)),
        (at(TUESDAY, 0), at(date(2026, 11, 4), 0)),
    )


def test_next_free_starts_on_a_quarter_hour_and_needs_one_stretch() -> None:
    week = hours((9, 12), (13, 17))
    days = availability(
        shown(
            events=(event(at(MONDAY, 10), at(MONDAY, 11)),),
            first=MONDAY,
            last=TUESDAY,
        ),
        week,
        TORONTO,
    )
    assert next_free(days, 60, at(MONDAY, 8), TORONTO) == at(MONDAY, 9)
    assert next_free(days, 60, at(MONDAY, 9, 7), TORONTO) == at(MONDAY, 11)
    assert next_free(days, 120, at(MONDAY, 9), TORONTO) == at(MONDAY, 13)
    assert next_free(days, 240, at(MONDAY, 13, 1), TORONTO) == at(TUESDAY, 13)
    assert next_free(days, 300, at(MONDAY, 9), TORONTO) is None


def test_step_up_follows_the_local_clock() -> None:
    kathmandu = ZoneInfo("Asia/Kathmandu")  # UTC+5:45.
    moment = datetime(2026, 11, 2, 3, 20, 30, tzinfo=UTC)  # 09:05:30 there.
    assert step_up(moment, kathmandu).astimezone(kathmandu).time() == time(9, 15)
    exact = at(MONDAY, 9, 45)
    assert step_up(exact, TORONTO) == exact


def test_an_unscheduled_task_says_what_is_left_and_how_long_to_start() -> None:
    task = UnscheduledTask(
        action_public_id=ID.format(1),
        title="Draft report",
        estimate_minutes=180,
        scheduled_minutes=60,
    )
    assert task.left_minutes == 120 and task.suggested_minutes == 120
    loose = UnscheduledTask(action_public_id=ID.format(2), title="Call the bank")
    assert loose.left_minutes is None and loose.suggested_minutes == 60
    over = task.model_copy(update={"scheduled_minutes": 240})
    assert over.left_minutes == 0
