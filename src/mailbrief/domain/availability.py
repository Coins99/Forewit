"""Free time: working hours less what is busy and what is already planned (C04, ADR 0029).

A day's free time is its working windows, in the owner's zone, less the busy events (the
owner's and Google's, each timed one widened by the buffer on both sides), less the blocks
of open tasks. An all-day busy event is a day off: it takes the whole day. Free events never
take time. Each instant counts once, however many entries cover it, and everything is
worked out locally from what the calendar holds; nothing here is ever sent anywhere.
"""

from collections.abc import Iterable, Sequence
from datetime import date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field

from mailbrief.domain.actions import ACTION_TITLE_MAX_CHARS, PUBLIC_ID_CHARS, ActionPriority
from mailbrief.domain.common import DomainModel, normalize_utc
from mailbrief.domain.events import CalendarEvent, CalendarRange, wall_clock
from mailbrief.domain.google_calendar import GoogleEvent
from mailbrief.domain.intervals import Interval, clip, minutes, subtract, union
from mailbrief.domain.work_blocks import suggested_minutes
from mailbrief.domain.working_hours import DAY_MINUTES, HOURS_STEP_MINUTES, WorkingHours

FREE_SEARCH_DAYS: Final = 14  # How far ahead the next free time is looked for.
UNSCHEDULED_LIMIT: Final = 50  # The most tasks the unscheduled panel lists.


