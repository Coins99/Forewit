"""Repeating tasks (B05): a rule for the dates a task repeats on, with no time zone of its own.

A repeating task is a series: the rule, and a template its later occurrences copy. Each
occurrence is an ordinary task with its own date, so completing, skipping or editing one never
rewrites another's history. Dates are local days in the owner's zone; the rule never does
calendar arithmetic with times.
"""

import calendar
from datetime import date, timedelta
from enum import StrEnum
from typing import Final, Self

from pydantic import Field, field_validator, model_validator

from mailbrief.domain.common import DomainModel

WEEKDAYS: Final = range(7)  # date.weekday(): Monday is 0.
ALL_WEEKDAYS: Final = (0, 1, 2, 3, 4, 5, 6)
WORKING_WEEKDAYS: Final = (0, 1, 2, 3, 4)


class RepeatFrequency(StrEnum):
    """How a task repeats. A monthly day the month doesn't have (the 31st in April) is
    skipped that month; the last day of the month is its own choice, never a stand-in."""

    DAILY = "daily"
    WEEKLY = "weekly"  # On the chosen weekdays.
    MONTHLY_DAY = "monthly_day"  # On one day of the month.
    MONTHLY_LAST = "monthly_last"  # On the last day of each month.


def weekday_mask(weekdays: tuple[int, ...]) -> int:
    """The weekdays as stored: bit ``n`` is ``date.weekday() == n``."""
    return sum(1 << day for day in set(weekdays))


def mask_weekdays(mask: int) -> tuple[int, ...]:
    """The weekdays of a stored mask, Monday first."""
    return tuple(day for day in WEEKDAYS if mask & (1 << day))


def _last_day(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


def _months_after(year: int, month: int, count: int) -> tuple[int, int]:
    index = year * 12 + month - 1 + count
    return index // 12, index % 12 + 1


class RepeatRule(DomainModel):
    """The dates a task repeats on, and the last date it may repeat on (``end_date``)."""

    frequency: RepeatFrequency
    weekdays: tuple[int, ...] = ()  # Weekly only: one or more, Monday (0) first.
    month_day: int | None = Field(default=None, ge=1, le=31)  # MONTHLY_DAY only.
    end_date: date | None = None

    @field_validator("weekdays")
    @classmethod
    def sort_weekdays(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(day not in WEEKDAYS for day in value):
            raise ValueError("a weekday is 0 (Monday) to 6 (Sunday)")
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def check_fields(self) -> Self:
        if (self.frequency is RepeatFrequency.WEEKLY) != bool(self.weekdays):
            raise ValueError("weekdays are chosen exactly when a task repeats weekly")
        if (self.frequency is RepeatFrequency.MONTHLY_DAY) != (self.month_day is not None):
            raise ValueError("a day of the month is chosen exactly when it repeats on one")
        return self

    def _next(self, day: date) -> date | None:
        """The first date of the rule after ``day``, ignoring the end date."""
        if day == date.max:
            return None
        match self.frequency:
            case RepeatFrequency.DAILY:
                return day + timedelta(days=1)
            case RepeatFrequency.WEEKLY:
                for ahead in range(1, 8):
                    if date.max - day < timedelta(days=ahead):
                        return None
                    later = day + timedelta(days=ahead)
                    if later.weekday() in self.weekdays:
                        return later
                return None  # pragma: no cover - a weekly rule has a weekday.
            case RepeatFrequency.MONTHLY_DAY:
                assert self.month_day is not None
                # A day of the month comes round within two months whichever it is.
                for ahead in range(3):
                    year, month = _months_after(day.year, day.month, ahead)
                    if year > date.max.year:
                        return None
                    if self.month_day <= _last_day(year, month):
                        later = date(year, month, self.month_day)
                        if later > day:
                            return later
                return None  # pragma: no cover - always found within three months.
            case RepeatFrequency.MONTHLY_LAST:
                last = date(day.year, day.month, _last_day(day.year, day.month))
                if last > day:
                    return last
                year, month = _months_after(day.year, day.month, 1)
                if year > date.max.year:
                    return None
                return date(year, month, _last_day(year, month))

    def next_after(self, day: date) -> date | None:
        """The first date of the rule after ``day``; None past the end date."""
        later = self._next(day)
        if later is None or (self.end_date is not None and later > self.end_date):
            return None
        return later

    def first_on_or_after(self, day: date) -> date | None:
        """The first date of the rule on ``day`` or later; None past the end date."""
        if self.matches(day):
            return day if self.end_date is None or day <= self.end_date else None
        return self.next_after(day)

    def matches(self, day: date) -> bool:
        """Whether ``day`` is one of the rule's dates, ignoring the end date."""
        match self.frequency:
            case RepeatFrequency.DAILY:
                return True
            case RepeatFrequency.WEEKLY:
                return day.weekday() in self.weekdays
            case RepeatFrequency.MONTHLY_DAY:
                return day.day == self.month_day
            case RepeatFrequency.MONTHLY_LAST:
                return day.day == _last_day(day.year, day.month)


def next_occurrence(rule: RepeatRule, occurrence: date, today: date) -> date | None:
    """The date of the occurrence after one dated ``occurrence`` that was completed or
    skipped ``today``: the rule's first date after both. Dates missed in between are not
    created, so a task left for a week comes back once, not seven times."""
    return rule.next_after(max(occurrence, today))


class RepeatEdit(DomainModel):
    """The owner's repeat settings from the task editor.

    ``rule`` None means the task doesn't repeat: a repeating one stops. ``series_revision``
    is the revision the editor saw, None for a task that didn't repeat. ``later_too`` copies
    the edit (all but the target date) into the template later occurrences are made from.
    """

    rule: RepeatRule | None
    series_revision: int | None = Field(default=None, ge=1)
    later_too: bool = True
