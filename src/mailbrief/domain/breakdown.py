"""AI breakdown of one task (ADR 0033): what one request sends, what comes back, and the
checked steps the owner may apply.

A BreakdownRequest holds exactly what one generation sends: the task's title, today's date,
the owner's instruction and only the parts they ticked. It never holds the task's person,
tags, sources or an ID. The model only proposes step text. Python checks every step, and
nothing changes until the owner applies.
"""

from datetime import date
from enum import StrEnum
from typing import Final, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from mailbrief.domain.actions import (
    ACTION_STEP_MAX_CHARS,
    ACTION_TITLE_MAX_CHARS,
    ESTIMATE_MAX_MINUTES,
    MAX_ACTION_STEPS,
    PUBLIC_ID_CHARS,
    Action,
    ActionStatus,
)
from mailbrief.domain.analysis import AIUsage
from mailbrief.domain.common import DomainModel
from mailbrief.domain.drafting import DraftingProblem, PreviewLine

BREAKDOWN_SCHEMA_VERSION: Final = 1
BREAKDOWN_INSTRUCTION_MAX_CHARS: Final = 1_000
BREAKDOWN_NOTES_SENT_CHARS: Final = 2_000
BREAKDOWN_STEPS_SENT_CHARS: Final = 2_000  # All existing steps sent, together.
MAX_PROPOSED_STEPS: Final = 8
PROPOSED_STEP_MAX_CHARS: Final = 120
MAX_MISSING_CONTEXT: Final = 3
MISSING_CONTEXT_MAX_CHARS: Final = 200
# Candidates are unvalidated; these only keep a runaway answer from growing without bound.
_CANDIDATE_ITEMS: Final = 100
_CANDIDATE_TEXT_CHARS: Final = 10_000

NOT_OPEN: Final = "Reopen the task to break it down."
FULL: Final = f"This task already has {MAX_ACTION_STEPS} steps."
LOCAL_MAIL: Final = "This task came from mail analysed on this computer."

_UNTRUSTED = ConfigDict(hide_input_in_errors=True)
# The owner's text is sent as it is, spaces included.
_AS_GIVEN = ConfigDict(hide_input_in_errors=True, str_strip_whitespace=False)


class BreakdownPart(StrEnum):
    """What the owner may add to a request; the title, date and instruction always go."""

    NOTES = "notes"
    STEPS = "steps"  # The existing steps, in order, with their done marks.
    PROJECT = "project"  # The project's and its area's names.
    DATES = "dates"  # The deadline and the target date, as local values.
    ESTIMATE = "estimate"


PART_ORDER: Final = (
    BreakdownPart.NOTES,
    BreakdownPart.STEPS,
    BreakdownPart.PROJECT,
    BreakdownPart.DATES,
    BreakdownPart.ESTIMATE,
)


class BreakdownEnding(StrEnum):
    """How one generation stands in the history (``task_breakdowns.outcome``)."""

    PROPOSED = "proposed"
    APPLIED = "applied"
    DISMISSED = "dismissed"
    UNDONE = "undone"
    DECLINED = "declined"
    CANCELLED = "cancelled"
    FAILED = "failed"
    NO_STEPS = "no_steps"


class BreakdownStatus(StrEnum):
    PROPOSED = "proposed"
    NO_STEPS = "no_steps"
    DECLINED = "declined"
    FAILED = "failed"
    CANCELLED = "cancelled"


def available_parts(action: Action) -> frozenset[BreakdownPart]:
    """The optional parts ``action`` has something to send for."""
    has = {
        BreakdownPart.NOTES: bool(action.notes.strip()),
        BreakdownPart.STEPS: bool(action.steps),
        BreakdownPart.PROJECT: action.project is not None,
        BreakdownPart.DATES: action.target_date is not None
        or action.deadline_date is not None
        or action.deadline_at_utc is not None,
        BreakdownPart.ESTIMATE: action.estimate_minutes is not None,
    }
    return frozenset(part for part, present in has.items() if present)


def blocked_reason(action: Action, *, local_mail: bool) -> str | None:
    """Why ``action`` can't be broken down now, or None when it can.

    ``local_mail`` says email analysis runs on this computer: a task with an email source
    then stays here too, since its title may come from mail that never went to the cloud.
    """
    if action.status is not ActionStatus.OPEN:
        return NOT_OPEN
    if len(action.steps) >= MAX_ACTION_STEPS:
        return FULL
    if local_mail and action.sources:
        return LOCAL_MAIL
    return None


