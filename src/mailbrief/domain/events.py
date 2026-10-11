"""The owner's calendar events, and the open actions' dates shown beside them (ADR 0018).

An event is the owner's own and is either timed, from one instant to a later one, or
all-day, from one local day through another. A timed event keeps UTC instants and the IANA
zone it was entered in; an all-day event keeps plain dates, so it stays on the same days
whatever zone the owner is in. Action deadlines and target dates are markers, never stored
as events, so moving an event can never change a deadline.
"""

import unicodedata
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Final, Self
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, field_validator, model_validator

from mailbrief.domain.actions import ACTION_TITLE_MAX_CHARS, PUBLIC_ID_CHARS
from mailbrief.domain.common import DomainModel, normalize_utc

EVENT_TITLE_MAX_CHARS: Final = 200
EVENT_NOTES_MAX_CHARS: Final = 10_000
EVENT_MAX_DAYS: Final = 31  # The longest an event may last.
ZONE_KEY_MAX_CHARS: Final = 64
_EARLIEST: Final = datetime.min.replace(tzinfo=UTC)


def known_zone(key: str) -> str:
    """``key`` when it names an IANA time zone; raises ValueError with a static message."""
    try:
        ZoneInfo(key)
    except (KeyError, ValueError, OSError):
        raise ValueError("the time zone is unknown") from None
    return key


def _one_line(text: str) -> str:
    if any(unicodedata.category(ch) == "Cc" for ch in text):
        raise ValueError("the title must be one line of text")
    return text


def check_timing(
    all_day: bool,
    starts_at_utc: datetime | None,
    ends_at_utc: datetime | None,
    start_date: date | None,
    last_date: date | None,
) -> None:
    """Raise ValueError, with a static message, unless the fields describe one event shape.

    An all-day event has a first and a last day and no times; a timed event has a start
    and a later end and no days. Either lasts at most EVENT_MAX_DAYS.
    """
    if all_day:
        if start_date is None or last_date is None:
            raise ValueError("an all-day event has a first and a last day, and no times")
        if starts_at_utc is not None or ends_at_utc is not None:
            raise ValueError("an all-day event has a first and a last day, and no times")
        if last_date < start_date:
            raise ValueError("an event can't end before it starts")
        if (last_date - start_date).days >= EVENT_MAX_DAYS:
            raise ValueError("an event can last at most 31 days")
        return
    if starts_at_utc is None or ends_at_utc is None:
        raise ValueError("a timed event has a start and an end, and no days")
    if start_date is not None or last_date is not None:
        raise ValueError("a timed event has a start and an end, and no days")
    if ends_at_utc <= starts_at_utc:
        raise ValueError("an event must end after it starts")
    if ends_at_utc - starts_at_utc > timedelta(days=EVENT_MAX_DAYS):
        raise ValueError("an event can last at most 31 days")


class EventEdit(DomainModel):
    """The owner's full set of fields for a new or changed event."""

    model_config = ConfigDict(hide_input_in_errors=True)

    title: str = Field(min_length=1, max_length=EVENT_TITLE_MAX_CHARS, repr=False)
    notes: str = Field(default="", max_length=EVENT_NOTES_MAX_CHARS, repr=False)
    all_day: bool
    starts_at_utc: datetime | None = None
    ends_at_utc: datetime | None = None
    start_date: date | None = None
    last_date: date | None = None
    time_zone: str = Field(min_length=1, max_length=ZONE_KEY_MAX_CHARS)

    @field_validator("title")
    @classmethod
    def _title_is_one_line(cls, value: str) -> str:
        return _one_line(value)

    @field_validator("starts_at_utc", "ends_at_utc")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_utc(value)

    @field_validator("time_zone")
    @classmethod
    def _zone(cls, value: str) -> str:
        return known_zone(value)

    @model_validator(mode="after")
    def _one_shape(self) -> Self:
        check_timing(
            self.all_day, self.starts_at_utc, self.ends_at_utc, self.start_date, self.last_date
        )
        return self


class CalendarEvent(DomainModel):
    """One of the owner's events, as stored."""

    model_config = ConfigDict(hide_input_in_errors=True)

    public_id: str = Field(min_length=PUBLIC_ID_CHARS, max_length=PUBLIC_ID_CHARS)
    title: str = Field(min_length=1, max_length=EVENT_TITLE_MAX_CHARS, repr=False)
    notes: str = Field(max_length=EVENT_NOTES_MAX_CHARS, repr=False)
    all_day: bool
    starts_at_utc: datetime | None
    ends_at_utc: datetime | None
    start_date: date | None
    last_date: date | None
    time_zone: str = Field(min_length=1, max_length=ZONE_KEY_MAX_CHARS)
    revision: int = Field(ge=1)
    created_at_utc: datetime
    updated_at_utc: datetime

    @field_validator("starts_at_utc", "ends_at_utc", "created_at_utc", "updated_at_utc")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_utc(value)

    @model_validator(mode="after")
    def _one_shape(self) -> Self:
        check_timing(
            self.all_day, self.starts_at_utc, self.ends_at_utc, self.start_date, self.last_date
        )
        return self

    def days(self, zone: ZoneInfo) -> tuple[date, date]:
        """The first and last day, in ``zone``, that the event touches.

        A timed event ending exactly at midnight does not touch the day that begins then.
        An all-day event keeps its own days in every zone.
        """
        if self.start_date is not None and self.last_date is not None:
            return self.start_date, self.last_date
        assert self.starts_at_utc is not None and self.ends_at_utc is not None
        first = self.starts_at_utc.astimezone(zone).date()
        last = (self.ends_at_utc - timedelta(microseconds=1)).astimezone(zone).date()
        return first, last

    def edit(self) -> EventEdit:
        """The event's own fields, to change and save."""
        return EventEdit(
            title=self.title,
            notes=self.notes,
            all_day=self.all_day,
            starts_at_utc=self.starts_at_utc,
            ends_at_utc=self.ends_at_utc,
            start_date=self.start_date,
            last_date=self.last_date,
            time_zone=self.time_zone,
        )

    def sort_key(self) -> tuple[bool, date, datetime, str, str]:
        """All-day events first, then by start, title and ID."""
        return (
            not self.all_day,
            self.start_date or date.min,
            self.starts_at_utc or _EARLIEST,
            self.title.casefold(),
            self.public_id,
        )


class MarkerKind(StrEnum):
    """Which of an open action's dates a marker shows."""

    DEADLINE = "deadline"
    TARGET = "target"


class ActionMarker(DomainModel):
    """An open action's deadline or target date on one local day; never an event."""

    model_config = ConfigDict(hide_input_in_errors=True)

    kind: MarkerKind
    day: date
    due_at_utc: datetime | None = None  # A deadline with a time of day.
    action_public_id: str = Field(min_length=PUBLIC_ID_CHARS, max_length=PUBLIC_ID_CHARS)
    action_title: str = Field(min_length=1, max_length=ACTION_TITLE_MAX_CHARS, repr=False)
    waiting: bool = False  # Someone else is doing it.

    @field_validator("due_at_utc")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_utc(value)

    def sort_key(self) -> tuple[date, bool, datetime, str, str]:
        """By day, deadlines first, then by time, title and ID."""
        return (
            self.day,
            self.kind is not MarkerKind.DEADLINE,
            self.due_at_utc or _EARLIEST,
            self.action_title.casefold(),
            self.action_public_id,
        )


class CalendarRange(DomainModel):
    """The events touching a run of local days, and the open actions' dates in them."""

    first_day: date
    last_day: date
    events: tuple[CalendarEvent, ...]
    markers: tuple[ActionMarker, ...]
