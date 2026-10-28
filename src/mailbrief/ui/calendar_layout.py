"""Where entries go on the Calendar's grids: arithmetic only, no Qt.

- A month grid shows six weeks from the Monday on or before the 1st (``month_days``), the
  most the Calendar reads at once (42 days).
- In the week grid, a timed event shorter than a day is a block in the hours of each day it
  touches (``timed_part``); blocks that overlap share their day's width in columns
  (``pack_columns``), as calendar apps lay them out.
- All-day events, events of a day or more, and the actions' deadlines and targets are bars
  across the days they cover (``bars``), each in the first lane free on all of them
  (``pack_lanes``): the week grid's top strip and each week of the month grid.
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

from mailbrief.domain.events import ActionMarker, CalendarEvent, CalendarRange, MarkerKind
from mailbrief.domain.google_calendar import GoogleEvent
from mailbrief.ui.who_text import in_sentence, who_text

Entry = CalendarEvent | GoogleEvent | ActionMarker
Event = CalendarEvent | GoogleEvent


class CalendarView(StrEnum):
    """How the Calendar page shows its days; the owner's choice is kept on this device."""

    WEEK = "week"  # A week of hours, as calendar apps show it.
    MONTH = "month"  # Six weeks of days around a month.
    LIST = "list"  # A week, day by day, as rows.


def clock(moment: datetime, zone: ZoneInfo) -> str:
    """A moment's time of day in ``zone``: "09:30"."""
    return f"{moment.astimezone(zone):%H:%M}"


def marker_label(marker: ActionMarker, zone: ZoneInfo) -> str:
    """ "Deadline", "Deadline 17:00", "Target" or, for an action the owner waits for,
    "Follow up" (B08)."""
    if marker.kind is MarkerKind.TARGET:
        return "Follow up" if marker.waiting else "Target"
    if marker.due_at_utc is None:
        return "Deadline"
    return f"Deadline {clock(marker.due_at_utc, zone)}"


def marker_note(marker: ActionMarker, zone: ZoneInfo) -> str:
    """The label and who it is with: "Follow up · waiting on Alex" (or "waiting for others"
    without a name), "Deadline 17:00 · for Sam", or the label alone."""
    who = who_text(marker.waiting, marker.person, unnamed="Waiting for others")
    label = marker_label(marker, zone)
    return label if who is None else f"{label} · {in_sentence(who)}"


def marker_text(marker: ActionMarker, zone: ZoneInfo) -> str:
    """ "Deadline · Pay the invoice", "Deadline 17:00 · …", "Target · …" or, for an action
    the owner waits for, "Follow up · …" (B08); and who it is with: "waiting on Alex" (or
    "waiting for others" without a name) or "for Sam"."""
    who = who_text(marker.waiting, marker.person, unnamed="Waiting for others")
    text = f"{marker_label(marker, zone)} · {marker.action_title}"
    return text if who is None else f"{text} · {in_sentence(who)}"


def entry_key(entry: Entry) -> tuple[str, ...]:
    """What stays the same about an entry from one read of the days to the next."""
    if isinstance(entry, CalendarEvent):
        return ("event", entry.public_id)
    if isinstance(entry, GoogleEvent):
        return ("google", entry.calendar_id, entry.event_id)
    return ("marker", entry.kind.value, entry.action_public_id, entry.day.isoformat())


GRID_WEEKS: Final = 6
WEEK_DAYS: Final = 7
DAY_MINUTES: Final = 24 * 60
# A block never shows shorter than this, so a five-minute event can still be read and hit.
MIN_BLOCK_MINUTES: Final = 20
# A timed event this long or longer is a bar across its days, not blocks in its hours.
BAR_FROM: Final = timedelta(hours=24)


def month_start(day: date) -> date:
    """The 1st of ``day``'s month."""
    return day.replace(day=1)