class BreakdownOptions(DomainModel):
    """What the owner chose for one generation."""

    model_config = _AS_GIVEN

    parts: frozenset[BreakdownPart] = frozenset()
    instruction: str = Field(default="", max_length=BREAKDOWN_INSTRUCTION_MAX_CHARS, repr=False)


class SentStep(DomainModel):
    """One existing step as sent: its words and whether it is done."""

    model_config = _AS_GIVEN

    text: str = Field(min_length=1, max_length=ACTION_STEP_MAX_CHARS, repr=False)
    done: bool


class BreakdownRequest(DomainModel):
    """Exactly what one generation sends. A part the owner didn't tick is None."""

    model_config = _AS_GIVEN

    request_key: str = Field(min_length=1, max_length=64)  # Random, for this request only.
    title: str = Field(min_length=1, max_length=ACTION_TITLE_MAX_CHARS, repr=False)
    today: date
    instruction: str = Field(default="", max_length=BREAKDOWN_INSTRUCTION_MAX_CHARS, repr=False)
    notes: str | None = Field(default=None, max_length=BREAKDOWN_NOTES_SENT_CHARS, repr=False)
    steps: tuple[SentStep, ...] | None = Field(
        default=None, max_length=MAX_ACTION_STEPS, repr=False
    )
    project: str | None = Field(default=None, repr=False)
    area: str | None = Field(default=None, repr=False)
    dates_sent: bool = False
    deadline_local: str | None = Field(default=None, max_length=16)
    target_date: date | None = None
    estimate_minutes: int | None = Field(default=None, ge=1, le=ESTIMATE_MAX_MINUTES)

    @field_validator("steps")
    @classmethod
    def _steps_fit(cls, value: tuple[SentStep, ...] | None) -> tuple[SentStep, ...] | None:
        if value is not None and sum(len(s.text) for s in value) > BREAKDOWN_STEPS_SENT_CHARS:
            raise ValueError("the steps sent are at most 2,000 characters in all")
        return value

    @model_validator(mode="after")
    def _parts_agree(self) -> Self:
        if self.area is not None and self.project is None:
            raise ValueError("an area is sent only with its project")
        if not self.dates_sent and (
            self.deadline_local is not None or self.target_date is not None
        ):
            raise ValueError("dates are sent only when the owner ticked them")
        return self

    @property
    def parts(self) -> frozenset[BreakdownPart]:
        chosen = {
            BreakdownPart.NOTES: self.notes is not None,
            BreakdownPart.STEPS: self.steps is not None,
            BreakdownPart.PROJECT: self.project is not None,
            BreakdownPart.DATES: self.dates_sent,
            BreakdownPart.ESTIMATE: self.estimate_minutes is not None,
        }
        return frozenset(part for part, present in chosen.items() if present)


def part_sizes(request: BreakdownRequest) -> dict[BreakdownPart | None, int]:
    """Characters of text each chosen part sends; None is the part always sent.

    The preview shows these counts, so they are computed from the request itself.
    """
    sizes: dict[BreakdownPart | None, int] = {
        None: len(request.title) + len(request.today.isoformat()) + len(request.instruction)
    }
    if request.notes is not None:
        sizes[BreakdownPart.NOTES] = len(request.notes)
    if request.steps is not None:
        sizes[BreakdownPart.STEPS] = sum(len(step.text) for step in request.steps)
    if request.project is not None:
        sizes[BreakdownPart.PROJECT] = len(request.project) + len(request.area or "")
    if request.dates_sent:
        sizes[BreakdownPart.DATES] = len(request.deadline_local or "") + len(
            request.target_date.isoformat() if request.target_date else ""
        )
    if request.estimate_minutes is not None:
        sizes[BreakdownPart.ESTIMATE] = len(str(request.estimate_minutes))
    return sizes


