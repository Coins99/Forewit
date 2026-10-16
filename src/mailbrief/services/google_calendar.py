"""Read the owner's Google calendars into the cache the Calendar page shows (ADR 0018).

Read-only: nothing here can change a Google calendar. A sync lists the calendars, then reads
each shown calendar's events for one window of local days around the day asked for, at most
CONCURRENCY at a time, committing per calendar. A calendar's cache is replaced only by a
complete read of it; one that fails keeps what it had but counts as checked, so a calendar
that keeps failing is tried again when the window goes stale, not on every look. Logs carry
counts and codes only.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.common import normalize_utc, utc_now
from mailbrief.domain.google_calendar import (
    CalendarSyncResult,
    GoogleCalendarInfo,
    GoogleCoverage,
    GoogleEvent,
)
from mailbrief.infra.tasks import running
from mailbrief.ports.calendar import CalendarReader
from mailbrief.ports.errors import (
    AuthenticationRequiredError,
    MessageUnavailableError,
    ProviderError,
    ProviderRateLimitError,
)
from mailbrief.services.calendar import day_window
from mailbrief.services.sync import sanitize_error_code
from mailbrief.services.writes import committed
from mailbrief.storage.google_calendar import GoogleCalendarRepository

logger = logging.getLogger(__name__)

WEEKS_BEFORE: Final = 1
WEEKS_AFTER: Final = 8
STALE_AFTER: Final = timedelta(minutes=15)
CONCURRENCY: Final = 3
# Account-wide errors end a sync. Any other failure, a calendar Google refuses included,
# is that calendar's alone: the token already holds both calendar permissions.
_STOPPING: Final = (AuthenticationRequiredError, ProviderRateLimitError)


def sync_window(day: date) -> tuple[date, date]:
    """The days one sync reads: from the Monday a week before ``day``'s week through the
    Sunday eight weeks after it, ten weeks in all."""
    monday = day - timedelta(days=day.weekday())
    first = monday - timedelta(weeks=WEEKS_BEFORE)
    return first, monday + timedelta(weeks=WEEKS_AFTER + 1) - timedelta(days=1)


def shown_calendars(calendars: Sequence[GoogleCalendarInfo]) -> list[GoogleCalendarInfo]:
    """The calendars shown in Google Calendar, each once, in the reader's order."""
    seen: set[str] = set()
    shown: list[GoogleCalendarInfo] = []
    for calendar in calendars:
        if calendar.shown and calendar.calendar_id not in seen:
            seen.add(calendar.calendar_id)
            shown.append(calendar)
    return shown


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
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._session = session
        self._reader = reader
        self._clock = clock
        self._repository = GoogleCalendarRepository(session)

    async def sync(self, around: date, zone: ZoneInfo) -> CalendarSyncResult:
        """Read the window around ``around`` (sync_window) in ``zone``.

        Errors while listing calendars raise. Once events are being read, a sign-in or
        rate-limit error stops the sync and becomes ``stopped_code``; after a rate limit the
        calendars not read count as checked, so the next try waits for the window to go
        stale. A calendar that is gone loses its cache; any other failure leaves its events
        as they were. Calendars no longer shown in Google go with their events.
        """
        first_day, last_day = sync_window(around)
        start_utc = day_window(first_day, zone).start_utc
        end_utc = day_window(last_day, zone).end_utc
        shown = shown_calendars(await self._reader.list_calendars())
        now = normalize_utc(self._clock())
        rows = await self._saved(self._repository.replace_calendars(shown, now))
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
        throttled = False
        done: set[str] = set()
        async with running(read(calendar) for calendar in shown) as tasks:
            for finished in asyncio.as_completed(tasks):
                calendar, outcome = await finished
                if outcome is None:
                    continue
                if isinstance(outcome, _STOPPING):
                    stopped = stopped or sanitize_error_code(outcome)
                    throttled = throttled or isinstance(outcome, ProviderRateLimitError)
                    continue
                row = rows[calendar.calendar_id]
                if isinstance(outcome, MessageUnavailableError):
                    outcome = ()  # Gone: nothing of it is left to show.
                if isinstance(outcome, ProviderError):
                    failed += 1
                    await self._saved(
                        self._repository.mark_checked((row,), first_day, last_day, now)
                    )
                else:
                    events += await self._saved(
                        self._repository.replace_events(row, outcome, first_day, last_day, now)
                    )
                    synced += 1
                done.add(calendar.calendar_id)
        if throttled:
            waiting = [rows[c.calendar_id] for c in shown if c.calendar_id not in done]
            await self._saved(self._repository.mark_checked(waiting, first_day, last_day, now))
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

    async def _saved[T](self, change: Awaitable[T]) -> T:
        """Commit one change to the cache, or roll it back and raise."""
        return await committed(self._session, change)

    async def clear(self) -> None:
        """Forget the cache, as disconnecting Google Calendar does."""
        await forget_cache(self._session)


async def forget_cache(session: AsyncSession) -> None:
    """Forget every cached Google calendar and event; no reader or network is needed."""
    await committed(session, GoogleCalendarRepository(session).clear())
