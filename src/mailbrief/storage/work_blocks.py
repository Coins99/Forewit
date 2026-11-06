"""Work blocks (ADR 0028); the service owns commits and rollbacks.

Blocks change only through ORM objects, never bulk statements, so rows already loaded in
the session always show what was last written. Reads that show blocks join their task and
skip the blocks of deleted tasks, so deleting a task hides its blocks and restoring it
brings them back.
"""

from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import ActionStatus
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.events import ScheduledBlock
from mailbrief.domain.work_blocks import BLOCK_MAX_MINUTES, BlockTime, WorkBlock
from mailbrief.storage.database import sorted_batches
from mailbrief.storage.tables import ActionTable, WorkBlockTable


def block_from_row(row: WorkBlockTable, action_public_id: str) -> WorkBlock:
    """Rebuild a stored block; raises ValueError when the row no longer validates."""
    return WorkBlock(
        public_id=row.public_id,
        action_public_id=action_public_id,
        starts_at_utc=row.starts_at_utc,
        ends_at_utc=row.ends_at_utc,
        time_zone=row.time_zone,
        revision=row.revision,
        created_at_utc=row.created_at_utc,
        updated_at_utc=row.updated_at_utc,
    )


def scheduled_from_rows(row: WorkBlockTable, action: ActionTable) -> ScheduledBlock:
    """A block with its task's live title and state."""
    return ScheduledBlock(
        block=block_from_row(row, action.public_id),
        action_title=action.title,
        action_status=ActionStatus(action.status),
        waiting=action.ownership == ActionOwnership.WAITING_FOR.value,
    )


def apply_time(row: WorkBlockTable, time: BlockTime) -> None:
    row.starts_at_utc = time.starts_at_utc
    row.ends_at_utc = time.ends_at_utc
    row.time_zone = time.time_zone


class WorkBlockRepository:
    """Read and add blocks."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, row: WorkBlockTable) -> None:
        self._session.add(row)

    async def get(
        self, public_id: str, *, include_deleted: bool = False
    ) -> tuple[WorkBlockTable, ActionTable] | None:
        """A block of a live task, with the task; an unscheduled block only when asked."""
        query = (
            select(WorkBlockTable, ActionTable)
            .join(ActionTable, WorkBlockTable.action_id == ActionTable.id)
            .where(WorkBlockTable.public_id == public_id, ActionTable.deleted_at_utc.is_(None))
        )
        if not include_deleted:
            query = query.where(WorkBlockTable.deleted_at_utc.is_(None))
        found = (await self._session.execute(query)).tuples().one_or_none()
        return None if found is None else (found[0], found[1])

    async def live_count(self, action_id: int) -> int:
        return (
            await self._session.scalar(
                select(func.count())
                .select_from(WorkBlockTable)
                .where(
                    WorkBlockTable.action_id == action_id,
                    WorkBlockTable.deleted_at_utc.is_(None),
                )
            )
            or 0
        )

    async def for_actions(self, action_ids: Iterable[int]) -> dict[int, list[WorkBlockTable]]:
        """The live blocks of these tasks, each task's by start."""
        found: dict[int, list[WorkBlockTable]] = {}
        for chunk in sorted_batches(action_ids):
            rows = await self._session.scalars(
                select(WorkBlockTable)
                .where(
                    WorkBlockTable.action_id.in_(chunk),
                    WorkBlockTable.deleted_at_utc.is_(None),
                )
                .order_by(
                    WorkBlockTable.action_id,
                    WorkBlockTable.starts_at_utc,
                    WorkBlockTable.ends_at_utc,
                    WorkBlockTable.public_id,
                )
            )
            for row in rows:
                found.setdefault(row.action_id, []).append(row)
        return found

    async def touching(
        self, start_utc: datetime, end_utc: datetime
    ) -> list[tuple[WorkBlockTable, ActionTable]]:
        """Live blocks of live tasks that touch ``[start_utc, end_utc)``, by start.

        No block lasts longer than BLOCK_MAX_MINUTES, so only blocks starting that long before
        the range can reach into it: the start index bounds the read on both sides.
        """
        earliest = start_utc - timedelta(minutes=BLOCK_MAX_MINUTES)
        result = await self._session.execute(
            select(WorkBlockTable, ActionTable)
            .join(ActionTable, WorkBlockTable.action_id == ActionTable.id)
            .where(
                WorkBlockTable.deleted_at_utc.is_(None),
                ActionTable.deleted_at_utc.is_(None),
                WorkBlockTable.starts_at_utc >= earliest,
                WorkBlockTable.starts_at_utc < end_utc,
                WorkBlockTable.ends_at_utc > start_utc,
            )
            .order_by(WorkBlockTable.starts_at_utc, WorkBlockTable.ends_at_utc, WorkBlockTable.id)
        )
        return [(block, action) for block, action in result.tuples()]
