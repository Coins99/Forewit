"""Calendar sync into the cache, and the cached events shown beside the owner's."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.google_calendar import GoogleCalendarInfo, GoogleCoverage, GoogleEvent
from mailbrief.ports.errors import (
    AuthenticationRequiredError,
    MessageUnavailableError,
    ProviderError,
    ProviderRateLimitError,
)
from mailbrief.services.events import EventService
from mailbrief.services.google_calendar import (
    STALE_AFTER,
    CalendarSyncService,
    needs_sync,
    sync_window,
)
from mailbrief.storage.database import Database
from mailbrief.storage.tables import GoogleEventTable

TORONTO = ZoneInfo("America/Toronto")
NOW = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)  # Wednesday.
TODAY = date(2026, 10, 14)
PRIMARY = GoogleCalendarInfo(
    calendar_id="owner@example.com", name="Owner", primary=True, shown=True
)
TEAM = GoogleCalendarInfo(
    calendar_id="team@group.calendar.google.com", name="Team", primary=False, shown=True
)
HIDDEN = GoogleCalendarInfo(
    calendar_id="old@group.calendar.google.com", name="Old", primary=False, shown=False
)


def google(
    calendar: GoogleCalendarInfo, event_id: str, start: datetime, minutes: int = 30
) -> GoogleEvent:
    return GoogleEvent(
        calendar_id=calendar.calendar_id,
        calendar_name=calendar.name,
        event_id=event_id,
        title=f"Event {event_id}",
        all_day=False,
        starts_at_utc=start,
        ends_at_utc=start + timedelta(minutes=minutes),
    )


class FakeReader:
    def __init__(self) -> None:
        self.calendars: tuple[GoogleCalendarInfo, ...] = (PRIMARY, TEAM, HIDDEN)
        self.events: dict[str, tuple[GoogleEvent, ...] | ProviderError] = {}
        self.list_fail: ProviderError | None = None
        self.read: list[tuple[str, datetime, datetime]] = []

    async def list_calendars(self) -> tuple[GoogleCalendarInfo, ...]:
        if self.list_fail is not None:
            raise self.list_fail
        return self.calendars

    async def list_events(
        self, calendar: GoogleCalendarInfo, start_utc: datetime, end_utc: datetime
    ) -> tuple[GoogleEvent, ...]:
        self.read.append((calendar.calendar_id, start_utc, end_utc))
        found = self.events.get(calendar.calendar_id, ())
        if isinstance(found, ProviderError):
            raise found
        return found


@pytest.fixture
async def session(tmp_path: Path) -> AsyncIterator[AsyncSession]:
    database = Database.from_path(tmp_path / "google.sqlite3")
    await database.create_schema_for_tests()
    try:
        async with database.session() as session:
            yield session
    finally:
        await database.dispose()


@pytest.fixture
def reader() -> FakeReader:
    return FakeReader()


@pytest.fixture
def service(session: AsyncSession, reader: FakeReader) -> CalendarSyncService:
    return CalendarSyncService(session, reader, clock=lambda: NOW)


async def cached(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(GoogleEventTable)) or 0)


def test_sync_window_is_ten_weeks_from_the_week_before() -> None:
    assert sync_window(TODAY) == (date(2026, 10, 5), date(2026, 12, 13))
    assert sync_window(date(2026, 10, 12)) == (date(2026, 10, 5), date(2026, 12, 13))


def test_needs_sync() -> None:
    coverage = GoogleCoverage(
        first_day=date(2026, 10, 5), last_day=date(2026, 12, 13), synced_at_utc=NOW
    )
    week = (date(2026, 10, 12), date(2026, 10, 18))
    assert needs_sync(None, *week, NOW)
    assert not needs_sync(coverage, *week, NOW + STALE_AFTER - timedelta(seconds=1))
    assert needs_sync(coverage, *week, NOW + STALE_AFTER)
    assert needs_sync(coverage, date(2026, 12, 7), date(2026, 12, 20), NOW)


async def test_sync_reads_shown_calendars_for_the_window(
    service: CalendarSyncService, reader: FakeReader, session: AsyncSession
) -> None:
    standup = datetime(2026, 10, 14, 13, 0, tzinfo=UTC)
    reader.events = {
        PRIMARY.calendar_id: (google(PRIMARY, "a", standup), google(PRIMARY, "a", standup)),
        TEAM.calendar_id: (google(TEAM, "b", standup + timedelta(hours=2)),),
        HIDDEN.calendar_id: (google(HIDDEN, "c", standup),),
    }
    result = await service.sync(TODAY, TORONTO)
    assert (result.calendars, result.synced, result.failed, result.events) == (2, 2, 0, 3)
    assert result.stopped_code is None
    assert {call[0] for call in reader.read} == {PRIMARY.calendar_id, TEAM.calendar_id}
    # The window is local days in the owner's zone: Monday 5 Oct 00:00 in Toronto onwards.
    assert reader.read[0][1] == datetime(2026, 10, 5, 4, 0, tzinfo=UTC)
    assert reader.read[0][2] == datetime(2026, 12, 14, 5, 0, tzinfo=UTC)
    assert await cached(session) == 2  # A repeated event ID is kept once.

    shown = await EventService(session).list_range(date(2026, 10, 12), date(2026, 10, 18), TORONTO)
    assert [event.event_id for event in shown.google_events] == ["a", "b"]
    assert shown.google_coverage == GoogleCoverage(
        first_day=date(2026, 10, 5), last_day=date(2026, 12, 13), synced_at_utc=NOW
    )
    outside = await EventService(session).list_range(date(2027, 1, 4), date(2027, 1, 10), TORONTO)
    assert outside.google_events == ()


async def test_failed_calendar_keeps_its_cache_and_gone_one_loses_it(
    service: CalendarSyncService, reader: FakeReader, session: AsyncSession
) -> None:
    start = datetime(2026, 10, 14, 13, 0, tzinfo=UTC)
    reader.events = {
        PRIMARY.calendar_id: (google(PRIMARY, "a", start),),
        TEAM.calendar_id: (google(TEAM, "b", start),),
    }
    await service.sync(TODAY, TORONTO)
    reader.events = {
        PRIMARY.calendar_id: ProviderError("temporary"),
        TEAM.calendar_id: MessageUnavailableError("gone"),
    }
    result = await service.sync(TODAY, TORONTO)
    assert (result.synced, result.failed, result.events) == (1, 1, 0)
    shown = await EventService(session).list_range(TODAY, TODAY, TORONTO)
    assert [event.event_id for event in shown.google_events] == ["a"]


async def test_stopping_errors_end_the_sync(
    service: CalendarSyncService, reader: FakeReader
) -> None:
    reader.events = {
        PRIMARY.calendar_id: ProviderRateLimitError("slow down"),
        TEAM.calendar_id: ProviderRateLimitError("slow down"),
    }
    result = await service.sync(TODAY, TORONTO)
    assert result.stopped_code == "RATE_LIMITED" and result.synced == 0
    reader.list_fail = AuthenticationRequiredError("reconnect")
    with pytest.raises(AuthenticationRequiredError):
        await service.sync(TODAY, TORONTO)


async def test_dropped_calendars_and_clear_forget_events(
    service: CalendarSyncService, reader: FakeReader, session: AsyncSession
) -> None:
    start = datetime(2026, 10, 14, 13, 0, tzinfo=UTC)
    reader.events = {TEAM.calendar_id: (google(TEAM, "b", start),)}
    await service.sync(TODAY, TORONTO)
    assert await cached(session) == 1
    reader.calendars = (PRIMARY,)
    await service.sync(TODAY, TORONTO)
    assert await cached(session) == 0
    reader.events = {PRIMARY.calendar_id: (google(PRIMARY, "a", start),)}
    await service.sync(TODAY, TORONTO)
    await service.clear()
    assert await cached(session) == 0
    assert (await EventService(session).list_range(TODAY, TODAY, TORONTO)).google_coverage is None


async def test_coverage_needs_every_shown_calendar(
    service: CalendarSyncService, reader: FakeReader, session: AsyncSession
) -> None:
    reader.events = {TEAM.calendar_id: ProviderError("temporary")}
    await service.sync(TODAY, TORONTO)
    assert (await EventService(session).list_range(TODAY, TODAY, TORONTO)).google_coverage is None


async def test_zero_length_and_all_day_events_show_on_their_days(
    service: CalendarSyncService, reader: FakeReader, session: AsyncSession
) -> None:
    point = google(PRIMARY, "p", datetime(2026, 10, 14, 4, 0, tzinfo=UTC), minutes=0)
    trip = GoogleEvent(
        calendar_id=PRIMARY.calendar_id,
        calendar_name="Owner",
        event_id="t",
        title="Trip",
        all_day=True,
        start_date=date(2026, 10, 15),
        last_date=date(2026, 10, 16),
    )
    reader.events = {PRIMARY.calendar_id: (point, trip)}
    await service.sync(TODAY, TORONTO)
    wednesday = await EventService(session).list_range(TODAY, TODAY, TORONTO)
    assert [event.event_id for event in wednesday.google_events] == ["p"]
    friday = await EventService(session).list_range(date(2026, 10, 16), date(2026, 10, 16), TORONTO)
    assert [event.event_id for event in friday.google_events] == ["t"]


async def test_default_clock_is_utc_now(session: AsyncSession, reader: FakeReader) -> None:
    result = await CalendarSyncService(session, reader).sync(TODAY, TORONTO)
    assert result.calendars == 2
