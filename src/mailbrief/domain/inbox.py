"""A bounded metadata page for interactive Inbox browsing and mailbox search."""

from pydantic import Field

from mailbrief.domain.common import DomainModel
from mailbrief.domain.messages import NormalizedMessage


class MailboxPage(DomainModel):
    messages: tuple[NormalizedMessage, ...] = ()
    continuation: str | None = Field(default=None, min_length=1, max_length=4096, repr=False)
    attempted: int = Field(default=0, ge=0, le=100)
    failed: int = Field(default=0, ge=0, le=100)
    discarded_ids: frozenset[str] = frozenset()
