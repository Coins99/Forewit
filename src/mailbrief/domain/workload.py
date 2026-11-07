"""Workload and conflicts (C05, ADR 0030): what each day holds against the time it has.

A read model over what the calendar already read: nothing here is stored, changes a task,
a block or an event, or is ever sent anywhere. Each figure keeps its parts so it can be
explained.

- **Available** is a day's working time less its busy time (busy events widened by the
  buffer; a day off takes the whole day), as ``domain.availability`` works it out. It is
  unknown when no working hours are set.
- **Scheduled** is the open tasks' blocks clipped to the day: their total length, and the
  unique minutes they cover, each instant once. With working hours set, only their minutes
  inside the working windows count toward planned: time outside them uses none of the
  working time (it is a conflict instead).
- **Focus without time** is the known estimates of the day's focus tasks that have no block
  that day, with a count of those that have no estimate. A focus task with a block that day
  counts through its blocks only.
- **Planned** is the unique scheduled minutes (inside the working windows, when set) plus
  focus without time. A day is **overfilled** when planned is more than available.
- A day is **provisional** when its figures may be wrong: Google Calendar is on and its
  cache misses the day or was last filled more than PROVISIONAL_AFTER ago, or a focus task
  has no estimate. A provisional day is never shown as fitting.
- Past days are shown but never flagged: only today and later can be overfilled,
  provisional or in conflict.

A block's **conflicts** say what it clashes with: a busy event it overlaps, or one whose
buffer it sits in; another block; a day off; the time outside the working hours; its task's
deadline; or more time set aside for its task than the task's estimate. Free events never
conflict. Saving a block warns from the same definition (``WorkBlockService``).
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Final
from zoneinfo import ZoneInfo

from pydantic import Field

from mailbrief.domain.availability import (
    DayAvailability,
    availability,
    day_span,
    local_moment,
    working_spans,
)
from mailbrief.domain.common import DomainModel, normalize_utc
from mailbrief.domain.events import CalendarEvent, CalendarRange, ScheduledBlock
from mailbrief.domain.google_calendar import GoogleCoverage, GoogleEvent
from mailbrief.domain.intervals import Interval, clip, minutes, overlaps, subtract, union
from mailbrief.domain.work_blocks import scheduled_time
from mailbrief.domain.working_hours import WorkingHours

# Older than this, Google's cached busy times may have changed: the day is provisional.
# Apart from the 15 minutes after which a sync is due (services.google_calendar).
PROVISIONAL_AFTER: Final = timedelta(hours=24)


class ConflictKind(StrEnum):
    """What a block clashes with."""

    BUSY = "busy"  # It overlaps a busy event.
    BUFFER = "buffer"  # It sits in the buffer kept clear around a busy event.
    BLOCK = "block"  # It overlaps another block.
    DAY_OFF = "day_off"  # It is on a day an all-day busy event takes.
    OUTSIDE_HOURS = "outside_hours"  # Some of it falls outside the working hours.
    AFTER_DEADLINE = "after_deadline"  # It ends after its task's deadline.
    OVER_ESTIMATE = "over_estimate"  # Its task has more time set aside than its estimate.


class Conflict(DomainModel):
    """One clash of a block, with the entry it clashes with when there is one (``label``:
    an event's title, a Google event's title and calendar, or "Task · title"). ``at`` orders
    a block's conflicts: the other entry's start, or the block's own."""

    kind: ConflictKind
    at: datetime
    label: str | None = Field(default=None, repr=False)


class ProvisionalReason(StrEnum):
    """Why a day's figures may be wrong."""

    GOOGLE_MISSING = "google_missing"  # Google Calendar is on, and the cache misses the day.
    GOOGLE_OLD = "google_old"  # The cache holds the day, but was filled too long ago.
    NO_ESTIMATE = "no_estimate"  # A focus task has no estimate.


