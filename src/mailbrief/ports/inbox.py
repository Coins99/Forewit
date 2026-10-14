"""Metadata enumeration for the current Inbox, independent of daily brief windows."""

from collections.abc import AsyncIterator
from typing import Protocol

from mailbrief.domain.inbox import MailboxPage
from mailbrief.domain.messages import MessagePage


class InboxReader(Protocol):
    def iter_inbox_pages(self, continuation: str | None = None) -> AsyncIterator[MessagePage]:
        """Enumerate current Inbox metadata across every received date."""
        ...


class MailboxReader(Protocol):
    async def fetch_mailbox_page(
        self, *, continuation: str | None = None, page_size: int = 50, query: str = ""
    ) -> MailboxPage:
        """One Inbox page, or all-mail search when query is nonempty; metadata only."""
        ...
