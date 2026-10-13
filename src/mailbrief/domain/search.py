"""What a search of the owner's local records finds (the command palette, Ctrl+K).

Hits point at existing records; nothing is copied or stored. Titles, senders and calendar
names are the owner's or mail-derived text, so they are kept out of reprs.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Final

from pydantic import ConfigDict, Field, field_validator

from mailbrief.domain.common import DomainModel, normalize_utc
from mailbrief.domain.drafts import DraftKind

# A query needs this many characters, ignoring spaces, before anything is searched; at most
# QUERY_MAX_TERMS words of QUERY_MAX_CHARS in all count, and each kind shows HITS_PER_KIND.
QUERY_MIN_CHARS: Final = 2
QUERY_MAX_CHARS: Final = 200
QUERY_MAX_TERMS: Final = 5
HITS_PER_KIND: Final = 8


class SearchKind(StrEnum):
    """What a hit is, in the order the palette lists them."""

    ACTION = "action"
    WRITING = "writing"  # A draft or a note.
    EVENT = "event"  # One of the owner's calendar events.
    GOOGLE_EVENT = "google_event"
    MAIL = "mail"  # Cached Gmail metadata: never a body.


class SearchHit(DomainModel):
    """One record a search found, with what opening it needs.

    ``public_id`` names an action, a draft or an event; ``calendar_id`` and ``event_id`` a
    Google event; ``message_id`` and ``web_link`` a cached email. ``when`` is the moment that
    describes it (an event's start, an email's receipt, a record's last change) and ``day``
    an all-day event's first day.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    kind: SearchKind
    title: str = Field(min_length=1, repr=False)
    detail: str = Field(default="", repr=False)  # A sender, a calendar or a draft's kind.
    public_id: str | None = None
    calendar_id: str | None = None
    event_id: str | None = None
    message_id: str | None = None
    web_link: str | None = Field(default=None, repr=False)
    when: datetime | None = None
    day: date | None = None
    open: bool = True  # An action still open; an email still in the Inbox.
    waiting: bool = False  # An action someone else does.
    draft_kind: DraftKind | None = None

    @field_validator("when")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_utc(value)


class SearchResults(DomainModel):
    """The hits for one query, each kind in its own order, and the kinds that had more."""

    model_config = ConfigDict(hide_input_in_errors=True)

    hits: tuple[SearchHit, ...] = ()
    more: frozenset[SearchKind] = frozenset()


def query_terms(text: str) -> tuple[str, ...]:
    """The words a query matches, all of which must appear: at most QUERY_MAX_TERMS, from at
    most QUERY_MAX_CHARS characters, without repeats whatever their case. Empty when the
    query is shorter than QUERY_MIN_CHARS without its spaces.

    Words keep their case: SQLite compares letters A to Z in either case, and any other
    letter as typed, so a casefolded "école" would miss "École".
    """
    words = text[:QUERY_MAX_CHARS].split()
    if sum(len(word) for word in words) < QUERY_MIN_CHARS:
        return ()
    unique = {word.casefold(): word for word in reversed(words)}
    return tuple(reversed(unique.values()))[:QUERY_MAX_TERMS]
