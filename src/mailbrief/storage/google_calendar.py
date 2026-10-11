"""The cache of the owner's Google calendars and events (ADR 0018, phase 2).

It is provider data, like cached mail, so it is replaced with bulk statements; the owner's
own events live in storage/events.py. The service owns commits and rollbacks.
"""

from collections.abc import Sequence
from datetime import date, datetime

from pydantic import HttpUrl
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.google_calendar import (
    GoogleCalendarInfo,
    GoogleCoverage,
    GoogleEvent,
)
from mailbrief.storage.tables import GoogleCalendarTable, GoogleEventTable


def google_event_from_row(row: GoogleEventTable, calendar: GoogleCalendarTable) -> GoogleEvent:
    """Rebuild a cached event; raises ValueError when the row no longer validates."""
    return GoogleEvent(
        calendar_id=calendar.calendar_id,
        calendar_name=calendar.name,
        event_id=row.event_id,
        title=row.title,
        all_day=row.all_day,
        starts_at_utc=row.starts_at_utc,
        ends_at_utc=row.ends_at_utc,
        start_date=row.start_date,
        last_date=row.last_date,
        location=row.location,
        web_link=None if row.web_link is None else HttpUrl(row.web_link),
        busy=row.busy,
    )


class GoogleCalendarRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def calendars(self) -> list[GoogleCalendarTable]:
        result = await self._session.scalars(
            select(GoogleCalendarTable).order_by(GoogleCalendarTable.id)
        )
        return list(result)

    async def replace_calendars(
        self, infos: Sequence[GoogleCalendarInfo], now: datetime
    ) -> dict[str, GoogleCalendarTable]:
        """Keep exactly these calendars; one no longer listed goes with its events."""
        existing = {row.calendar_id: row for row in await self.calendars()}
        wanted = {info.calendar_id for info in infos}
        gone = [row.id for key, row in existing.items() if key not in wanted]
        if gone:
            await self._session.execute(
                delete(GoogleEventTable).where(GoogleEventTable.calendar_row_id.in_(gone))
            )
            await self._session.execute(
                delete(GoogleCalendarTable).where(GoogleCalendarTable.id.in_(gone))
            )
        rows: dict[str, GoogleCalendarTable] = {}
        for info in infos:
            row = existing.get(info.calendar_id)
            if row is None:
                row = GoogleCalendarTable(calendar_id=info.calendar_id)
                self._session.add(row)
            row.name = info.name
            row.is_primary = info.primary
            row.shown = info.shown
            row.updated_at_utc = now
            rows[info.calendar_id] = row
        await self._session.flush()
        return rows

    async def replace_events(
        self,
        calendar: GoogleCalendarTable,
        events: Sequence[GoogleEvent],
        first_day: date,
        last_day: date,
        now: datetime,
    ) -> None:
        """The calendar's cache becomes exactly ``events``, read for these days."""
        await self._session.execute(
            delete(GoogleEventTable).where(GoogleEventTable.calendar_row_id == calendar.id)
        )
        seen: set[str] = set()
        for event in events:
            if event.event_id in seen:
                continue  # Google never repeats an ID in one listing; a repeat is dropped.
            seen.add(event.event_id)
            self._session.add(
                GoogleEventTable(
                    calendar_row_id=calendar.id,
                    event_id=event.event_id,
                    title=event.title,
                    all_day=event.all_day,
                    starts_at_utc=event.starts_at_utc,
                    ends_at_utc=event.ends_at_utc,
                    start_date=event.start_date,
                    last_date=event.last_date,
                    location=event.location,
                    web_link=None if event.web_link is None else str(event.web_link),
                    busy=event.busy,
                    fetched_at_utc=now,
                )
            )
        calendar.synced_from = first_day
        calendar.synced_until = last_day
        calendar.synced_at_utc = now

    async def touching(
        self, start_utc: datetime, end_utc: datetime, first_day: date, last_day: date
    ) -> list[GoogleEvent]:
        """Cached events of shown calendars touching the instants or, all-day, the days."""
        result = await self._session.execute(
            select(GoogleEventTable, GoogleCalendarTable)
            .join(GoogleCalendarTable, GoogleEventTable.calendar_row_id == GoogleCalendarTable.id)
            .where(
                GoogleCalendarTable.shown.is_(True),
                or_(
                    and_(
                        GoogleEventTable.all_day.is_(False),
                        GoogleEventTable.starts_at_utc < end_utc,
                        or_(
                            GoogleEventTable.ends_at_utc > start_utc,
                            and_(
                                GoogleEventTable.ends_at_utc == GoogleEventTable.starts_at_utc,
                                GoogleEventTable.starts_at_utc >= start_utc,
                            ),
                        ),
                    ),
                    and_(
                        GoogleEventTable.all_day.is_(True),
                        GoogleEventTable.start_date <= last_day,
                        GoogleEventTable.last_date >= first_day,
                    ),
                ),
            )
            .order_by(GoogleEventTable.id)
        )
        return [google_event_from_row(event, calendar) for event, calendar in result.tuples()]

    async def coverage(self) -> GoogleCoverage | None:
        """The days every shown calendar was last read for, and the oldest read; None
        when a shown calendar has never been read."""
        shown = [row for row in await self.calendars() if row.shown]
        windows = [
            (row.synced_from, row.synced_until, row.synced_at_utc)
            for row in shown
            if row.synced_from is not None
            and row.synced_until is not None
            and row.synced_at_utc is not None
        ]
        if not shown or len(windows) != len(shown):
            return None
        first = max(window[0] for window in windows)
        last = min(window[1] for window in windows)
        if last < first:
            return None
        return GoogleCoverage(
            first_day=first, last_day=last, synced_at_utc=min(window[2] for window in windows)
        )

    async def clear(self) -> None:
        """Forget every cached calendar and event."""
        await self._session.execute(delete(GoogleEventTable))
        await self._session.execute(delete(GoogleCalendarTable))
