"""Read the owner's Google calendars into the cache the Calendar page shows (ADR 0018).

Read-only: nothing here can change a Google calendar. A sync lists the calendars, then reads
each shown calendar's events for one window of local days around the day asked for, at most
CONCURRENCY at a time, committing per calendar. A calendar's cache is replaced only by a
complete read of it; one that fails keeps what it had. Logs carry counts and codes only.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.common import normalize_utc
from mailbrief.domain.google_calendar import (
    CalendarSyncResult,
    GoogleCalendarInfo,
    GoogleCoverage,
    GoogleEvent,
)
from mailbrief.ports.calendar import CalendarReader
from mailbrief.ports.errors import (
    AuthenticationRequiredError,
    MessageUnavailableError,
    ProviderError,
    ProviderPermissionError,
    ProviderRateLimitError,
)
from mailbrief.services.calendar import day_window
from mailbrief.services.sync import sanitize_error_code
from mailbrief.storage.google_calendar import GoogleCalendarRepository

logger = logging.getLogger(__name__)

WEEKS_BEFORE: Final = 1
WEEKS_AFTER: Final = 8
STALE_AFTER: Final = timedelta(minutes=15)
CONCURRENCY: Final = 3
_STOPPING: Final = (AuthenticationRequiredError, ProviderPermissionError, ProviderRateLimitError)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def sync_window(day: date) -> tuple[date, date]:
    """The days one sync reads: from the Monday a week before ``day``'s week through the
    Sunday eight weeks after it, ten weeks in all."""
    monday = day - timedelta(days=day.weekday())
    first = monday - timedelta(weeks=WEEKS_BEFORE)
    return first, monday + timedelta(weeks=WEEKS_AFTER + 1) - timedelta(days=1)


def needs_sync(
    coverage: GoogleCoverage | None, first_day: date, last_day: date, now: datetime
) -> bool:
    """Whether the cache misses these days or is older than STALE_AFTER."""
    return (
        coverage is None
        or not coverage.covers(first_day, last_day)
        or normalize_utc(now) - coverage.synced_at_utc >= STALE_AFTER
    )


class CalendarSyncService:
    def __init__(
        self,
        session: AsyncSession,
        reader: CalendarReader,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._session = session
        self._reader = reader
        self._clock = clock
        self._repository = GoogleCalendarRepository(session)

    async def sync(self, around: date, zone: ZoneInfo) -> CalendarSyncResult:
        """Read the window around ``around`` (sync_window) in ``zone``.

        Sign-in, permission and rate-limit errors while listing calendars raise; once
        events are being read they stop the sync and become ``stopped_code``. A calendar
        that is gone loses its cache; any other failure leaves its cache as it was.
        """
        first_day, last_day = sync_window(around)
        start_utc = day_window(first_day, zone).start_utc
        end_utc = day_window(last_day, zone).end_utc
        calendars = await self._reader.list_calendars()
        now = normalize_utc(self._clock())
        try:
            rows = await self._repository.replace_calendars(calendars, now)
            await self._session.commit()
        except BaseException:
            await self._session.rollback()
            raise
        shown = [calendar for calendar in calendars if calendar.shown]
        slots = asyncio.Semaphore(CONCURRENCY)
        stop = asyncio.Event()

        async def read(
            calendar: GoogleCalendarInfo,
        ) -> tuple[GoogleCalendarInfo, tuple[GoogleEvent, ...] | ProviderError | None]:
            async with slots:
                if stop.is_set():
                    return calendar, None
                try:
                    return calendar, await self._reader.list_events(calendar, start_utc, end_utc)
                except ProviderError as exc:
                    if isinstance(exc, _STOPPING):
                        stop.set()
                    return calendar, exc

        synced = failed = events = 0
        stopped: str | None = None
        tasks = [asyncio.create_task(read(calendar)) for calendar in shown]
        try:
            for finished in asyncio.as_completed(tasks):
                calendar, outcome = await finished
                if outcome is None:
                    continue
                if isinstance(outcome, _STOPPING):
                    stopped = stopped or sanitize_error_code(outcome)
                    continue
                if isinstance(outcome, MessageUnavailableError):
                    outcome = ()  # Gone: nothing of it is left to show.
                if isinstance(outcome, ProviderError):
                    failed += 1
                    continue
                try:
                    await self._repository.replace_events(
                        rows[calendar.calendar_id], outcome, first_day, last_day, now
                    )
                    await self._session.commit()
                except BaseException:
                    await self._session.rollback()
                    raise
                synced += 1
                events += len(outcome)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(
            "Calendar sync: %d calendars, %d read, %d failed, %d events%s",
            len(shown),
            synced,
            failed,
            events,
            f", stopped: {stopped}" if stopped else "",
        )
        return CalendarSyncResult(
            calendars=len(shown), synced=synced, failed=failed, events=events, stopped_code=stopped
        )

    async def clear(self) -> None:
        """Forget the cache, as disconnecting Google Calendar does."""
        await forget_cache(self._session)


async def forget_cache(session: AsyncSession) -> None:
    """Forget every cached Google calendar and event; no reader or network is needed."""
    try:
        await GoogleCalendarRepository(session).clear()
        await session.commit()
    except BaseException:
        await session.rollback()
        raise
