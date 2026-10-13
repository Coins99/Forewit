"""The owner's day at a glance: what needs attention, today's agenda and new replies.

A read model only: it copies nothing and stores nothing. Every item is an existing action or
calendar entry, and opening one shows its own page.
"""

from datetime import date
from enum import StrEnum

from pydantic import ConfigDict

from mailbrief.domain.actions import Action
from mailbrief.domain.common import DomainModel
from mailbrief.domain.events import CalendarRange


class AttentionReason(StrEnum):
    """Why an open action needs attention today, most urgent first."""

    OVERDUE = "overdue"  # Its dated deadline has passed.
    DUE_TODAY = "due_today"  # Its deadline falls today.
    PLANNED_TODAY = "planned_today"  # Its target date is today.
    PLANNED_EARLIER = "planned_earlier"  # Its target date has passed and it is still open.


class AttentionItem(DomainModel):
    """An open action and the most urgent reason it needs attention today."""

    model_config = ConfigDict(hide_input_in_errors=True)

    action: Action
    reason: AttentionReason


class DaySummary(DomainModel):
    """``day`` in the owner's zone: the open actions that need attention, in order; the open
    actions with new replies in their threads, latest first; and the day's calendar (the
    owner's events, Google's and the actions' dates)."""

    model_config = ConfigDict(hide_input_in_errors=True)

    day: date
    attention: tuple[AttentionItem, ...] = ()
    replies: tuple[Action, ...] = ()
    calendar: CalendarRange
