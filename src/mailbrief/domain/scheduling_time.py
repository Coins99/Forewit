"""Explicit wall-clock input and DST-safe working windows; existing events stay unchanged."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

from mailbrief.domain.planning_intervals import UtcInterval, union_intervals

MAX_PLANNING_DAYS: Final = 42
_HOUR: Final = timedelta(hours=1)
_MICROSECOND: Final = timedelta(microseconds=1)


class WallTimeChoice(StrEnum):
    EARLIER = "earlier"
    LATER = "later"


class NonexistentWallTimeError(ValueError):
    """The requested wall-clock time does not occur in the selected zone."""


class AmbiguousWallTimeError(ValueError):
    """A repeated wall-clock time needs an explicit earlier/later choice."""


def wall_time_options(day: date, clock: time, zone: ZoneInfo) -> tuple[datetime, ...]:
    """Return zero, one or two valid UTC instants in chronological order."""
    if clock.tzinfo is not None:
        raise ValueError("enter a wall-clock time without a time zone")
    wall = datetime.combine(day, clock).replace(fold=0)
    candidates = set()
    for fold in (0, 1):
        candidate = wall.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if candidate.astimezone(zone).replace(tzinfo=None, fold=0) == wall:
            candidates.add(candidate)
    return tuple(sorted(candidates))


def resolve_wall_time(
    day: date,
    clock: time,
    zone: ZoneInfo,
    *,
    choice: WallTimeChoice | None = None,
) -> datetime:
    """Never normalize a skipped input or silently choose one repeated instant."""
    options = wall_time_options(day, clock, zone)
    if not options:
        raise NonexistentWallTimeError("this time does not occur; choose another time")
    if len(options) > 1 and choice is None:
        raise AmbiguousWallTimeError("this time occurs twice; choose earlier or later")
    return options[-1] if choice is WallTimeChoice.LATER else options[0]


def planning_dates(first: date, last: date) -> tuple[date, ...]:
    """Inclusive date range for one bounded read/command, never a lifetime limit."""
    days = (last - first).days + 1
    if not 1 <= days <= MAX_PLANNING_DAYS:
        raise ValueError("choose between one and 42 local dates")
    return tuple(first + timedelta(days=index) for index in range(days))


def _offset(instant: datetime, zone: ZoneInfo) -> timedelta:
    offset = instant.astimezone(zone).utcoffset()
    assert offset is not None
    return offset


def _offset_periods(
    start: datetime, end: datetime, zone: ZoneInfo
) -> Iterator[tuple[datetime, datetime, timedelta]]:
    """Split UTC at IANA offset transitions, retaining sub-minute precision.

    IANA transitions are separated by more than an hour. Hourly probes locate
    their containing span; binary search finds the exact transition, including
    historical second offsets. No private zoneinfo API or network is needed.
    """
    cursor = period_start = start
    offset = _offset(cursor, zone)
    while cursor < end:
        probe = min(cursor + _HOUR, end)
        if _offset(probe, zone) == offset:
            cursor = probe
            continue
        before, after = cursor, probe
        while after - before > _MICROSECOND:
            middle = before + (after - before) // 2
            if _offset(middle, zone) == offset:
                before = middle
            else:
                after = middle
        yield period_start, after, offset
        cursor = period_start = after
        offset = _offset(after, zone)
    if period_start < end:
        yield period_start, end, offset


def working_window_intervals(
    day: date, start_minute: int, end_minute: int, zone: ZoneInfo
) -> tuple[UtcInterval, ...]:
    """Exact inverse of local wall-time membership for one same-day working window.

    Repeated portions can produce separate intervals. Skipped portions add no
    capacity, including an entire skipped local day. Offsets are applied only
    within their actual UTC validity period, never across a transition.
    """
    if not 0 <= start_minute < end_minute <= 1440:
        raise ValueError("a working window needs increasing minutes within one day")
    try:
        midnight = datetime.combine(day, time.min, UTC)
        wall_start = midnight + timedelta(minutes=start_minute)
        wall_end = midnight + timedelta(minutes=end_minute)
        # Valid UTC offsets are strictly within +/-24 hours. This encloses every
        # possible inverse, including date-line changes, without choosing a fold.
        envelope_start = wall_start - timedelta(days=1)
        envelope_end = wall_end + timedelta(days=1)
        result: list[UtcInterval] = []
        for left, right, offset in _offset_periods(envelope_start, envelope_end, zone):
            start, end = max(left, wall_start - offset), min(right, wall_end - offset)
            if start < end:
                result.append(UtcInterval(start=start, end=end))
    except OverflowError:
        raise ValueError("the working date is outside the supported time range") from None
    return union_intervals(result)


def all_day_intervals(first: date, last: date, zone: ZoneInfo) -> tuple[UtcInterval, ...]:
    """Convert inclusive stored local dates to exclusive UTC spans, without mutation."""
    return union_intervals(
        interval
        for day in planning_dates(first, last)
        for interval in working_window_intervals(day, 0, 1440, zone)
    )
