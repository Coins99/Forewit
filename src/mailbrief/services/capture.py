"""Optional review of canonical quick captures. Unassigned never means unprocessed."""

from collections.abc import Callable
from datetime import datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from mailbrief.domain.actions import Action, ActionActivityKind
from mailbrief.domain.common import utc_now
from mailbrief.domain.drafts import Draft
from mailbrief.services.actions import ActionService
from mailbrief.services.drafts import DraftService
from mailbrief.services.writes import commit_once
from mailbrief.storage.actions import ActionRepository
from mailbrief.storage.drafts import DraftRepository
from mailbrief.storage.tables import ActionTable, DraftTable


class CaptureConflictError(ValueError):
    """Review used an old revision or an unavailable record."""


class CaptureService:
    def __init__(self, session: AsyncSession, *, clock: Callable[[], datetime] = utc_now) -> None:
        self.session, self.clock = session, clock

    async def inbox(self) -> tuple[Action | Draft, ...]:
        """Newest 100 pending records per type, merged in recency order.

        Retain both bounded sets so a UI type change can reveal older notes or
        tasks. The displayed 100-row limit belongs after that type filter.
        """
        tasks = list(
            await self.session.scalars(
                select(ActionTable)
                .where(
                    ActionTable.capture_origin == "quick_capture",
                    ActionTable.capture_processed_at_utc.is_(None),
                    ActionTable.deleted_at_utc.is_(None),
                )
                .order_by(ActionTable.updated_at_utc.desc(), ActionTable.public_id)
                .limit(100)
            )
        )
        notes = list(
            await self.session.scalars(
                select(DraftTable)
                .where(
                    DraftTable.capture_origin == "quick_capture",
                    DraftTable.capture_processed_at_utc.is_(None),
                    DraftTable.deleted_at_utc.is_(None),
                    DraftTable.kind == "note",
                )
                .order_by(DraftTable.updated_at_utc.desc(), DraftTable.public_id)
                .limit(100)
            )
        )
        records: list[Action | Draft] = [
            *await ActionRepository(self.session).load(tasks),
            *await DraftRepository(self.session).load(notes),
        ]
        return tuple(
            sorted(records, key=lambda item: (item.updated_at_utc, item.public_id), reverse=True)
        )

    async def keep(
        self, kind: Literal["task", "note"], public_id: str, revision: int
    ) -> Action | Draft:
        async def run(now: datetime) -> Action | Draft:
            row: ActionTable | DraftTable | None
            if kind == "task":
                row = await self.session.scalar(
                    select(ActionTable)
                    .where(ActionTable.public_id == public_id)
                    .execution_options(populate_existing=True)
                )
            else:
                row = await self.session.scalar(
                    select(DraftTable)
                    .where(DraftTable.public_id == public_id)
                    .execution_options(populate_existing=True)
                )
            if (
                row is None
                or row.deleted_at_utc is not None
                or row.revision != revision
                or isinstance(row, DraftTable)
                and row.kind != "note"
            ):
                raise CaptureConflictError(
                    "That capture changed or is no longer available. Review it again."
                )
            if row.capture_origin == "quick_capture" and row.capture_processed_at_utc is None:
                row.capture_processed_at_utc = now
                row.updated_at_utc = now
                row.revision += 1
                if isinstance(row, ActionTable):
                    await ActionRepository(self.session).record_activity(
                        row, ActionActivityKind.EDITED, now
                    )
            if kind == "task":
                return await ActionService(self.session).get(public_id)
            return await DraftService(self.session).get(public_id)

        try:
            return await commit_once(self.session, self.clock, run)
        except StaleDataError:
            raise CaptureConflictError("That capture changed. Review it again.") from None
