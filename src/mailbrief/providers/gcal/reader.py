"""The CalendarReader over Google Calendar: list calendars, read events, nothing else."""

from datetime import datetime

from mailbrief.domain.google_calendar import GoogleCalendarInfo, GoogleEvent
from mailbrief.providers.gcal.auth import CalendarAuth
from mailbrief.providers.gcal.client import CalendarClient
from mailbrief.providers.gcal.errors import calendar_response_error
from mailbrief.providers.gcal.mapper import map_calendar, map_event

# The most calendars listed: the primary first, then those shown in Google Calendar, so
# the cap never drops a shown calendar in favour of one the owner left unticked.
MAX_CALENDARS = 20


class GoogleCalendarReader:
    def __init__(self, auth: CalendarAuth, client: CalendarClient) -> None:
        self._auth = auth
        self._client = client

    @property
    def auth(self) -> CalendarAuth:
        return self._auth

    async def list_calendars(self) -> tuple[GoogleCalendarInfo, ...]:
        """The readable calendars, each once. A list without exactly one primary calendar is
        an invalid answer, so the sync stops before it changes the cache."""
        found: dict[str, GoogleCalendarInfo] = {}
        for item in await self._client.calendars():
            mapped = map_calendar(item)
            if mapped is not None:
                found.setdefault(mapped.calendar_id, mapped)  # A repeat is kept once.
        if sum(calendar.primary for calendar in found.values()) != 1:
            raise calendar_response_error("Google returned no primary calendar.")
        ordered = sorted(
            found.values(),
            key=lambda calendar: (
                not calendar.primary,
                not calendar.shown,
                calendar.name.casefold(),
            ),
        )
        return tuple(ordered[:MAX_CALENDARS])

    async def list_events(
        self, calendar: GoogleCalendarInfo, start_utc: datetime, end_utc: datetime
    ) -> tuple[GoogleEvent, ...]:
        items = await self._client.events(calendar.calendar_id, start_utc, end_utc)
        return tuple(mapped for item in items if (mapped := map_event(item, calendar)) is not None)
