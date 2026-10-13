"""Search the owner's local records for the command palette (Ctrl+K).

Only this computer's SQLite is read: actions (title, notes and steps), drafts and notes
(title and body), the owner's events (title and notes), cached Google event titles, and
the cached metadata of the connected Gmail account (subject, sender and Gmail's preview
snippet; bodies are never stored, so never searched). Nothing is sent anywhere or written.
The query is the owner's text: logs carry only counts.

Every word must appear somewhere in a record. Queries are parameterized LIKE patterns with
the wildcards escaped, bounded to HITS_PER_KIND hits per kind (plus one, to know there are
more), so the work stays small without a full-text index.
"""

import logging
from collections.abc import Sequence
from typing import Any, Final

from sqlalchemy import ColumnElement, and_, case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import QueryableAttribute

from mailbrief.domain.drafts import DraftKind, first_line
from mailbrief.domain.messages import ProviderKind
from mailbrief.domain.search import (
    HITS_PER_KIND,
    SearchHit,
    SearchKind,
    SearchResults,
    query_terms,
)
from mailbrief.storage.tables import (
    AccountTable,
    ActionStepTable,
    ActionTable,
    CalendarEventTable,
    DraftTable,
    GoogleCalendarTable,
    GoogleEventTable,
    MessageTable,
)

logger = logging.getLogger(__name__)

_ESCAPE: Final = "\\"
# A text column of a mapped table.
_Text = QueryableAttribute[Any]


def like_pattern(term: str) -> str:
    """``term`` anywhere, with LIKE's wildcards and the escape character taken literally."""
    escaped = term.replace(_ESCAPE, _ESCAPE * 2).replace("%", r"\%").replace("_", r"\_")
    return f"%{escaped}%"


def _contains(column: _Text, term: str) -> ColumnElement[bool]:
    return column.ilike(like_pattern(term), escape=_ESCAPE)


def _every_term(terms: Sequence[str], columns: Sequence[_Text]) -> ColumnElement[bool]:
    """Each term in at least one of the columns."""
    return and_(*(or_(*(_contains(column, term) for column in columns)) for term in terms))


