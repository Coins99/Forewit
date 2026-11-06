"""The owner's calendar events, and the open actions' dates shown beside them (ADR 0018).

An event is the owner's own and is either timed, from one instant to a later one, or
all-day, from one local day through another. A timed event keeps UTC instants and the IANA
zone it was entered in; an all-day event keeps plain dates, so it stays on the same days
whatever zone the owner is in. Action deadlines and target dates are markers, never stored
as events, so moving an event can never change a deadline.

An event is busy or free (ADR 0029): a busy one takes the time it covers, a free one is
only shown. Unless the owner chooses, a timed event is busy and an all-day one free, so an
all-day Busy event is a day off.
"""

import unicodedata
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Final, Self
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, field_validator, model_validator

from mailbrief.domain.actions import (
    ACTION_TITLE_MAX_CHARS,
    PERSON_MAX_CHARS,
    PUBLIC_ID_CHARS,
    ActionStatus,
)
from mailbrief.domain.common import (
    ZONE_KEY_MAX_CHARS,
    DomainModel,
    busy_by_shape,
    known_zone,
    normalize_utc,
)
from mailbrief.domain.google_calendar import GoogleCoverage, GoogleEvent
from mailbrief.domain.work_blocks import WorkBlock
from mailbrief.domain.working_hours import WorkingHours

EVENT_TITLE_MAX_CHARS: Final = 200
EVENT_NOTES_MAX_CHARS: Final = 10_000
EVENT_MAX_DAYS: Final = 31  # The longest an event may last.
_EARLIEST: Final = datetime.min.replace(tzinfo=UTC)


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
    # Whether the event takes its time (ADR 0029); left out, it follows the shape.
    busy: bool = True

    @model_validator(mode="before")
    @classmethod
    def _busy_default(cls, data: object) -> object:
        return busy_by_shape(data)

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
    busy: bool = True  # Left out, it follows the shape (ADR 0029).

    @model_validator(mode="before")
    @classmethod
    def _busy_default(cls, data: object) -> object:
        return busy_by_shape(data)

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
            busy=self.busy,
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
    # Who it waits on or is for (B08): the owner's text, shown with the marker only.
    person: str | None = Field(default=None, min_length=1, max_length=PERSON_MAX_CHARS, repr=False)

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


class ScheduledBlock(DomainModel):
    """A work block (ADR 0028) with its task's live title and state, for the calendar to
    show; like a marker, it is never an event."""

    model_config = ConfigDict(hide_input_in_errors=True)

    block: WorkBlock
    action_title: str = Field(min_length=1, max_length=ACTION_TITLE_MAX_CHARS, repr=False)
    action_status: ActionStatus
    waiting: bool = False  # Someone else is doing the task.

    @property
    def public_id(self) -> str:
        return self.block.public_id

    @property
    def action_public_id(self) -> str:
        return self.block.action_public_id

    @property
    def title(self) -> str:
        return self.action_title

    @property
    def starts_at_utc(self) -> datetime:
        return self.block.starts_at_utc

    @property
    def ends_at_utc(self) -> datetime:
        return self.block.ends_at_utc

    @property
    def open(self) -> bool:
        return self.action_status is ActionStatus.OPEN

    @property
    def all_day(self) -> bool:
        """Never: a block always has a start and an end, as a timed event does."""
        return False

    def days(self, zone: ZoneInfo) -> tuple[date, date]:
        return self.block.days(zone)

    def sort_key(self) -> tuple[datetime, datetime, str, str]:
        return (
            self.block.starts_at_utc,
            self.block.ends_at_utc,
            self.action_title.casefold(),
            self.block.public_id,
        )


class CalendarRange(DomainModel):
    """The events touching a run of local days, and the open actions' dates in them.

    ``google_events`` are the cached Google events touching those days, read-only, and
    ``google_coverage`` the days the cache holds (None when nothing is cached). ``blocks``
    are the live work blocks of live tasks touching them (ADR 0028), and ``hours`` the
    owner's working hours as they were read with the days (ADR 0029).
    """

    first_day: date
    last_day: date
    events: tuple[CalendarEvent, ...]
    markers: tuple[ActionMarker, ...]
    google_events: tuple[GoogleEvent, ...] = ()
    google_coverage: GoogleCoverage | None = None
    blocks: tuple[ScheduledBlock, ...] = ()
    hours: WorkingHours = Field(default_factory=WorkingHours.unset)


def wall_clock(day: date, clock: time, zone: ZoneInfo) -> datetime:
    """A wall-clock time in ``zone``. A time a DST change skips is read with the offset
    before the change, as a clock would show it; a repeated time is its first."""
    return datetime.combine(day, clock).replace(tzinfo=zone, fold=0)


class ChangeKind(StrEnum):
    """What one change in a batch does to the owner's events."""

    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class EventChange(DomainModel):
    """One change to apply: a new event's fields, an event and its new fields, or an event
    to delete. ``event`` is the event as it was read, so applying checks its revision."""

    model_config = ConfigDict(hide_input_in_errors=True)

    kind: ChangeKind
    event: CalendarEvent | None = None
    edit: EventEdit | None = None

    @model_validator(mode="after")
    def _fits_kind(self) -> Self:
        wants_event = self.kind is not ChangeKind.CREATE
        wants_edit = self.kind is not ChangeKind.DELETE
        if (self.event is not None) != wants_event or (self.edit is not None) != wants_edit:
            raise ValueError("the change's fields don't fit its kind")
        return self


class AppliedChange(DomainModel):
    """What applying one change left, so that undoing it can check nothing changed since."""

    model_config = ConfigDict(hide_input_in_errors=True)

    kind: ChangeKind
    public_id: str = Field(min_length=PUBLIC_ID_CHARS, max_length=PUBLIC_ID_CHARS)
    revision: int = Field(ge=1)
    previous: EventEdit | None = None  # An update's fields before it.


class AppliedBatch(DomainModel):
    """The changes one apply made, all together; undo reverses all of them or none."""

    changes: tuple[AppliedChange, ...] = Field(min_length=1)
