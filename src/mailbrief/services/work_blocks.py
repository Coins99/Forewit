"""Work blocks: time the owner sets aside on the calendar for one task (ADR 0028).

A block never changes its task. Nothing here writes to ``actions``: no revision, no
``updated_at_utc``, no activity, no deadline or target date. Nothing reads or sends email or
calls a provider, and AI never sees blocks. Every mutating method reads the clock once,
commits once and rolls back on failure. Errors carry static messages, and nothing logs a
task's title.

Overlaps, a block in a busy event's buffer, a block ending after its task's deadline and a
block outside the working hours are warnings that never stop a save. Only busy events overlap
a block, and an all-day busy event, a day off, overlaps every block on its days (ADR 0029).
They are the calendar's conflicts, from one definition (``domain.workload``, ADR 0030).
"""

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import ActionStatus
from mailbrief.domain.common import new_public_id, utc_now
from mailbrief.domain.events import CalendarEvent, ScheduledBlock
from mailbrief.domain.google_calendar import GoogleEvent
from mailbrief.domain.work_blocks import (
    MAX_BLOCKS_PER_TASK,
    MAX_OVERLAP_LABELS,
    BlockSaved,
    BlockTime,
    WorkBlock,
)
from mailbrief.domain.workload import ConflictKind, conflicts_for
from mailbrief.services.writes import commit_once
from mailbrief.storage.events import EventRepository, event_from_row
from mailbrief.storage.google_calendar import GoogleCalendarRepository
from mailbrief.storage.tables import ActionTable, WorkBlockTable
from mailbrief.storage.work_blocks import (
    WorkBlockRepository,
    apply_time,
    block_from_row,
    scheduled_from_rows,
)
from mailbrief.storage.working_hours import WorkingHoursRepository

_NO_TASK: Final = "That task was not found."
_NO_BLOCK: Final = "That scheduled time was not found."
_NOT_OPEN: Final = "Only an open task can be scheduled. Reopen it first."
_STALE: Final = "The scheduled time changed since it was loaded; reload it and try again."
_UNDO: Final = "The scheduled time changed since; it can no longer be undone."
_TOO_MANY: Final = "A task can have at most 50 scheduled times."


class BlockNotFoundError(LookupError):
    """No live block, or no live task, has that public ID."""


class BlockConflictError(ValueError):
    """The block changed since it was read, or its task can't take the change."""


def _touch(row: WorkBlockTable, now: datetime) -> None:
    row.revision += 1
    row.updated_at_utc = now