class DayWorkload(DomainModel):
    """One local day's load against its time; see the module's definitions."""

    day: date
    availability: DayAvailability
    scheduled_minutes: int = Field(default=0, ge=0)  # Unique minutes the day's blocks cover.
    scheduled_total_minutes: int = Field(default=0, ge=0)  # Their lengths added up.
    # Of the unique minutes, those inside the working windows; all of them without hours.
    scheduled_working_minutes: int = Field(default=0, ge=0)
    focus_minutes: int = Field(default=0, ge=0)  # Focus tasks without a block that day.
    focus_tasks: int = Field(default=0, ge=0)  # How many those are, estimated or not.
    focus_unestimated: int = Field(default=0, ge=0)
    flagged: bool = False  # Today or later: flags apply.
    provisional_reasons: tuple[ProvisionalReason, ...] = ()
    conflicted_blocks: int = Field(default=0, ge=0)

    @property
    def available_minutes(self) -> int | None:
        """Working time less busy time; None without working hours."""
        found = self.availability
        return found.working_minutes - found.busy_minutes if found.hours_set else None

    @property
    def planned_minutes(self) -> int:
        return self.scheduled_working_minutes + self.focus_minutes

    @property
    def outside_minutes(self) -> int:
        """The day's block minutes outside its working windows, which planned leaves out."""
        return self.scheduled_minutes - self.scheduled_working_minutes

    @property
    def over_minutes(self) -> int:
        """How far planned goes past available; 0 when it fits, unflagged or unknown."""
        available = self.available_minutes
        if not self.flagged or available is None:
            return 0
        return max(0, self.planned_minutes - available)

    @property
    def overfilled(self) -> bool:
        return self.over_minutes > 0

    @property
    def provisional(self) -> bool:
        return bool(self.provisional_reasons)


def owner_today(now: datetime, zone: ZoneInfo) -> date:
    return normalize_utc(now).astimezone(zone).date()


def google_reason(
    coverage: GoogleCoverage | None, day: date, now: datetime
) -> ProvisionalReason | None:
    """Why Google's busy times for ``day`` may be wrong, when Google Calendar is on."""
    if coverage is None or not coverage.covers(day, day):
        return ProvisionalReason.GOOGLE_MISSING
    if normalize_utc(now) - coverage.synced_at_utc > PROVISIONAL_AFTER:
        return ProvisionalReason.GOOGLE_OLD
    return None


def _blocks_on(blocks: Sequence[ScheduledBlock], span: Interval) -> list[ScheduledBlock]:
    return [block for block in blocks if overlaps(block.block.interval, span)]


def workload(
    shown: CalendarRange,
    zone: ZoneInfo,
    now: datetime,
    *,
    google_on: bool,
    conflicts: Mapping[str, tuple[Conflict, ...]] | None = None,
) -> tuple[DayWorkload, ...]:
    """Every day of ``shown``, first to last. ``google_on``: Google Calendar is connected
    and allowed, so a day its cache misses is provisional. ``conflicts`` is
    block_conflicts() for the same range and moment, when the caller already has it."""
    today = owner_today(now, zone)
    open_blocks = [block for block in shown.blocks if block.open]
    found_conflicts = block_conflicts(shown, zone, now) if conflicts is None else conflicts
    conflicted = {found for found, clashes in found_conflicts.items() if clashes}
    days: list[DayWorkload] = []
    for found in availability(shown, shown.hours, zone):
        span = day_span(found.day, zone)
        on_day = _blocks_on(open_blocks, span)
        scheduled = scheduled_time([block.block for block in on_day], *span)
        spans = union(block.block.interval for block in on_day)
        working = (
            round(minutes(part for window in found.working for part in clip(spans, *window)))
            if found.hours_set
            else scheduled.unique_minutes
        )
        with_time = {block.action_public_id for block in on_day}
        focus = [
            task for task in shown.focus_on(found.day) if task.action_public_id not in with_time
        ]
        unestimated = sum(task.estimate_minutes is None for task in focus)
        flagged = found.day >= today
        reasons: list[ProvisionalReason] = []
        if flagged:
            google = google_reason(shown.google_coverage, found.day, now) if google_on else None
            if google is not None:
                reasons.append(google)
            if unestimated:
                reasons.append(ProvisionalReason.NO_ESTIMATE)
        days.append(
            DayWorkload(
                day=found.day,
                availability=found,
                scheduled_minutes=scheduled.unique_minutes,
                scheduled_total_minutes=scheduled.total_minutes,
                scheduled_working_minutes=min(working, scheduled.unique_minutes),
                focus_minutes=sum(task.estimate_minutes or 0 for task in focus),
                focus_tasks=len(focus),
                focus_unestimated=unestimated,
                flagged=flagged,
                provisional_reasons=tuple(reasons),
                conflicted_blocks=sum(block.public_id in conflicted for block in on_day),
            )
        )
    return tuple(days)


