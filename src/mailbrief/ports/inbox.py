"""Metadata enumeration for the current Inbox, independent of daily brief windows."""

from collections.abc import AsyncIterator
from typing import Protocol

from mailbrief.domain.messages import MessagePage


class InboxReader(Protocol):
    def iter_inbox_pages(self, continuation: str | None = None) -> AsyncIterator[MessagePage]:
        """Enumerate current Inbox metadata across every received date."""
        ...
