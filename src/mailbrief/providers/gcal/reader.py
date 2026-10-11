"""The CalendarReader over Google Calendar: list calendars, read events, nothing else."""

from datetime import datetime

from mailbrief.domain.google_calendar import GoogleCalendarInfo, GoogleEvent
from mailbrief.providers.gcal.auth import CalendarAuth
from mailbrief.providers.gcal.client import CalendarClient
from mailbrief.providers.gcal.mapper import map_calendar, map_event

MAX_CALENDARS = 20  # The most calendars read; the primary one always comes first.


class GoogleCalendarReader:
    def __init__(self, auth: CalendarAuth, client: CalendarClient) -> None:
        self._auth = auth
        self._client = client

    @property
    def auth(self) -> CalendarAuth:
        return self._auth

    async def list_calendars(self) -> tuple[GoogleCalendarInfo, ...]:
        found = [
            mapped
            for item in await self._client.calendars()
            if (mapped := map_calendar(item)) is not None
        ]
        found.sort(key=lambda calendar: (not calendar.primary, calendar.name.casefold()))
        return tuple(found[:MAX_CALENDARS])

    async def list_events(
        self, calendar: GoogleCalendarInfo, start_utc: datetime, end_utc: datetime
    ) -> tuple[GoogleEvent, ...]:
        items = await self._client.events(calendar.calendar_id, start_utc, end_utc)
        return tuple(mapped for item in items if (mapped := map_event(item, calendar)) is not None)
