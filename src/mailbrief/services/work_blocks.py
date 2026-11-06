"""Work blocks: time the owner sets aside on the calendar for one task (ADR 0028).

A block never changes its task. Nothing here writes to ``actions``: no revision, no
``updated_at_utc``, no activity, no deadline or target date. Nothing reads or sends email or
calls a provider, and AI never sees blocks. Every mutating method reads the clock once,
commits once and rolls back on failure. Errors carry static messages, and nothing logs a
task's title.

Overlaps and a block ending after its task's deadline are warnings that never stop a save.
"""

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import ActionStatus
from mailbrief.domain.analysis import DeadlinePrecision, deadline_due_at
from mailbrief.domain.common import new_public_id, utc_now
from mailbrief.domain.events import ScheduledBlock
from mailbrief.domain.intervals import overlaps
from mailbrief.domain.work_blocks import (
    MAX_BLOCKS_PER_TASK,
    MAX_OVERLAP_LABELS,
    BlockSaved,
    BlockTime,
    WorkBlock,
)
from mailbrief.services.writes import commit_once
from mailbrief.storage.events import EventRepository
from mailbrief.storage.google_calendar import GoogleCalendarRepository
from mailbrief.storage.tables import ActionTable, WorkBlockTable
from mailbrief.storage.work_blocks import (
    WorkBlockRepository,
    apply_time,
    block_from_row,
    scheduled_from_rows,
)

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


def _due(action: ActionTable) -> datetime | None:
    return deadline_due_at(
        DeadlinePrecision(action.deadline_precision),
        action.deadline_date,
        action.deadline_at_utc,
        action.deadline_timezone,
    )


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
        """The block as written, with the timed entries it overlaps and whether it ends after
        its task's deadline."""
        await self._session.flush()
        block = block_from_row(row, action.public_id)
        start, end = block.interval
        labels: list[tuple[datetime, str]] = []
        days = block.days(ZoneInfo(block.time_zone))
        events = await EventRepository(self._session).touching(start, end, days[0], days[1])
        for event in events:
            if event.all_day or event.starts_at_utc is None or event.ends_at_utc is None:
                continue
            if overlaps(block.interval, (event.starts_at_utc, event.ends_at_utc)):
                labels.append((event.starts_at_utc, event.title))
        cached = await GoogleCalendarRepository(self._session).touching(
            start, end, days[0], days[1]
        )
        for google in cached:
            if google.all_day or google.starts_at_utc is None or google.ends_at_utc is None:
                continue
            if overlaps(block.interval, (google.starts_at_utc, google.ends_at_utc)):
                labels.append((google.starts_at_utc, f"{google.title} ({google.calendar_name})"))
        for other, task in await self._repository.touching(start, end):
            if other.id != row.id:
                labels.append((other.starts_at_utc, f"Task · {task.title}"))
        labels.sort(key=lambda found: found[0])
        due = _due(action)
        return BlockSaved(
            block=block,
            previous=previous,
            overlaps=tuple(label for _, label in labels[:MAX_OVERLAP_LABELS]),
            more_overlaps=max(0, len(labels) - MAX_OVERLAP_LABELS),
            after_deadline=due is not None and block.ends_at_utc > due,
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