def local_moment(day: date, minute: int, zone: ZoneInfo) -> datetime:
    """``minute`` past midnight of ``day`` in ``zone``, in UTC; DAY_MINUTES is the next
    midnight. A wall time a DST change skips reads as wall_clock() reads it."""
    if minute >= DAY_MINUTES:
        return normalize_utc(wall_clock(day + timedelta(days=1), time(0), zone))
    return normalize_utc(wall_clock(day, time(minute // 60, minute % 60), zone))


def day_span(day: date, zone: ZoneInfo) -> Interval:
    """The local day, from its midnight to the next."""
    return local_moment(day, 0, zone), local_moment(day, DAY_MINUTES, zone)


def working_spans(hours: WorkingHours, day: date, zone: ZoneInfo) -> tuple[Interval, ...]:
    """The day's working windows as UTC spans, merged; one a DST change leaves empty is
    dropped."""
    return union(
        (local_moment(day, window.start_minute, zone), local_moment(day, window.end_minute, zone))
        for window in hours.windows(day.weekday())
    )


def _all_day_span(first: date, last: date, zone: ZoneInfo) -> Interval:
    return local_moment(first, 0, zone), local_moment(last, DAY_MINUTES, zone)


def busy_spans(
    events: Iterable[CalendarEvent | GoogleEvent], zone: ZoneInfo, buffer_minutes: int = 0
) -> tuple[Interval, ...]:
    """What busy events take, merged: a timed one its time and the buffer either side, an
    all-day one its whole local days. Free events take nothing."""
    buffer = timedelta(minutes=buffer_minutes)
    spans: list[Interval] = []
    for event in events:
        if not event.busy:
            continue
        if event.all_day:
            assert event.start_date is not None and event.last_date is not None
            spans.append(_all_day_span(event.start_date, event.last_date, zone))
        else:
            assert event.starts_at_utc is not None and event.ends_at_utc is not None
            spans.append((event.starts_at_utc - buffer, event.ends_at_utc + buffer))
    return union(spans)


def day_off(events: Iterable[CalendarEvent | GoogleEvent], day: date) -> bool:
    """Whether an all-day busy event covers ``day``."""
    return any(
        event.busy
        and event.all_day
        and event.start_date is not None
        and event.last_date is not None
        and event.start_date <= day <= event.last_date
        for event in events
    )


class DayAvailability(DomainModel):
    """One local day's working time and what is left of it.

    ``busy_minutes`` is the working time busy events and their buffers take,
    ``scheduled_minutes`` what open tasks' blocks take of the rest, and ``free`` what is
    left. Without working hours (``hours_set`` false) every figure is 0.
    """

    day: date
    hours_set: bool
    day_off: bool
    working: tuple[Interval, ...] = ()
    free: tuple[Interval, ...] = ()
    working_minutes: int = Field(default=0, ge=0)
    busy_minutes: int = Field(default=0, ge=0)
    scheduled_minutes: int = Field(default=0, ge=0)
    free_minutes: int = Field(default=0, ge=0)


def day_availability(
    shown: CalendarRange, hours: WorkingHours, day: date, zone: ZoneInfo
) -> DayAvailability:
    """``day``'s availability from the entries ``shown`` holds, which should cover it."""
    events: Sequence[CalendarEvent | GoogleEvent] = (*shown.events, *shown.google_events)
    off = day_off(events, day)
    if not hours.is_set:
        return DayAvailability(day=day, hours_set=False, day_off=off)
    working = working_spans(hours, day, zone)
    busy = clip(busy_spans(events, zone, hours.buffer_minutes), *day_span(day, zone))
    after_busy = subtract(working, busy)
    blocks = [block.block.interval for block in shown.blocks if block.open]
    free = subtract(after_busy, blocks)
    worked, left, available = minutes(working), minutes(after_busy), minutes(free)
    return DayAvailability(
        day=day,
        hours_set=True,
        day_off=off,
        working=working,
        free=free,
        working_minutes=round(worked),
        busy_minutes=round(worked - left),
        scheduled_minutes=round(left - available),
        free_minutes=round(available),
    )


def availability(
    shown: CalendarRange, hours: WorkingHours, zone: ZoneInfo
) -> tuple[DayAvailability, ...]:
    """Every day of ``shown``, first to last."""
    count = (shown.last_day - shown.first_day).days + 1
    return tuple(
        day_availability(shown, hours, shown.first_day + timedelta(days=offset), zone)
        for offset in range(count)
    )


def step_up(moment: datetime, zone: ZoneInfo) -> datetime:
    """The first quarter hour of the wall clock in ``zone`` at or after ``moment``."""
    local = moment.astimezone(zone)
    extra = timedelta(
        minutes=local.minute % HOURS_STEP_MINUTES,
        seconds=local.second,
        microseconds=local.microsecond,
    )
    if not extra:
        return moment
    return moment - extra + timedelta(minutes=HOURS_STEP_MINUTES)


def next_free(
    days: Iterable[DayAvailability], length_minutes: int, after: datetime, zone: ZoneInfo
) -> datetime | None:
    """The first quarter hour at or after ``after`` that starts ``length_minutes`` of free
    time, in one stretch, on one of ``days``; None when there is none."""
    need = timedelta(minutes=length_minutes)
    for day in days:
        for start, end in day.free:
            begin = step_up(max(start, after), zone)
            if begin + need <= end:
                return begin
    return None


class UnscheduledTask(DomainModel):
    """An open task of the owner's that still needs time on the calendar.

    ``scheduled_minutes`` adds up its live blocks from today on. ``deadline_day`` is the
    local day of its deadline, if it has a dated one.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    action_public_id: str = Field(min_length=PUBLIC_ID_CHARS, max_length=PUBLIC_ID_CHARS)
    title: str = Field(min_length=1, max_length=ACTION_TITLE_MAX_CHARS, repr=False)
    estimate_minutes: int | None = Field(default=None, ge=1)
    scheduled_minutes: int = Field(default=0, ge=0)
    priority: ActionPriority | None = None
    target_date: date | None = None
    deadline_day: date | None = None

    @property
    def left_minutes(self) -> int | None:
        """What is left of the estimate; None without one."""
        if self.estimate_minutes is None:
            return None
        return max(0, self.estimate_minutes - self.scheduled_minutes)

    @property
    def suggested_minutes(self) -> int:
        """How long a new block for it starts out (ADR 0028)."""
        return suggested_minutes(self.estimate_minutes, self.scheduled_minutes)


class UnscheduledTasks(DomainModel):
    """The tasks the panel lists, and how many need time in all."""

    tasks: tuple[UnscheduledTask, ...] = ()
    total: int = Field(default=0, ge=0)