class BreakdownCandidate(DomainModel):
    """An answer as the adapter parsed it; the breakdown service checks it."""

    model_config = _UNTRUSTED

    steps: tuple[str, ...] = Field(default=(), max_length=_CANDIDATE_ITEMS, repr=False)
    missing_context: tuple[str, ...] = Field(default=(), max_length=_CANDIDATE_ITEMS, repr=False)

    @field_validator("steps", "missing_context")
    @classmethod
    def _bounded(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(len(item) > _CANDIDATE_TEXT_CHARS for item in value):
            raise ValueError("an answer's item is too long")
        return value


class BreakdownResponse(DomainModel):
    """A candidate, or the problem that kept one from being usable."""

    model_config = _UNTRUSTED

    candidate: BreakdownCandidate | None = None
    problem: DraftingProblem | None = None
    usage: AIUsage | None = None

    @model_validator(mode="after")
    def _one_answer(self) -> Self:
        if (self.candidate is None) == (self.problem is None):
            raise ValueError("a breakdown response has a candidate or a problem, not both")
        return self


class BreakdownPreview(DomainModel):
    """What the owner approves before anything is sent."""

    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    privacy_notice: str = Field(min_length=1)
    first_use: bool
    email_source: bool  # The task has an email source, so its title may come from mail.
    lines: tuple[PreviewLine, ...]

    @property
    def total_characters(self) -> int:
        return sum(line.characters for line in self.lines)


class CheckedSteps(DomainModel):
    """A checked answer: cleaned, bounded step text and missing-context notes."""

    model_config = _UNTRUSTED

    steps: tuple[str, ...] = Field(default=(), max_length=MAX_PROPOSED_STEPS, repr=False)
    missing_context: tuple[str, ...] = Field(default=(), max_length=MAX_MISSING_CONTEXT, repr=False)
    dropped: int = Field(default=0, ge=0)

    @field_validator("steps")
    @classmethod
    def _steps_bounded(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not 1 <= len(step) <= PROPOSED_STEP_MAX_CHARS for step in value):
            raise ValueError("a proposed step is 1 to 120 characters")
        return value

    @field_validator("missing_context")
    @classmethod
    def _missing_bounded(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not 1 <= len(item) <= MISSING_CONTEXT_MAX_CHARS for item in value):
            raise ValueError("a missing-context item is 1 to 200 characters")
        return value


class BreakdownProposal(DomainModel):
    """What the owner reviews: the checked steps of one generation, for one task as read.

    It lives in memory only. ``free_slots`` is how many steps the task could still take
    when it was read; ``steps`` never holds more.
    """

    model_config = _UNTRUSTED

    generation_id: str = Field(min_length=PUBLIC_ID_CHARS, max_length=PUBLIC_ID_CHARS)
    task_public_id: str = Field(min_length=PUBLIC_ID_CHARS, max_length=PUBLIC_ID_CHARS)
    read_revision: int = Field(ge=1)
    steps: tuple[str, ...] = Field(min_length=1, max_length=MAX_PROPOSED_STEPS, repr=False)
    missing_context: tuple[str, ...] = Field(default=(), max_length=MAX_MISSING_CONTEXT, repr=False)
    free_slots: int = Field(ge=1, le=MAX_ACTION_STEPS)


class KeptStep(DomainModel):
    """One proposed step the owner keeps, as they left its words."""

    model_config = _UNTRUSTED

    index: int = Field(ge=0, lt=MAX_PROPOSED_STEPS)  # Its place among the proposed steps.
    text: str = Field(min_length=1, max_length=ACTION_STEP_MAX_CHARS, repr=False)


class BreakdownOutcome(DomainModel):
    """What one generation did. Only a proposed one has steps to review."""

    model_config = _UNTRUSTED

    status: BreakdownStatus
    proposal: BreakdownProposal | None = None
    error_code: str | None = None
    provider_detail: str | None = None

    @model_validator(mode="after")
    def _status_agrees(self) -> Self:
        if (self.status is BreakdownStatus.PROPOSED) != (self.proposal is not None):
            raise ValueError("only a proposed outcome has a proposal")
        if (self.status is BreakdownStatus.FAILED) != (self.error_code is not None):
            raise ValueError("only a failed outcome has an error code")
        return self


class BreakdownApplied(DomainModel):
    """The task after an Apply, and what Undo needs to take exactly those steps back."""

    model_config = _UNTRUSTED

    action: Action
    generation_id: str = Field(min_length=PUBLIC_ID_CHARS, max_length=PUBLIC_ID_CHARS)
    added: int = Field(ge=0, le=MAX_PROPOSED_STEPS)