class WorkBlockService:
    """The owner's work blocks.

    A method taking ``expected_revision`` raises BlockConflictError when the block's revision
    differs, so a stale screen never overwrites a newer change.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = new_public_id,
    ) -> None:
        self._session = session
        self._clock = clock
        self._id_factory = id_factory
        self._repository = WorkBlockRepository(session)

    async def _write[T](self, operation: Callable[[datetime], Awaitable[T]]) -> T:
        return await commit_once(self._session, self._clock, operation)

    async def _task(self, action_public_id: str) -> ActionTable:
        action = (
            await self._session.scalars(
                select(ActionTable).where(
                    ActionTable.public_id == action_public_id,
                    ActionTable.deleted_at_utc.is_(None),
                )
            )
        ).one_or_none()
        if action is None:
            raise BlockNotFoundError(_NO_TASK)
        return action

    async def _live(
        self, public_id: str, expected_revision: int | None
    ) -> tuple[WorkBlockTable, ActionTable]:
        found = await self._repository.get(public_id)
        if found is None:
            raise BlockNotFoundError(_NO_BLOCK)
        if expected_revision is not None and found[0].revision != expected_revision:
            raise BlockConflictError(_STALE)
        return found

    @staticmethod
    def _open(action: ActionTable) -> None:
        if action.status != ActionStatus.OPEN.value:
            raise BlockConflictError(_NOT_OPEN)

    async def _saved(
        self, row: WorkBlockTable, action: ActionTable, previous: BlockTime | None = None
    ) -> BlockSaved:
        """The block as written, with its conflicts as the calendar shows them (ADR 0030):
        the busy entries and blocks it overlaps, the busy events whose buffer it sits in,
        whether it ends after its task's deadline and whether it falls outside the working
        hours."""
        await self._session.flush()
        scheduled = scheduled_from_rows(row, action)
        block = scheduled.block
        zone = ZoneInfo(block.time_zone)
        first_day, last_day = block.days(zone)
        hours = await WorkingHoursRepository(self._session).read()
        # Read around the block by the buffer, so an event whose buffer it sits in is seen.
        reach = timedelta(minutes=hours.buffer_minutes)
        start, end = block.starts_at_utc - reach, block.ends_at_utc + reach
        events: list[CalendarEvent | GoogleEvent] = []
        for stored in await EventRepository(self._session).touching(
            start, end, first_day, last_day
        ):
            try:
                events.append(event_from_row(stored))
            except ValueError:  # A row that no longer validates is a warning lost, never a
                continue  # save refused: warnings never stop a save.
        events += await GoogleCalendarRepository(self._session).touching(
            start, end, first_day, last_day
        )
        others = [
            scheduled_from_rows(other, task)
            for other, task in await self._repository.touching(*block.interval)
        ]
        conflicts = conflicts_for(scheduled, zone, events=events, blocks=others, hours=hours)
        kinds = {conflict.kind for conflict in conflicts}
        named = [
            conflict.label
            for conflict in conflicts
            if conflict.label is not None and conflict.kind is not ConflictKind.BUFFER
        ]
        buffered = [
            conflict.label
            for conflict in conflicts
            if conflict.label is not None and conflict.kind is ConflictKind.BUFFER
        ]
        return BlockSaved(
            block=block,
            previous=previous,
            overlaps=tuple(named[:MAX_OVERLAP_LABELS]),
            more_overlaps=max(0, len(named) - MAX_OVERLAP_LABELS),
            after_deadline=ConflictKind.AFTER_DEADLINE in kinds,
            outside_hours=ConflictKind.OUTSIDE_HOURS in kinds,
            buffered=tuple(buffered[:MAX_OVERLAP_LABELS]),
        )

    async def schedule(self, action_public_id: str, time: BlockTime) -> BlockSaved:
        """Add a block to a live, open task as revision 1."""

        async def run(now: datetime) -> BlockSaved:
            action = await self._task(action_public_id)
            self._open(action)
            if await self._repository.live_count(action.id) >= MAX_BLOCKS_PER_TASK:
                raise BlockConflictError(_TOO_MANY)
            row = WorkBlockTable(
                public_id=self._id_factory(),
                action_id=action.id,
                created_at_utc=now,
                updated_at_utc=now,
                revision=1,
            )
            apply_time(row, time)
            self._repository.add(row)
            return await self._saved(row, action)

        return await self._write(run)

    async def move(self, public_id: str, expected_revision: int, time: BlockTime) -> BlockSaved:
        """Give a live block of an open task a new time, as one revision; this is also how a
        block is resized. ``previous`` in the result is its time before, for Undo."""

        async def run(now: datetime) -> BlockSaved:
            row, action = await self._live(public_id, expected_revision)
            self._open(action)
            previous = block_from_row(row, action.public_id).time
            apply_time(row, time)
            _touch(row, now)
            return await self._saved(row, action, previous)

        return await self._write(run)

    async def undo_move(
        self, public_id: str, revision_after: int, previous: BlockTime
    ) -> BlockSaved:
        """Put a moved block back at ``previous``, only while it is as the move left it."""

        async def run(now: datetime) -> BlockSaved:
            found = await self._repository.get(public_id)
            if found is None or found[0].revision != revision_after:
                raise BlockConflictError(_UNDO)
            row, action = found
            self._open(action)
            apply_time(row, previous)
            _touch(row, now)
            return await self._saved(row, action)

        return await self._write(run)

    async def unschedule(self, public_id: str, expected_revision: int) -> WorkBlock:
        """Take a live block off the calendar, whatever its task's state; restore() brings it
        back. Returns the block as it was unscheduled."""

        async def run(now: datetime) -> WorkBlock:
            row, action = await self._live(public_id, expected_revision)
            row.deleted_at_utc = now
            _touch(row, now)
            return block_from_row(row, action.public_id)

        return await self._write(run)

    async def restore(self, public_id: str) -> BlockSaved:
        """Bring back an unscheduled block of a live task; a live block is returned as it is.
        Its task's state isn't checked, so Undo works after the task was completed."""

        async def run(now: datetime) -> BlockSaved:
            found = await self._repository.get(public_id, include_deleted=True)
            if found is None:
                raise BlockNotFoundError(_NO_BLOCK)
            row, action = found
            if row.deleted_at_utc is not None:
                if await self._repository.live_count(action.id) >= MAX_BLOCKS_PER_TASK:
                    raise BlockConflictError(_TOO_MANY)
                row.deleted_at_utc = None
                _touch(row, now)
            return await self._saved(row, action)

        return await self._write(run)

    async def get(self, public_id: str) -> ScheduledBlock:
        """A live block of a live task, with the task's title and state."""
        row, action = await self._live(public_id, None)
        return scheduled_from_rows(row, action)

    async def for_action(self, action_public_id: str) -> tuple[WorkBlock, ...]:
        """A live task's live blocks, by start."""
        action = await self._task(action_public_id)
        rows = (await self._repository.for_actions([action.id])).get(action.id, [])
        return tuple(block_from_row(row, action.public_id) for row in rows)
