"""The owner's calendar: events, and the open actions' dates beside them (ADR 0018).

Nothing here reads or sends email or calls a provider, and nothing changes an action:
deadlines and target dates are only read, to be shown as markers. Every mutating method
reads the clock once, commits once and rolls back on failure. Errors carry static messages,
and nothing logs event text.
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.analysis import ActionOwnership, DeadlinePrecision
from mailbrief.domain.common import normalize_utc
from mailbrief.domain.events import (
    ActionMarker,
    AppliedBatch,
    AppliedChange,
    CalendarEvent,
    CalendarRange,
    ChangeKind,
    EventChange,
    EventEdit,
    MarkerKind,
)
from mailbrief.domain.google_calendar import GoogleEvent
from mailbrief.services.calendar import day_window
from mailbrief.storage.events import EventRepository, apply_edit, event_from_row
from mailbrief.storage.google_calendar import GoogleCalendarRepository
from mailbrief.storage.tables import ActionTable, CalendarEventTable

MAX_RANGE_DAYS: Final = 42
_NOT_FOUND: Final = "That event was not found."
_STALE: Final = "The event changed since it was loaded; reload it and try again."
_BAD_RANGE: Final = "Choose a range of 1 to 42 days."
_UNDO: Final = "Those events changed since; they can no longer be undone together."
_NO_CHANGES: Final = "Choose at least one change."


class EventNotFoundError(LookupError):
    """No live event has that public ID."""


class EventConflictError(ValueError):
    """The event changed since it was read."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_public_id() -> str:
    return str(uuid.uuid4())


def _touch(row: CalendarEventTable, now: datetime) -> None:
    row.revision += 1
    row.updated_at_utc = now


def markers_for(
    actions: Sequence[ActionTable], first_day: date, last_day: date, zone: ZoneInfo
) -> tuple[ActionMarker, ...]:
    """The target dates and deadlines of these actions that fall in the range.

    A target date and a deadline day are shown on their own day in every zone. A deadline
    with a time is shown on its local day in ``zone``. An unresolved deadline, which has
    only words, is not shown.
    """
    found: list[ActionMarker] = []
    for action in actions:
        waiting = action.ownership == ActionOwnership.WAITING_FOR.value
        if action.target_date is not None and first_day <= action.target_date <= last_day:
            found.append(
                ActionMarker(
                    kind=MarkerKind.TARGET,
                    day=action.target_date,
                    action_public_id=action.public_id,
                    action_title=action.title,
                    waiting=waiting,
                )
            )
        precision = DeadlinePrecision(action.deadline_precision)
        due: datetime | None = None
        if precision is DeadlinePrecision.DATETIME and action.deadline_at_utc is not None:
            due = normalize_utc(action.deadline_at_utc)
            day = due.astimezone(zone).date()
        elif precision is DeadlinePrecision.DATE and action.deadline_date is not None:
            day = action.deadline_date
        else:
            continue
        if first_day <= day <= last_day:
            found.append(
                ActionMarker(
                    kind=MarkerKind.DEADLINE,
                    day=day,
                    due_at_utc=due,
                    action_public_id=action.public_id,
                    action_title=action.title,
                    waiting=waiting,
                )
            )
    return tuple(sorted(found, key=ActionMarker.sort_key))


