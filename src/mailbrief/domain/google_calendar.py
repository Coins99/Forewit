"""Events from the owner's Google calendars, cached only to show them (ADR 0018, phase 2).

They are Google's data, not the owner's: MailBrief never changes them and keeps only what
the Calendar page shows: titles, times, links and calendar names. Descriptions, locations,
guests and conference details are never requested.
"""

from datetime import UTC, date, datetime, timedelta
from typing import Final, Self
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, HttpUrl, ValidationError, field_validator, model_validator

from mailbrief.domain.common import DomainModel, normalize_utc

GOOGLE_ID_MAX_CHARS: Final = 1024
GOOGLE_TITLE_MAX_CHARS: Final = 200
CALENDAR_NAME_MAX_CHARS: Final = 120
GOOGLE_EVENT_MAX_DAYS: Final = 366  # Longer events, such as all-year ones, are skipped.
UNTITLED: Final = "(No title)"
_EARLIEST: Final = datetime.min.replace(tzinfo=UTC)
# Each host's allowed path prefix. Other paths on www.google.com include a redirector.
_LINK_PREFIXES: Final = {"calendar.google.com": "/", "www.google.com": "/calendar/"}


def is_calendar_link(url: str) -> bool:
    """Only https links to Google Calendar are kept and offered for opening: any page on
    calendar.google.com, and /calendar on www.google.com, so never Google's redirector.

    The link is read as a browser reads it, with dot segments resolved, so
    "/calendar/../url" doesn't pass; a user name or a port other than 443 is refused.
    """
    try:
        link = HttpUrl(url)
    except ValidationError:
        return False
    prefix = _LINK_PREFIXES.get(link.host or "")
    return (
        link.scheme == "https"
        and prefix is not None
        and link.port == 443
        and f"{link.path or ''}/".startswith(prefix)
        and not link.username
        and not link.password
    )


class GoogleCalendarInfo(DomainModel):
    """One calendar in the owner's Google calendar list."""

    model_config = ConfigDict(hide_input_in_errors=True)

    calendar_id: str = Field(min_length=1, max_length=GOOGLE_ID_MAX_CHARS)
    name: str = Field(min_length=1, max_length=CALENDAR_NAME_MAX_CHARS, repr=False)
    primary: bool
    shown: bool  # Shown in Google Calendar's own list, so shown here.


class GoogleEvent(DomainModel):
    """One event (or one instance of a repeating event) on a Google calendar."""

    model_config = ConfigDict(hide_input_in_errors=True)

    calendar_id: str = Field(min_length=1, max_length=GOOGLE_ID_MAX_CHARS)
    calendar_name: str = Field(min_length=1, max_length=CALENDAR_NAME_MAX_CHARS, repr=False)
    event_id: str = Field(min_length=1, max_length=GOOGLE_ID_MAX_CHARS)
    title: str = Field(min_length=1, max_length=GOOGLE_TITLE_MAX_CHARS, repr=False)
    all_day: bool
    starts_at_utc: datetime | None = None
    ends_at_utc: datetime | None = None
    start_date: date | None = None
    last_date: date | None = None
    web_link: HttpUrl | None = None

    @field_validator("starts_at_utc", "ends_at_utc")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_utc(value)

    @model_validator(mode="after")
    def _one_shape(self) -> Self:
        if self.all_day:
            if self.start_date is None or self.last_date is None:
                raise ValueError("an all-day event has a first and a last day")
            if self.starts_at_utc is not None or self.ends_at_utc is not None:
                raise ValueError("an all-day event has no times")
            if (
                not timedelta(0)
                <= self.last_date - self.start_date
                < timedelta(days=GOOGLE_EVENT_MAX_DAYS)
            ):
                raise ValueError("the event's days are out of bounds")
            return self
        if self.starts_at_utc is None or self.ends_at_utc is None:
            raise ValueError("a timed event has a start and an end")
        if self.start_date is not None or self.last_date is not None:
            raise ValueError("a timed event has no days")
        if (
            not timedelta(0)
            <= self.ends_at_utc - self.starts_at_utc
            <= timedelta(days=GOOGLE_EVENT_MAX_DAYS)
        ):
            raise ValueError("the event's times are out of bounds")
        return self

    def days(self, zone: ZoneInfo) -> tuple[date, date]:
        """The first and last day, in ``zone``, that the event touches.

        A zero-length event touches its start day; one ending exactly at midnight does not
        touch the day that begins then.
        """
        if self.start_date is not None and self.last_date is not None:
            return self.start_date, self.last_date
        assert self.starts_at_utc is not None and self.ends_at_utc is not None
        first = self.starts_at_utc.astimezone(zone).date()
        end = max(self.ends_at_utc - timedelta(microseconds=1), self.starts_at_utc)
        return first, end.astimezone(zone).date()

    def sort_key(self) -> tuple[bool, date, datetime, str, str, str]:
        """All-day events first, then by start, title and IDs."""
        return (
            not self.all_day,
            self.start_date or date.min,
            self.starts_at_utc or _EARLIEST,
            self.title.casefold(),
            self.calendar_id,
            self.event_id,
        )


class GoogleCoverage(DomainModel):
    """Which local days the cache holds Google events for, and when it was filled."""

    first_day: date
    last_day: date
    synced_at_utc: datetime

    @field_validator("synced_at_utc")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return normalize_utc(value)

    def covers(self, first_day: date, last_day: date) -> bool:
        return self.first_day <= first_day and last_day <= self.last_day


class CalendarSyncResult(DomainModel):
    """What one sync did: calendars listed, read and failed, events cached, and the error
    code that stopped it, if any."""

    calendars: int = Field(ge=0)
    synced: int = Field(ge=0)
    failed: int = Field(ge=0)
    events: int = Field(ge=0)
    stopped_code: str | None = None
