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
    CalendarEvent,
    CalendarRange,
    EventEdit,
    MarkerKind,
)
from mailbrief.services.calendar import day_window
from mailbrief.storage.events import EventRepository, apply_edit, event_from_row
from mailbrief.storage.tables import ActionTable, CalendarEventTable

MAX_RANGE_DAYS: Final = 42
_NOT_FOUND: Final = "That event was not found."
_STALE: Final = "The event changed since it was loaded; reload it and try again."
_BAD_RANGE: Final = "Choose a range of 1 to 42 days."


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
        ones first, then by start; and the open actions' dates in those days.

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
        return CalendarRange(
            first_day=first_day,
            last_day=last_day,
            events=tuple(events),
            markers=markers_for(actions, first_day, last_day, zone),
        )