class EventService:
    """The owner's events.

    Every mutating method reads the clock once, commits once and rolls back on failure. A
    method taking ``expected_revision`` raises EventConflictError when the event's revision
    differs, so a stale screen never overwrites a newer change.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Callable[[], datetime] = _utc_now,
        id_factory: Callable[[], str] = _new_public_id,
    ) -> None:
        self._session = session
        self._clock = clock
        self._id_factory = id_factory
        self._repository = EventRepository(session)

    async def _write[T](self, operation: Callable[[datetime], Awaitable[T]]) -> T:
        now = normalize_utc(self._clock())
        try:
            result = await operation(now)
            await self._session.commit()
        except BaseException:
            await self._session.rollback()
            raise
        return result

    async def _live(self, public_id: str, expected_revision: int | None) -> CalendarEventTable:
        row = await self._repository.get(public_id)
        if row is None:
            raise EventNotFoundError(_NOT_FOUND)
        if expected_revision is not None and row.revision != expected_revision:
            raise EventConflictError(_STALE)
        return row

    async def create(self, edit: EventEdit) -> CalendarEvent:
        """Add an event as revision 1."""

        async def run(now: datetime) -> CalendarEvent:
            row = CalendarEventTable(
                public_id=self._id_factory(),
                created_at_utc=now,
                updated_at_utc=now,
                revision=1,
            )
            apply_edit(row, edit)
            self._repository.add(row)
            await self._session.flush()
            return event_from_row(row)

        return await self._write(run)

    async def save(self, public_id: str, expected_revision: int, edit: EventEdit) -> CalendarEvent:
        """Replace a live event's fields with ``edit``, as one revision."""

        async def run(now: datetime) -> CalendarEvent:
            row = await self._live(public_id, expected_revision)
            apply_edit(row, edit)
            _touch(row, now)
            return event_from_row(row)

        return await self._write(run)

    async def delete(self, public_id: str, expected_revision: int) -> None:
        """Hide an event; restore() brings it back."""

        async def run(now: datetime) -> None:
            row = await self._live(public_id, expected_revision)
            row.deleted_at_utc = now
            _touch(row, now)

        await self._write(run)

    async def restore(self, public_id: str) -> CalendarEvent:
        """Bring back a deleted event; an event that isn't deleted is returned as it is."""

        async def run(now: datetime) -> CalendarEvent:
            row = await self._repository.get(public_id, include_deleted=True)
            if row is None:
                raise EventNotFoundError(_NOT_FOUND)
            if row.deleted_at_utc is not None:
                row.deleted_at_utc = None
                _touch(row, now)
            return event_from_row(row)

        return await self._write(run)

    async def get(self, public_id: str) -> CalendarEvent:
        """A live event; deleted events are not found."""
        return event_from_row(await self._live(public_id, None))

    async def list_range(self, first_day: date, last_day: date, zone: ZoneInfo) -> CalendarRange:
        """The live events touching ``first_day`` through ``last_day`` in ``zone``, all-day
        ones first, then by start; the open actions' dates in those days; and the cached
        Google events touching them, with the days the cache covers (ADR 0018).

        Raises ValueError, with a static message, for a range of more than MAX_RANGE_DAYS
        days or one that ends before it starts.
        """
        if last_day < first_day or (last_day - first_day).days >= MAX_RANGE_DAYS:
            raise ValueError(_BAD_RANGE)
        start_utc = day_window(first_day, zone).start_utc
        end_utc = day_window(last_day, zone).end_utc
        rows = await self._repository.touching(start_utc, end_utc, first_day, last_day)
        actions = await self._repository.dated_actions(start_utc, end_utc, first_day, last_day)
        events = sorted((event_from_row(row) for row in rows), key=CalendarEvent.sort_key)
        google = GoogleCalendarRepository(self._session)
        cached = await google.touching(start_utc, end_utc, first_day, last_day)
        return CalendarRange(
            first_day=first_day,
            last_day=last_day,
            events=tuple(events),
            markers=markers_for(actions, first_day, last_day, zone),
            google_events=tuple(sorted(cached, key=GoogleEvent.sort_key)),
            google_coverage=await google.coverage(),
        )

    async def apply_changes(self, changes: Sequence[EventChange]) -> AppliedBatch:
        """Apply every change as one transaction, each event one revision.

        An update or delete names the event as it was read; when any of them has changed
        since, EventConflictError is raised and nothing is applied. Returns what undo needs.
        """
        if not changes:
            raise ValueError(_NO_CHANGES)

        async def run(now: datetime) -> AppliedBatch:
            applied: list[AppliedChange] = []
            for change in changes:
                if change.kind is ChangeKind.CREATE:
                    assert change.edit is not None
                    row = CalendarEventTable(
                        public_id=self._id_factory(),
                        created_at_utc=now,
                        updated_at_utc=now,
                        revision=1,
                    )
                    apply_edit(row, change.edit)
                    self._repository.add(row)
                    await self._session.flush()
                    applied.append(
                        AppliedChange(kind=change.kind, public_id=row.public_id, revision=1)
                    )
                    continue
                assert change.event is not None
                row = await self._live(change.event.public_id, change.event.revision)
                previous = event_from_row(row).edit()
                if change.kind is ChangeKind.UPDATE:
                    assert change.edit is not None
                    apply_edit(row, change.edit)
                else:
                    row.deleted_at_utc = now
                _touch(row, now)
                applied.append(
                    AppliedChange(
                        kind=change.kind,
                        public_id=row.public_id,
                        revision=row.revision,
                        previous=previous if change.kind is ChangeKind.UPDATE else None,
                    )
                )
            return AppliedBatch(changes=tuple(applied))

        return await self._write(run)

    async def undo_changes(self, batch: AppliedBatch) -> None:
        """Reverse a batch while every event is exactly as it left them; all or none.

        An added event is deleted, an updated one gets its earlier fields back as a new
        revision, and a deleted one is restored. Raises EventConflictError otherwise.
        """

        async def run(now: datetime) -> None:
            for change in batch.changes:
                row = await self._repository.get(change.public_id, include_deleted=True)
                gone = row is None or (row.deleted_at_utc is not None) != (
                    change.kind is ChangeKind.DELETE
                )
                if row is None or gone or row.revision != change.revision:
                    raise EventConflictError(_UNDO)
                if change.kind is ChangeKind.CREATE:
                    row.deleted_at_utc = now
                elif change.kind is ChangeKind.UPDATE:
                    assert change.previous is not None
                    apply_edit(row, change.previous)
                else:
                    row.deleted_at_utc = None
                _touch(row, now)

        await self._write(run)