class SearchService:
    """Reads only; one query per kind."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self, text: str, *, account_email: str | None = None, limit: int = HITS_PER_KIND
    ) -> SearchResults:
        """What matches ``text`` (see query_terms): at most ``limit`` hits of each kind, in
        SearchKind's order. Mail is searched only for ``account_email``'s cached Gmail
        metadata, and not at all without it."""
        if limit < 1:
            raise ValueError("limit must be at least 1")
        terms = query_terms(text)
        if not terms:
            return SearchResults()
        hits: list[SearchHit] = []
        more: set[SearchKind] = set()
        found = {
            SearchKind.ACTION: await self._actions(terms, limit + 1),
            SearchKind.WRITING: await self._writing(terms, limit + 1),
            SearchKind.EVENT: await self._events(terms, limit + 1),
            SearchKind.GOOGLE_EVENT: await self._google(terms, limit + 1),
            SearchKind.MAIL: (
                await self._mail(terms, account_email, limit + 1) if account_email else []
            ),
        }
        for kind, kind_hits in found.items():
            if len(kind_hits) > limit:
                more.add(kind)
            hits.extend(kind_hits[:limit])
        logger.info("Search of %d words found %d records", len(terms), len(hits))
        return SearchResults(hits=tuple(hits), more=frozenset(more))

    async def _actions(self, terms: Sequence[str], limit: int) -> list[SearchHit]:
        """Live actions, open ones first, then the most recently changed."""
        in_steps = [
            ActionTable.id.in_(
                select(ActionStepTable.action_id).where(_contains(ActionStepTable.text, term))
            )
            for term in terms
        ]
        matches = and_(
            *(
                or_(_contains(ActionTable.title, term), _contains(ActionTable.notes, term), step)
                for term, step in zip(terms, in_steps, strict=True)
            )
        )
        rows = await self._session.scalars(
            select(ActionTable)
            .where(ActionTable.deleted_at_utc.is_(None), matches)
            .order_by(
                case((ActionTable.status == "open", 0), else_=1),
                ActionTable.updated_at_utc.desc(),
                ActionTable.id.desc(),
            )
            .limit(limit)
        )
        return [
            SearchHit(
                kind=SearchKind.ACTION,
                title=row.title,
                public_id=row.public_id,
                when=row.updated_at_utc,
                open=row.status == "open",
                waiting=row.ownership == "waiting_for",
            )
            for row in rows
        ]

    async def _writing(self, terms: Sequence[str], limit: int) -> list[SearchHit]:
        """Live drafts and notes, the most recently changed first."""
        rows = await self._session.scalars(
            select(DraftTable)
            .where(
                DraftTable.deleted_at_utc.is_(None),
                _every_term(terms, (DraftTable.title, DraftTable.body)),
            )
            .order_by(DraftTable.updated_at_utc.desc(), DraftTable.id.desc())
            .limit(limit)
        )
        return [
            SearchHit(
                kind=SearchKind.WRITING,
                title=row.title.strip() or first_line(row.body) or f"Untitled {row.kind}",
                public_id=row.public_id,
                when=row.updated_at_utc,
                draft_kind=DraftKind(row.kind),
            )
            for row in rows
        ]

    async def _events(self, terms: Sequence[str], limit: int) -> list[SearchHit]:
        """The owner's live events, the latest first."""
        rows = await self._session.scalars(
            select(CalendarEventTable)
            .where(
                CalendarEventTable.deleted_at_utc.is_(None),
                _every_term(terms, (CalendarEventTable.title, CalendarEventTable.notes)),
            )
            .order_by(
                func.coalesce(
                    CalendarEventTable.starts_at_utc, CalendarEventTable.start_date
                ).desc(),
                CalendarEventTable.id.desc(),
            )
            .limit(limit)
        )
        return [
            SearchHit(
                kind=SearchKind.EVENT,
                title=row.title,
                public_id=row.public_id,
                when=row.starts_at_utc,
                day=row.start_date,
            )
            for row in rows
        ]

    async def _google(self, terms: Sequence[str], limit: int) -> list[SearchHit]:
        """Cached Google events, by title, the latest first."""
        rows = await self._session.execute(
            select(GoogleEventTable, GoogleCalendarTable.calendar_id, GoogleCalendarTable.name)
            .join(GoogleCalendarTable, GoogleCalendarTable.id == GoogleEventTable.calendar_row_id)
            .where(_every_term(terms, (GoogleEventTable.title,)))
            .order_by(
                func.coalesce(GoogleEventTable.starts_at_utc, GoogleEventTable.start_date).desc(),
                GoogleEventTable.id.desc(),
            )
            .limit(limit)
        )
        return [
            SearchHit(
                kind=SearchKind.GOOGLE_EVENT,
                title=event.title,
                detail=name,
                calendar_id=calendar_id,
                event_id=event.event_id,
                web_link=event.web_link,
                when=event.starts_at_utc,
                day=event.start_date,
            )
            for event, calendar_id, name in rows
        ]

    async def _mail(self, terms: Sequence[str], account_email: str, limit: int) -> list[SearchHit]:
        """The account's cached mail metadata, the newest first."""
        rows = await self._session.scalars(
            select(MessageTable)
            .join(AccountTable, AccountTable.id == MessageTable.account_id)
            .where(
                AccountTable.provider == ProviderKind.GMAIL.value,
                AccountTable.email_address == account_email,
                _every_term(
                    terms,
                    (
                        MessageTable.subject,
                        MessageTable.sender_name,
                        MessageTable.sender_address,
                        MessageTable.body_preview,
                    ),
                ),
            )
            .order_by(MessageTable.received_at_utc.desc(), MessageTable.id.desc())
            .limit(limit)
        )
        return [
            SearchHit(
                kind=SearchKind.MAIL,
                title=row.subject or "(no subject)",
                detail=row.sender_name or row.sender_address,
                message_id=row.provider_message_id,
                web_link=row.web_link,
                when=row.received_at_utc,
                open=row.is_in_inbox,
            )
            for row in rows
        ]
