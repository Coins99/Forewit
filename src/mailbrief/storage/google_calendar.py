"""The cache of the owner's shown Google calendars and their events (ADR 0018, phase 2).

It is provider data, like cached mail, so it is replaced with bulk statements; the owner's
own events live in storage/events.py. The service owns commits and rollbacks.
"""

from collections.abc import Iterable, Sequence
from datetime import date, datetime

from pydantic import HttpUrl
from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.common import new_public_id, utc_now
from mailbrief.domain.google_calendar import (
    CalendarKnowledge,
    GoogleCalendarInfo,
    GoogleCoverage,
    GoogleEvent,
)
from mailbrief.storage.tables import GoogleCalendarStateTable, GoogleCalendarTable, GoogleEventTable


def google_event_from_row(row: GoogleEventTable, calendar: GoogleCalendarTable) -> GoogleEvent:
    """Rebuild a cached event; raises ValueError when the row no longer validates."""
    return GoogleEvent(
        calendar_id=calendar.calendar_id,
        calendar_name=calendar.name,
        event_id=row.event_id,
        title=row.title,
        all_day=row.all_day,
        busy=row.busy,
        starts_at_utc=row.starts_at_utc,
        ends_at_utc=row.ends_at_utc,
        start_date=row.start_date,
        last_date=row.last_date,
        web_link=None if row.web_link is None else HttpUrl(row.web_link),
    )


class GoogleCalendarRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def calendars(self) -> list[GoogleCalendarTable]:
        result = await self._session.scalars(
            select(GoogleCalendarTable)
            .order_by(GoogleCalendarTable.id)
            .execution_options(populate_existing=True)
        )
        return list(result)

    async def replace_calendars(
        self,
        infos: Sequence[GoogleCalendarInfo],
        now: datetime,
        first: date | None = None,
        last: date | None = None,
        zone: str | None = None,
    ) -> dict[str, GoogleCalendarTable]:
        """Keep exactly these calendars, the shown ones; any other goes with its events.
        A repeated calendar ID is kept once."""
        state = await self.bump()
        state.list_succeeded = True
        state.listed_at_utc = now
        state.first_day, state.last_day, state.time_zone = first, last, zone
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
            if info.calendar_id in rows:
                continue
            row = existing.get(info.calendar_id)
            if row is None:
                row = GoogleCalendarTable(calendar_id=info.calendar_id)
                self._session.add(row)
            row.name = info.name
            row.is_primary = info.primary
            row.updated_at_utc = now
            row.read_failed = True
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
        zone: str = "UTC",
    ) -> int:
        """The calendar's cache becomes exactly ``events``, read for these days; returns how
        many were stored."""
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
                    busy=event.busy,
                    starts_at_utc=event.starts_at_utc,
                    ends_at_utc=event.ends_at_utc,
                    start_date=event.start_date,
                    last_date=event.last_date,
                    web_link=None if event.web_link is None else str(event.web_link),
                    fetched_at_utc=now,
                )
            )
        await self.mark_checked((calendar,), first_day, last_day, now)
        calendar.successful_from, calendar.successful_until = first_day, last_day
        calendar.successful_at_utc, calendar.successful_zone = now, zone
        calendar.read_failed = False
        return len(seen)

    async def mark_checked(
        self,
        calendars: Iterable[GoogleCalendarTable],
        first_day: date,
        last_day: date,
        now: datetime,
    ) -> None:
        """A sync dealt with these calendars for these days: their events were read, or the
        read failed and they keep what they had."""
        await self.bump()
        for calendar in calendars:
            calendar.read_failed = True
            calendar.synced_from = first_day
            calendar.synced_until = last_day
            calendar.synced_at_utc = now

    async def touching(
        self, start_utc: datetime, end_utc: datetime, first_day: date, last_day: date
    ) -> list[GoogleEvent]:
        """Cached events touching the instants or, all-day, the days."""
        result = await self._session.execute(
            select(GoogleEventTable, GoogleCalendarTable)
            .join(GoogleCalendarTable, GoogleEventTable.calendar_row_id == GoogleCalendarTable.id)
            .where(
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

    async def checked_coverage(self) -> GoogleCoverage | None:
        """The days every calendar was last checked for, and the oldest check; None when
        a calendar has never been checked."""
        rows = await self.calendars()
        if not rows:
            knowledge = await self.knowledge()
            return knowledge.coverage
        windows = [
            (row.synced_from, row.synced_until, row.synced_at_utc)
            for row in rows
            if row.synced_from is not None
            and row.synced_until is not None
            and row.synced_at_utc is not None
        ]
        if not rows or len(windows) != len(rows):
            return None
        first = max(window[0] for window in windows)
        last = min(window[1] for window in windows)
        if last < first:
            return None
        state = (await self._session.scalars(select(GoogleCalendarStateTable))).one_or_none()
        return GoogleCoverage(
            first_day=first,
            last_day=last,
            synced_at_utc=min(window[2] for window in windows),
            time_zone=None if state is None else state.time_zone,
        )

    async def bump(self) -> GoogleCalendarStateTable:
        state = (
            await self._session.scalars(
                select(GoogleCalendarStateTable).execution_options(populate_existing=True)
            )
        ).one_or_none()
        if state is None:
            state = GoogleCalendarStateTable(id=1, list_succeeded=False)
            self._session.add(state)
        state.generation = new_public_id()
        return state

    async def list_failed(self) -> None:
        state = await self.bump()
        state.list_succeeded = False
        state.listed_at_utc = utc_now()  # Attempt only; never promoted to successful coverage.

    async def knowledge(self) -> CalendarKnowledge:
        state = (
            await self._session.scalars(
                select(GoogleCalendarStateTable).execution_options(populate_existing=True)
            )
        ).one_or_none()
        if state is None:
            return CalendarKnowledge()
        rows = await self.calendars()
        failed = any(row.read_failed for row in rows)
        coverage = None
        if (
            state.list_succeeded
            and not rows
            and all(
                value is not None
                for value in (state.first_day, state.last_day, state.time_zone, state.listed_at_utc)
            )
        ):
            assert state.first_day is not None and state.last_day is not None
            assert state.listed_at_utc is not None
            coverage = GoogleCoverage(
                first_day=state.first_day,
                last_day=state.last_day,
                synced_at_utc=state.listed_at_utc,
                time_zone=state.time_zone,
            )
        elif rows and not failed:
            windows = [
                (
                    row.successful_from,
                    row.successful_until,
                    row.successful_at_utc,
                    row.successful_zone,
                )
                for row in rows
            ]
            if all(all(value is not None for value in window) for window in windows):
                starts = [row.successful_from for row in rows if row.successful_from is not None]
                ends = [row.successful_until for row in rows if row.successful_until is not None]
                times = [row.successful_at_utc for row in rows if row.successful_at_utc is not None]
                zones = {row.successful_zone for row in rows}
                if len(zones) == 1 and max(starts) <= min(ends):
                    coverage = GoogleCoverage(
                        first_day=max(starts),
                        last_day=min(ends),
                        synced_at_utc=min(times),
                        time_zone=zones.pop(),
                    )
        return CalendarKnowledge(
            generation=state.generation,
            list_succeeded=state.list_succeeded,
            failed=failed,
            calendars=len(rows),
            coverage=coverage,
            disconnected=(not state.list_succeeded and state.listed_at_utc is None),
        )

    async def coverage(self) -> GoogleCoverage | None:
        knowledge = await self.knowledge()
        return knowledge.coverage if knowledge.list_succeeded and not knowledge.failed else None

    async def clear(self) -> None:
        """Forget every cached calendar and event."""
        await self.list_failed()
        state = (await self._session.scalars(select(GoogleCalendarStateTable))).one()
        state.listed_at_utc = None  # Explicit disconnect; a failed read has an attempt timestamp.
        state.first_day = state.last_day = None
        state.time_zone = None
        await self._session.execute(delete(GoogleEventTable))
        await self._session.execute(delete(GoogleCalendarTable))
