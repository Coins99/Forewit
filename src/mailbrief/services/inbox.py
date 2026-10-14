"""Bounded interactive browsing; absence from a page never reconciles the Inbox."""

import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.inbox import MailboxPage
from mailbrief.ports.inbox import MailboxReader
from mailbrief.storage.repositories import MessageRepository


class InboxService:
    def __init__(self, session: AsyncSession, reader: MailboxReader) -> None:
        self._session = session
        self._reader = reader

    async def load_page(
        self,
        account_id: int,
        cancel: asyncio.Event,
        *,
        continuation: str | None = None,
        page_size: int = 50,
        query: str = "",
    ) -> MailboxPage:
        if cancel.is_set():
            raise asyncio.CancelledError
        page = await self._reader.fetch_mailbox_page(
            continuation=continuation, page_size=page_size, query=query
        )
        if cancel.is_set():
            raise asyncio.CancelledError
        messages = MessageRepository(self._session)
        await messages.upsert_messages(account_id, page.messages)
        # These IDs were directly observed as unavailable, not merely absent from a page.
        for row in await messages.get_by_provider_ids(account_id, page.discarded_ids):
            await self._session.delete(row)
        await self._session.commit()
        return page
