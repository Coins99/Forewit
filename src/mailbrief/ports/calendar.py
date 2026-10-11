"""The calendar reader a provider adapter supplies to calendar sync (ADR 0018, phase 2)."""

from datetime import datetime
from typing import Protocol

from mailbrief.domain.google_calendar import GoogleCalendarInfo, GoogleEvent


class CalendarReader(Protocol):
    """Read-only access to the owner's calendars; nothing here can change one."""

    async def list_calendars(self) -> tuple[GoogleCalendarInfo, ...]:
        """Every calendar in the owner's list that MailBrief may read."""
        ...

    async def list_events(
        self, calendar: GoogleCalendarInfo, start_utc: datetime, end_utc: datetime
    ) -> tuple[GoogleEvent, ...]:
        """The calendar's events overlapping ``[start_utc, end_utc)``, repeating events
        expanded. Raises MessageUnavailableError when the calendar is gone."""
        ...
