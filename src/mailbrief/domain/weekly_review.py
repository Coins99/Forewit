"""The weekly review (A07, P5-1): a deterministic read of one Monday-to-Sunday week.

It is built from saved records only: no provider, no AI and no network. The one thing it
writes is the owner's small review record, which holds no owner text.
"""

from datetime import date, datetime, timedelta
from typing import Final

from pydantic import Field, field_validator

from mailbrief.domain.common import DomainModel, normalize_utc
from mailbrief.domain.routes import TaskRoute
from mailbrief.domain.upcoming import Upcoming
from mailbrief.domain.workload import DayWorkload

WEEK_DAYS: Final = 7


def week_start(day: date) -> date:
    """The Monday of ``day``'s week, as recurrence and the Calendar count weeks."""
    return day - timedelta(days=day.weekday())


def week_dates(start: date) -> tuple[date, ...]:
    return tuple(start + timedelta(days=offset) for offset in range(WEEK_DAYS))


def _monday(value: date) -> date:
    if value.weekday() != 0:
        raise ValueError("A review week starts on a Monday.")
    return value


class WeeklyReviewRecord(DomainModel):
    """That the owner reviewed a week, and the note holding their reflection, if any."""

    week_start: date
    revision: int = Field(ge=1)
    reviewed_at_utc: datetime | None = None
    reflection_note_public_id: str | None = Field(default=None, min_length=36, max_length=36)

    _week = field_validator("week_start")(_monday)

    @field_validator("reviewed_at_utc")
    @classmethod
    def _aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_utc(value)

    @property
    def reviewed(self) -> bool:
        return self.reviewed_at_utc is not None


class ReviewTask(DomainModel):
    """One task row of the review: its title, why it is listed, and where it opens."""

    public_id: str = Field(min_length=36, max_length=36)
    title: str = Field(repr=False)
    detail: str = Field(default="", repr=False)
    route: TaskRoute
    revision: int = Field(default=0, ge=0)


class WeeklyReview(DomainModel):
    """One week as saved records show it, as of ``captured_at_utc``.

    ``done`` are the distinct tasks with an effective completion in the week: the same
    tasks, and ``done_total`` the same figure, as Insights' "Distinct tasks completed" for
    those dates. ``partial_tasks`` counts tasks whose history is legacy or gapped.
    ``skipped`` are repeat occurrences skipped in the week, never counted as done.
    ``still_open`` are open tasks overdue now, or with a deadline or target date by the
    week's Sunday, or in one of its days' focus; ``waiting`` the tasks waiting on someone.
    ``next_week`` and ``next_days`` read the seven dates after the week.
    """

    week_start: date
    zone: str
    captured_at_utc: datetime
    done: tuple[ReviewTask, ...] = ()
    done_total: int = Field(default=0, ge=0)
    partial_tasks: int = Field(default=0, ge=0)
    skipped: tuple[ReviewTask, ...] = ()
    still_open: tuple[ReviewTask, ...] = ()
    waiting: tuple[ReviewTask, ...] = ()
    next_week: Upcoming
    next_days: tuple[DayWorkload, ...] = ()
    record: WeeklyReviewRecord | None = None

    _week = field_validator("week_start")(_monday)

    @property
    def week_end(self) -> date:
        return self.week_start + timedelta(days=WEEK_DAYS - 1)

    @property
    def next_start(self) -> date:
        return self.week_start + timedelta(days=WEEK_DAYS)
