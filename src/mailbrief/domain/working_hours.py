"""The owner's weekly working hours and buffer (C04, ADR 0029).

Working hours are wall-clock times in the owner's zone, the same every week: up to three
windows a weekday, each from one quarter hour to a later one of the same day (24:00 is the
day's end). A day off is not a setting: it is an all-day Busy event. The buffer is time kept
clear before and after each busy timed event. Hours that were never set are not set: nothing
is shaded and no free time is worked out until the owner sets them.
"""

from datetime import date, datetime
from typing import Final, Self

from pydantic import Field, field_validator, model_validator

from mailbrief.domain.common import DomainModel, normalize_utc

WEEKDAYS: Final = 7  # Monday first, as date.weekday() counts.
MAX_WINDOWS_PER_DAY: Final = 3
HOURS_STEP_MINUTES: Final = 15
DAY_MINUTES: Final = 24 * 60
BUFFER_CHOICES: Final = (0, 5, 10, 15, 30)
BUFFER_MAX_MINUTES: Final = max(BUFFER_CHOICES)

_ON_STEPS: Final = "Working hours start and end on a quarter hour."
_ORDERED: Final = "Working hours must end after they start."
_SEVEN_DAYS: Final = "Working hours have one entry for each day of the week."
_TOO_MANY: Final = "A day can have at most 3 working hours windows."
_OVERLAP: Final = "A day's working hours windows can't overlap."
_BUFFER: Final = "The buffer is 0, 5, 10, 15 or 30 minutes."


class WorkingWindow(DomainModel):
    """From ``start_minute`` to ``end_minute`` past midnight, in wall-clock time."""

    start_minute: int = Field(ge=0, le=DAY_MINUTES - HOURS_STEP_MINUTES)
    end_minute: int = Field(ge=HOURS_STEP_MINUTES, le=DAY_MINUTES)

    @model_validator(mode="after")
    def _stepped(self) -> Self:
        if self.start_minute % HOURS_STEP_MINUTES or self.end_minute % HOURS_STEP_MINUTES:
            raise ValueError(_ON_STEPS)
        if self.end_minute <= self.start_minute:
            raise ValueError(_ORDERED)
        return self

    @property
    def minutes(self) -> int:
        return self.end_minute - self.start_minute


type DayWindows = tuple[WorkingWindow, ...]


class DatedHours(DomainModel):
    day: date
    windows: DayWindows = ()

    @field_validator("windows")
    @classmethod
    def _windows(cls, value: DayWindows) -> DayWindows:
        # Share the weekly validation, including ordering and the three-window limit.
        return HoursEdit(days=(value, (), (), (), (), (), ())).days[0]


class HoursEdit(DomainModel):
    """The owner's full set of working hours: each weekday's windows, Monday first, and the
    buffer. A day's windows are kept in order; they may touch but not overlap."""

    days: tuple[DayWindows, ...] = ((),) * WEEKDAYS
    buffer_minutes: int = 0
    buffer_before_minutes: int | None = Field(default=None, ge=0, le=120)
    buffer_after_minutes: int | None = Field(default=None, ge=0, le=120)
    overrides: tuple[DatedHours, ...] = Field(default=(), max_length=366)

    @field_validator("overrides")
    @classmethod
    def _unique_dates(cls, value: tuple[DatedHours, ...]) -> tuple[DatedHours, ...]:
        if len({item.day for item in value}) != len(value):
            raise ValueError("Choose each override date once.")
        return tuple(sorted(value, key=lambda item: item.day))

    @property
    def before_minutes(self) -> int:
        return (
            self.buffer_minutes
            if self.buffer_before_minutes is None
            else self.buffer_before_minutes
        )

    @property
    def after_minutes(self) -> int:
        return (
            self.buffer_minutes if self.buffer_after_minutes is None else self.buffer_after_minutes
        )

    def on_day(self, day: date) -> DayWindows:
        return next(
            (item.windows for item in self.overrides if item.day == day),
            self.windows(day.weekday()),
        )

    @field_validator("days")
    @classmethod
    def _week(cls, value: tuple[DayWindows, ...]) -> tuple[DayWindows, ...]:
        if len(value) != WEEKDAYS:
            raise ValueError(_SEVEN_DAYS)
        week: list[DayWindows] = []
        for windows in value:
            if len(windows) > MAX_WINDOWS_PER_DAY:
                raise ValueError(_TOO_MANY)
            ordered = tuple(sorted(windows, key=lambda window: window.start_minute))
            if any(
                later.start_minute < earlier.end_minute
                for earlier, later in zip(ordered, ordered[1:], strict=False)
            ):
                raise ValueError(_OVERLAP)
            week.append(ordered)
        return tuple(week)

    @field_validator("buffer_minutes")
    @classmethod
    def _buffer(cls, value: int) -> int:
        if value not in BUFFER_CHOICES:
            raise ValueError(_BUFFER)
        return value

    @property
    def is_set(self) -> bool:
        """Whether any day has working hours; without any, there is no free time to show."""
        return any(self.days) or bool(self.overrides)

    def windows(self, weekday: int) -> DayWindows:
        """The windows of a weekday, 0 for Monday."""
        return self.days[weekday]

    @property
    def weekly_minutes(self) -> int:
        return sum(window.minutes for windows in self.days for window in windows)


class WorkingHours(HoursEdit):
    """The saved working hours; revision 0 means they were never saved."""

    revision: int = Field(ge=0)
    updated_at_utc: datetime | None = None

    @field_validator("updated_at_utc")
    @classmethod
    def _utc(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_utc(value)

    @classmethod
    def unset(cls) -> Self:
        """Never saved: no hours and no buffer."""
        return cls(revision=0)

    def edit(self) -> HoursEdit:
        return HoursEdit(**self.model_dump(include=set(HoursEdit.model_fields)))


def _nine_to_five() -> DayWindows:
    return (WorkingWindow(start_minute=9 * 60, end_minute=17 * 60),)


# Where the editor starts when no hours are set: Monday to Friday, 9:00 to 17:00. Nothing
# is saved until the owner saves it.
SUGGESTED_HOURS: Final = HoursEdit(days=(*(_nine_to_five(),) * 5, (), ()))