def conflicts_for(
    block: ScheduledBlock,
    zone: ZoneInfo,
    *,
    events: Sequence[CalendarEvent | GoogleEvent],
    blocks: Sequence[ScheduledBlock],
    hours: WorkingHours,
) -> tuple[Conflict, ...]:
    """What ``block`` clashes with among ``events`` and the other ``blocks``, in the order of
    what it clashes with, then its own. The caller passes the entries near the block; a task's
    time set aside counts only when ``block.upcoming_minutes`` was read."""
    start, end = block.block.interval
    first_day, last_day = block.days(zone)
    before = timedelta(minutes=hours.before_minutes)
    after = timedelta(minutes=hours.after_minutes)
    found: list[Conflict] = []
    for event in events:
        if not event.busy:
            continue
        label = (
            f"{event.title} ({event.calendar_name})"
            if isinstance(event, GoogleEvent)
            else event.title
        )
        if event.all_day:
            assert event.start_date is not None and event.last_date is not None
            if event.start_date <= last_day and event.last_date >= first_day:
                at = local_moment(max(event.start_date, first_day), 0, zone)
                found.append(Conflict(kind=ConflictKind.DAY_OFF, at=at, label=label))
            continue
        assert event.starts_at_utc is not None and event.ends_at_utc is not None
        span = (event.starts_at_utc, event.ends_at_utc)
        if overlaps((start, end), span):
            found.append(Conflict(kind=ConflictKind.BUSY, at=span[0], label=label))
        elif (before or after) and overlaps((start, end), (span[0] - before, span[1] + after)):
            found.append(Conflict(kind=ConflictKind.BUFFER, at=span[0], label=label))
    for other in blocks:
        if other.public_id != block.public_id and overlaps((start, end), other.block.interval):
            label = f"Task · {other.title}"
            found.append(Conflict(kind=ConflictKind.BLOCK, at=other.starts_at_utc, label=label))
    found.sort(key=lambda conflict: conflict.at)
    if hours.is_set:
        working = [
            span
            for offset in range((last_day - first_day).days + 1)
            for span in working_spans(hours, first_day + timedelta(days=offset), zone)
        ]
        if subtract(((start, end),), working):
            found.append(Conflict(kind=ConflictKind.OUTSIDE_HOURS, at=start))
    if block.due_at_utc is not None and end > block.due_at_utc:
        found.append(Conflict(kind=ConflictKind.AFTER_DEADLINE, at=start))
    estimate = block.estimate_minutes
    if estimate is not None and block.upcoming_minutes > estimate:
        found.append(Conflict(kind=ConflictKind.OVER_ESTIMATE, at=start))
    return tuple(found)


def block_conflicts(
    shown: CalendarRange, zone: ZoneInfo, now: datetime
) -> dict[str, tuple[Conflict, ...]]:
    """Each flagged block's conflicts, by its public ID: the blocks of open tasks that end
    after the start of the owner's today. A done task's block and a past one are never in
    conflict."""
    since = local_moment(owner_today(now, zone), 0, zone)
    events: list[CalendarEvent | GoogleEvent] = [*shown.events, *shown.google_events]
    checked = [block for block in shown.blocks if block.open and block.ends_at_utc > since]
    found = {
        block.public_id: conflicts_for(
            block, zone, events=events, blocks=shown.blocks, hours=shown.hours
        )
        for block in checked
    }
    # More time than the estimate is the task's, not each block's: only its latest block
    # shown carries it, so one mark says which task to trim.
    latest: dict[str, ScheduledBlock] = {}
    for block in checked:
        if (
            block.action_public_id not in latest
            or block.sort_key() > latest[block.action_public_id].sort_key()
        ):
            latest[block.action_public_id] = block
    for block in checked:
        if latest[block.action_public_id] is not block:
            found[block.public_id] = tuple(
                item
                for item in found[block.public_id]
                if item.kind is not ConflictKind.OVER_ESTIMATE
            )
    return found