def add_months(month: date, count: int) -> date:
    """The 1st of the month ``count`` months after ``month``'s (before it when negative)."""
    index = month.year * 12 + (month.month - 1) + count
    return date(index // 12, index % 12 + 1, 1)


def month_days(month: date) -> tuple[date, date]:
    """The first and last days of ``month``'s grid: six weeks from the Monday on or before
    its 1st, so every month fits and the grid never changes height."""
    first = month_start(month)
    first -= timedelta(days=first.weekday())
    return first, first + timedelta(days=GRID_WEEKS * WEEK_DAYS - 1)


def shown_month(first_day: date) -> date:
    """The month a grid starting on ``first_day`` shows: the one its first week reaches,
    since the grid starts no more than six days before the 1st."""
    return month_start(first_day + timedelta(days=WEEK_DAYS - 1))


@dataclass(frozen=True)
class Part:
    """A timed event's part of one day, in wall-clock minutes from that day's midnight in
    the owner's zone, placed in ``column`` of ``columns`` beside the parts it overlaps."""

    event: Event
    day: date
    start: int
    end: int
    column: int = 0
    columns: int = 1


def is_bar(event: Event) -> bool:
    """Whether the event is drawn as a bar across days rather than a block of hours."""
    if event.all_day:
        return True
    assert event.starts_at_utc is not None and event.ends_at_utc is not None
    return event.ends_at_utc - event.starts_at_utc >= BAR_FROM


def _minutes(moment: datetime) -> int:
    return moment.hour * 60 + moment.minute


def timed_part(event: Event, day: date, zone: ZoneInfo) -> Part | None:
    """The part of the timed ``event`` on ``day``, or None when it doesn't touch the day.
    A part that runs past midnight ends at 24:00, one that began the day before starts at
    00:00; an event ending at midnight belongs to the day before (``days``). A part is at
    least MIN_BLOCK_MINUTES long, moved earlier when it would pass the day's end."""
    if event.all_day:
        return None
    assert event.starts_at_utc is not None and event.ends_at_utc is not None
    first, last = event.days(zone)
    if not first <= day <= last:
        return None
    start_at = event.starts_at_utc.astimezone(zone)
    end_at = event.ends_at_utc.astimezone(zone)
    start = _minutes(start_at) if start_at.date() == day else 0
    end = _minutes(end_at) if end_at.date() == day else DAY_MINUTES
    end = max(end, start + MIN_BLOCK_MINUTES)  # Short, or a clock that went back (DST).
    if end > DAY_MINUTES:
        end = DAY_MINUTES
        start = min(start, DAY_MINUTES - MIN_BLOCK_MINUTES)
    return Part(event, day, start, end)


def pack_columns(parts: Sequence[Part]) -> list[Part]:
    """Overlapping parts side by side. Parts that overlap, directly or through others, form
    a cluster that shares the day's width in as many columns as it ever needs at once; each
    part takes the first column free at its start. Earlier parts come first; at the same
    start, the longer."""
    ordered = sorted(parts, key=lambda part: (part.start, -part.end))
    placed: list[Part] = []
    cluster: list[Part] = []
    ends: list[int] = []  # Each column's latest end in the cluster.
    reach = -1  # The cluster's latest end.

    def close() -> None:
        placed.extend(replace(part, columns=len(ends)) for part in cluster)

    for part in ordered:
        if cluster and part.start >= reach:
            close()
            cluster, ends, reach = [], [], -1
        column = next((number for number, end in enumerate(ends) if end <= part.start), None)
        if column is None:
            column = len(ends)
            ends.append(part.end)
        else:
            ends[column] = part.end
        cluster.append(replace(part, column=column))
        reach = max(reach, part.end)
    if cluster:
        close()
    return placed


@dataclass(frozen=True)
class Bar:
    """An entry across days ``first`` to ``last`` (indexes into the range's days, clipped to
    it), in ``lane`` of its stack."""

    entry: Entry
    first: int
    last: int
    lane: int = 0
    starts_before: bool = False  # It began before the first day shown.
    ends_after: bool = False  # It goes on past the last day shown.


def _bar_order(entry: Entry) -> int:
    """All-day events first, then the actions' dates, then long timed events."""
    if isinstance(entry, ActionMarker):
        return 1
    return 0 if entry.all_day else 2


def bars(shown: CalendarRange, first_day: date, last_day: date, zone: ZoneInfo) -> list[Bar]:
    """The bars in ``first_day`` to ``last_day``: all-day events and events of a day or more
    (the owner's, then Google's), and the actions' deadlines and targets on their day."""
    found: list[Bar] = []

    def add(entry: Entry, first: date, last: date) -> None:
        if last < first_day or first > last_day:
            return
        found.append(
            Bar(
                entry,
                (max(first, first_day) - first_day).days,
                (min(last, last_day) - first_day).days,
                starts_before=first < first_day,
                ends_after=last > last_day,
            )
        )

    events: list[Event] = [*shown.events, *shown.google_events]
    for event in events:
        if is_bar(event):
            add(event, *event.days(zone))
    for marker in shown.markers:
        add(marker, marker.day, marker.day)
    return found


def pack_lanes(found: Sequence[Bar]) -> list[Bar]:
    """Each bar in the first lane free on every day it covers. Bars that start earlier come
    first; at the same start, longer ones, then all-day events before the actions' dates
    and long timed events, keeping the given order after that."""
    ordered = sorted(
        enumerate(found),
        key=lambda pair: (
            pair[1].first,
            -(pair[1].last - pair[1].first),
            _bar_order(pair[1].entry),
            pair[0],
        ),
    )
    lanes: list[list[tuple[int, int]]] = []
    placed: list[Bar] = []
    for _index, bar in ordered:
        lane = next(
            (
                number
                for number, taken in enumerate(lanes)
                if all(bar.last < first or bar.first > last for first, last in taken)
            ),
            None,
        )
        if lane is None:
            lane = len(lanes)
            lanes.append([])
        lanes[lane].append((bar.first, bar.last))
        placed.append(replace(bar, lane=lane))
    return placed


def day_parts(shown: CalendarRange, day: date, zone: ZoneInfo) -> list[Part]:
    """The day's timed blocks, packed into columns: the timed events shorter than a day that
    touch it, the owner's and Google's."""
    events: list[Event] = [*shown.events, *shown.google_events]
    parts = [
        part
        for event in events
        if not is_bar(event) and (part := timed_part(event, day, zone)) is not None
    ]
    return pack_columns(parts)
