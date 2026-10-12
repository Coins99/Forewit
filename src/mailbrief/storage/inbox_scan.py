"""Account-scoped, transient Inbox scan checkpoints; no OAuth tokens or bodies."""

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.storage.tables import InboxScanMessageTable, MessageTable, SyncRunTable


@dataclass(frozen=True)
class InboxScan:
    run_id: int
    continuation: str
    pages: int
    failed: int
    seen_ids: frozenset[str]


class InboxScanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resume(self, account_id: int) -> InboxScan | None:
        row = await self._session.scalar(
            select(SyncRunTable)
            .where(
                SyncRunTable.account_id == account_id,
                SyncRunTable.inbox_continuation.is_not(None),
            )
            .order_by(SyncRunTable.id.desc())
            .limit(1)
        )
        if row is None:
            return None
        seen = frozenset(
            await self._session.scalars(
                select(MessageTable.provider_message_id)
                .join(InboxScanMessageTable, InboxScanMessageTable.message_id == MessageTable.id)
                .where(
                    InboxScanMessageTable.sync_run_id == row.id,
                    MessageTable.account_id == account_id,
                )
            )
        )
        token = row.inbox_continuation
        if not token or len(token) > 4096 or len(seen) != row.message_count:
            await self.discard(row.id)
            return None
        row.status = "started"
        row.completed_at_utc = None
        row.sanitized_error_code = None
        return InboxScan(row.id, token, row.page_count, row.failed_message_count, seen)

    async def remember(
        self, run_id: int, account_id: int, identifiers: set[str], continuation: str | None
    ) -> None:
        rows = await self._session.scalars(
            select(MessageTable.id).where(
                MessageTable.account_id == account_id,
                MessageTable.provider_message_id.in_(identifiers),
            )
        )
        values = [{"sync_run_id": run_id, "message_id": identifier} for identifier in rows]
        for offset in range(0, len(values), 100):
            await self._session.execute(
                insert(InboxScanMessageTable)
                .values(values[offset : offset + 100])
                .on_conflict_do_nothing()
            )
        row = await self._session.get(SyncRunTable, run_id)
        assert row is not None
        row.inbox_continuation = continuation
        await self._session.flush()

    async def discard(self, run_id: int) -> None:
        row = await self._session.get(SyncRunTable, run_id)
        if row is not None:
            row.inbox_continuation = None
        await self._session.execute(
            delete(InboxScanMessageTable).where(InboxScanMessageTable.sync_run_id == run_id)
        )

    async def clear(self) -> None:
        rows = await self._session.scalars(
            select(SyncRunTable).where(SyncRunTable.inbox_continuation.is_not(None))
        )
        for row in rows:
            row.inbox_continuation = None
        await self._session.execute(delete(InboxScanMessageTable))
