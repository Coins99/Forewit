"""AI calendar proposals (ADR 0018, phase 3): what one request sends, what comes back,
and the checked changes the owner may apply.

A CalendarPlanningRequest holds exactly what one request sends: the owner's instruction,
their own events in the planning window, and only the extra parts they chose. The model
only proposes. Python checks every change, and nothing changes until the owner applies.
"""

from datetime import date
from enum import StrEnum
from typing import Final, Self

from pydantic import ConfigDict, Field, model_validator

from mailbrief.domain.analysis import AIUsage
from mailbrief.domain.common import DomainModel
from mailbrief.domain.drafting import DraftingProblem, PreviewLine
from mailbrief.domain.events import EventChange

PLAN_INSTRUCTION_MAX_CHARS: Final = 1_000
PLANNING_DAYS: Final = 28  # Four weeks from the Monday shown.
MAX_PROPOSED_CHANGES: Final = 10
MAX_SENT_EVENTS: Final = 200
MAX_SENT_BUSY: Final = 200
MAX_SENT_ACTIONS: Final = 100
REASON_MAX_CHARS: Final = 200
MESSAGE_MAX_CHARS: Final = 300
MAX_OVERLAPS: Final = 3
_CANDIDATE_TEXT: Final = 10_000
_UNTRUSTED = ConfigDict(hide_input_in_errors=True)


class PlanningPart(StrEnum):
    """What the owner may add to a request; their own events are always sent."""

    GOOGLE_BUSY = "google_busy"  # When Google events are, without their titles.
    GOOGLE_TITLES = "google_titles"  # Their titles too; needs GOOGLE_BUSY.
    ACTIONS = "actions"  # Open actions' titles, deadlines and target dates in the window.


class PlanningOptions(DomainModel):
    """What the owner chose for one request."""

    model_config = _UNTRUSTED

    instruction: str = Field(min_length=1, max_length=PLAN_INSTRUCTION_MAX_CHARS, repr=False)
    parts: frozenset[PlanningPart] = frozenset()

    @model_validator(mode="after")
    def _titles_need_times(self) -> Self:
        if PlanningPart.GOOGLE_TITLES in self.parts and PlanningPart.GOOGLE_BUSY not in self.parts:
            raise ValueError("Google titles are sent only with Google busy times")
        return self


class PlannedEvent(DomainModel):
    """One of the owner's events as sent, under a key that exists for this request only."""

    model_config = _UNTRUSTED

    key: str = Field(min_length=2, max_length=8)
    title: str = Field(min_length=1, repr=False)
    all_day: bool
    start: str  # YYYY-MM-DD, or YYYY-MM-DD HH:MM, in the owner's zone.
    end: str  # The last day of an all-day event; the end time of a timed one.


class BusyTime(DomainModel):
    """A Google event's time, and its title only when the owner chose to send titles."""

    model_config = _UNTRUSTED

    all_day: bool
    start: str
    end: str
    title: str | None = Field(default=None, repr=False)


class PlannedAction(DomainModel):
    model_config = _UNTRUSTED

    title: str = Field(min_length=1, repr=False)
    deadline: str | None = None
    target: str | None = None


class CalendarPlanningRequest(DomainModel):
    """Exactly what one request sends; ``busy`` and ``actions`` are None when not chosen."""

    model_config = _UNTRUSTED

    instruction: str = Field(min_length=1, max_length=PLAN_INSTRUCTION_MAX_CHARS, repr=False)
    now_local: str
    time_zone: str
    window_first: date
    window_last: date
    events: tuple[PlannedEvent, ...]
    busy: tuple[BusyTime, ...] | None = None
    actions: tuple[PlannedAction, ...] | None = None


class ChangeCandidate(DomainModel):
    """One change as the adapter parsed it; nothing here has been checked."""

    model_config = ConfigDict(hide_input_in_errors=True, str_strip_whitespace=False)

    op: str = Field(max_length=_CANDIDATE_TEXT)
    event_key: str | None = Field(default=None, max_length=_CANDIDATE_TEXT)
    title: str | None = Field(default=None, max_length=_CANDIDATE_TEXT, repr=False)
    all_day: bool | None = None
    start_date: str | None = Field(default=None, max_length=_CANDIDATE_TEXT)
    start_time: str | None = Field(default=None, max_length=_CANDIDATE_TEXT)
    end_date: str | None = Field(default=None, max_length=_CANDIDATE_TEXT)
    end_time: str | None = Field(default=None, max_length=_CANDIDATE_TEXT)
    reason: str | None = Field(default=None, max_length=_CANDIDATE_TEXT, repr=False)


class PlanningCandidate(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True, str_strip_whitespace=False)

    changes: tuple[ChangeCandidate, ...] = Field(default=(), max_length=100)
    message: str | None = Field(default=None, max_length=_CANDIDATE_TEXT, repr=False)


class PlanningResponse(DomainModel):
    """A candidate, or the problem that kept one from being usable."""

    model_config = _UNTRUSTED

    candidate: PlanningCandidate | None = None
    problem: DraftingProblem | None = None
    usage: AIUsage | None = None

    @model_validator(mode="after")
    def _one_answer(self) -> Self:
        if (self.candidate is None) == (self.problem is None):
            raise ValueError("a planning response has a candidate or a problem, not both")
        return self


class ProposedChange(DomainModel):
    """A checked change, with the model's reason and the events it would overlap."""

    model_config = _UNTRUSTED

    change: EventChange
    reason: str | None = Field(default=None, max_length=REASON_MAX_CHARS, repr=False)
    overlaps: tuple[str, ...] = Field(default=(), max_length=MAX_OVERLAPS, repr=False)


class CalendarPlan(DomainModel):
    """What the owner reviews: checked changes, a note, and how many were dropped."""

    model_config = _UNTRUSTED

    proposals: tuple[ProposedChange, ...] = Field(max_length=MAX_PROPOSED_CHANGES)
    message: str | None = Field(default=None, max_length=MESSAGE_MAX_CHARS, repr=False)
    ignored: int = Field(default=0, ge=0)


class PlanningPreview(DomainModel):
    """What the owner approves before anything is sent."""

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    privacy_notice: str = Field(min_length=1)
    first_use: bool
    lines: tuple[PreviewLine, ...]


class PlanningStatus(StrEnum):
    PROPOSED = "proposed"
    DECLINED = "declined"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PlanningOutcome(DomainModel):
    model_config = _UNTRUSTED

    status: PlanningStatus
    plan: CalendarPlan | None = None
    error_code: str | None = None
    provider_detail: str | None = None
