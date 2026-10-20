"""User-owned actions accepted from suggestions, with their steps and source messages."""

import re
import unicodedata
from collections.abc import Iterable
from datetime import date, datetime, time
from enum import StrEnum
from typing import Final, Self
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, HttpUrl, field_validator, model_validator

from mailbrief.domain.analysis import (
    FOLLOW_UP_EVIDENCE_CHARS,
    SUGGESTION_EVIDENCE_CHARS,
    ActionEffort,
    ActionOwnership,
    ActionSuggestion,
    DeadlineFields,
    DeadlinePrecision,
    FollowUpKind,
    TargetReason,
    deadline_due_at,
)
from mailbrief.domain.common import DomainModel, normalize_utc
from mailbrief.domain.projects import Project
from mailbrief.domain.recurrence import RepeatRule

ACTION_TITLE_MAX_CHARS: Final = 200
ACTION_STEP_MAX_CHARS: Final = 500
MAX_ACTION_STEPS: Final = 30
ACTION_NOTES_MAX_CHARS: Final = 10_000
# The owner's own estimate, in whole minutes up to 100 hours; never derived from the AI's effort.
ESTIMATE_MAX_MINUTES: Final = 6_000
PUBLIC_ID_CHARS: Final = 36  # A UUID in its canonical text form.
TAG_MAX_CHARS: Final = 40
MAX_ACTION_TAGS: Final = 10
_TAG_SPACES: Final = re.compile(r"\s+")

TARGET_REASON_TEXT: Final[dict[TargetReason, str]] = {
    TargetReason.WORKING_DAY_BEFORE: "one working day before the deadline",
    TargetReason.ON_DEADLINE: "on the deadline; no earlier working day was left",
}


class ActionStatus(StrEnum):
    """Whether an accepted action is still open. Only an occurrence of a repeating task is
    skipped: closed without being done, so its series moves on (B05)."""

    OPEN = "open"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class ActionPriority(StrEnum):
    """The owner's own priority for a task; AI runs and proposals never set it."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


# Among tasks with the same dates: high first, and tasks without a priority last.
PRIORITY_RANK: Final[dict[ActionPriority | None, int]] = {
    ActionPriority.HIGH: 0,
    ActionPriority.MEDIUM: 1,
    ActionPriority.LOW: 2,
    None: 3,
}


def tag_key(name: str) -> str:
    """What makes two tags the same tag: case and character width don't ("Home" is "home").
    Unicode's NFKC case folding, so a key is its own key."""
    return unicodedata.normalize("NFKC", unicodedata.normalize("NFKC", name).casefold())


def clean_tag(text: str) -> str:
    """A tag as stored: single spaces, no control or format characters, no leading ``#``."""
    kept = "".join(
        char for char in text if char.isspace() or not unicodedata.category(char).startswith("C")
    )
    return _TAG_SPACES.sub(" ", kept).strip().lstrip("#").strip()


def normalize_tags(values: Iterable[str]) -> tuple[str, ...]:
    """The owner's tags cleaned, without blanks or repeats (the first spelling is kept),
    sorted by their key. Raises ValueError, naming no tag, for a tag that is too long or has a
    comma (tags are typed separated by commas), or for more than MAX_ACTION_TAGS."""
    found: dict[str, str] = {}
    for value in values:
        tag = clean_tag(value)
        if not tag:
            continue
        if "," in tag:
            raise ValueError("A tag can't contain a comma.")
        if len(tag) > TAG_MAX_CHARS:
            raise ValueError(f"A tag can be at most {TAG_MAX_CHARS} characters.")
        found.setdefault(tag_key(tag), tag)
    if len(found) > MAX_ACTION_TAGS:
        raise ValueError(f"A task can have at most {MAX_ACTION_TAGS} tags.")
    return tuple(found[key] for key in sorted(found))


class ActionActivityKind(StrEnum):
    CREATED = "created"
    EDITED = "edited"
    COMPLETED = "completed"
    REOPENED = "reopened"
    UNDO = "undo"
    DELETED = "deleted"
    RESTORED = "restored"
    SOURCE_ADDED = "source_added"
    SUGGESTION_ACCEPTED = "suggestion_accepted"
    THREAD_SEEN = "thread_seen"
    PROPOSAL_APPLIED = "proposal_applied"
    SKIPPED = "skipped"
    # Another task respelt a tag this one shares; the owner didn't change this task itself.
    TAG_RESPELLED = "tag_respelled"


class ActionActivity(DomainModel):
    """An immutable record of an owner change, including the prior completion state."""

    revision: int = Field(ge=1)
    kind: ActionActivityKind
    occurred_at_utc: datetime
    previous_status: ActionStatus | None = None
    previous_completed_at_utc: datetime | None = None
    undo_of_revision: int | None = Field(default=None, ge=1)
    previous_source_count: int | None = Field(default=None, ge=0)
    project_public_id: str | None = None
    area_public_id: str | None = None
    legacy: bool = False  # Migrated facts, not a reconstruction of missing past changes.

    @field_validator("occurred_at_utc", "previous_completed_at_utc")
    @classmethod
    def normalize_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_utc(value)


class SuggestionState(StrEnum):
    """What the owner decided about a suggestion."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    DISMISSED = "dismissed"


class ActionFilter(StrEnum):
    """Views of the action list."""

    OPEN = "open"
    WAITING = "waiting"
    COMPLETED = "completed"


class ProposalState(StrEnum):
    """What the owner decided about a follow-up proposal (ADR 0016)."""

    PENDING = "pending"
    APPLIED = "applied"
    DISMISSED = "dismissed"


class DeadlineOrigin(StrEnum):
    """Who set an action's deadline (B06, ADR 0021): an email, through the suggestion the
    owner accepted or a follow-up proposal they applied, or the owner."""

    EMAIL = "email"
    OWNER = "owner"


class StatedDeadline(DeadlineFields):
    """An email's deadline the owner replaced or cleared, kept as the email stated it so
    they can see it and go back to it (B06)."""

    @model_validator(mode="after")
    def check_stated(self) -> Self:
        if self.deadline_precision is DeadlinePrecision.NONE:
            raise ValueError("a stated deadline is never empty")
        return self


class DeadlineChangeKind(StrEnum):
    """What the owner does to an action's deadline (B06)."""

    SET = "set"  # A day, and optionally a time, in the owner's zone.
    CLEAR = "clear"
    STATED = "stated"  # Go back to the email's deadline.


class DeadlineChange(DomainModel):
    """The owner's change to an action's deadline (B06); leaving it out keeps the deadline.

    ``due_date`` and ``due_time`` are wall-clock values in the owner's zone, and only a SET
    carries them: a date, and a time of day when the deadline has one.
    """

    kind: DeadlineChangeKind
    due_date: date | None = None
    due_time: time | None = None

    @field_validator("due_time")
    @classmethod
    def whole_minutes(cls, value: time | None) -> time | None:
        if value is not None and value.tzinfo is not None:
            raise ValueError("a deadline's time is a wall-clock time, without a zone")
        return None if value is None else value.replace(second=0, microsecond=0)

    @model_validator(mode="after")
    def check_kind(self) -> Self:
        if (self.due_date is not None) != (self.kind is DeadlineChangeKind.SET):
            raise ValueError("only a new deadline has a date, and it must")
        if self.due_time is not None and self.due_date is None:
            raise ValueError("a deadline's time needs its date")
        return self

    @classmethod
    def to(cls, due_date: date, due_time: time | None = None) -> Self:
        """Set the deadline to ``due_date``, at ``due_time`` when given."""
        return cls(kind=DeadlineChangeKind.SET, due_date=due_date, due_time=due_time)

    @classmethod
    def cleared(cls) -> Self:
        return cls(kind=DeadlineChangeKind.CLEAR)

    @classmethod
    def from_email(cls) -> Self:
        """Go back to the deadline the email stated."""
        return cls(kind=DeadlineChangeKind.STATED)


class ActionRepeat(DomainModel):
    """How a task repeats (B05): its series, the rule, and this occurrence's own date.

    ``occurrence_date`` is the occurrence's identity in its series; moving the task's target
    date reschedules it without changing which occurrence it is. ``ended`` says the owner
    stopped the repeats. ``previous_date`` and ``previous_status`` describe the live
    occurrence this one followed, if any, and ``next_date`` is the date of the live one its
    completion or skip made, if any.
    """

    series_public_id: str = Field(min_length=PUBLIC_ID_CHARS, max_length=PUBLIC_ID_CHARS)
    series_revision: int = Field(ge=1)
    rule: RepeatRule
    occurrence_date: date
    ended: bool = False
    previous_date: date | None = None
    previous_status: ActionStatus | None = None
    next_date: date | None = None

    @model_validator(mode="after")
    def check_previous(self) -> Self:
        if (self.previous_date is None) != (self.previous_status is None):
            raise ValueError("the previous occurrence has both a date and a status, or neither")
        return self


class ActionStep(DomainModel):
    """One ordered step of an action's plan."""

    model_config = ConfigDict(hide_input_in_errors=True)

    step_id: int = Field(ge=1)
    position: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=ACTION_STEP_MAX_CHARS, repr=False)
    done: bool
    done_at_utc: datetime | None = None

    @field_validator("done_at_utc")
    @classmethod
    def normalize_done_at(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_utc(value)

    @model_validator(mode="after")
    def validate_done_at(self) -> Self:
        if self.done_at_utc is not None and not self.done:
            raise ValueError("only a done step can have done_at_utc")
        return self


class ActionSource(DomainModel):
    """A message an action came from; ``available`` says whether it is still cached."""

    model_config = ConfigDict(hide_input_in_errors=True)

    provider_message_id: str = Field(min_length=1, max_length=512)
    subject: str = Field(default="", max_length=998, repr=False)
    sender_address: str = Field(min_length=3, max_length=320)
    web_link: HttpUrl
    received_at_utc: datetime
    available: bool
    in_inbox: bool | None = None  # None when Inbox membership is unknown.

    @field_validator("received_at_utc")
    @classmethod
    def normalize_received_at(cls, value: datetime) -> datetime:
        return normalize_utc(value)


class ThreadActivity(DomainModel):
    """Later messages in an action's threads, derived from cached metadata (ADR 0015).

    ``new_messages``, ``latest_at_utc`` and ``latest_sender`` (a display name, else the
    address) cover other people's messages after the owner's "seen" watermark; the owner's
    own latest reply is reported separately. None of it ever changes the action.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    new_messages: int = Field(default=0, ge=0)
    latest_at_utc: datetime | None = None
    latest_sender: str | None = Field(default=None, min_length=1, max_length=320, repr=False)
    owner_replied_at_utc: datetime | None = None

    @field_validator("latest_at_utc", "owner_replied_at_utc")
    @classmethod
    def normalize_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_utc(value)

    @model_validator(mode="after")
    def validate_latest(self) -> Self:
        new = self.new_messages > 0
        if (self.latest_at_utc is not None) != new or (self.latest_sender is not None) != new:
            raise ValueError("the latest message is set exactly when there are new messages")
        return self

    @property
    def unseen(self) -> bool:
        """Whether anything arrived after the watermark, from others or the owner."""
        return self.new_messages > 0 or self.owner_replied_at_utc is not None


class ActionProposal(DeadlineFields):
    """A later email's proposed update to an action, which only the owner applies (ADR 0016).

    A new deadline carries the deadline and suggested target; a cancellation or delivery
    proposes completing the action and carries none. The email is a snapshot, so the
    proposal outlives the cached message. ``action_title`` is the action's current title,
    for display, and ``action_revision`` the action's revision when the proposal was loaded:
    applying it needs that revision, so a change in between is caught.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    id: int = Field(ge=1)
    action_public_id: str = Field(min_length=PUBLIC_ID_CHARS, max_length=PUBLIC_ID_CHARS)
    action_title: str = Field(min_length=1, max_length=ACTION_TITLE_MAX_CHARS, repr=False)
    action_revision: int = Field(ge=1)
    kind: FollowUpKind
    state: ProposalState
    suggested_target_date: date | None = None
    target_reason: TargetReason | None = None
    evidence: str = Field(min_length=1, max_length=FOLLOW_UP_EVIDENCE_CHARS, repr=False)
    provider_message_id: str = Field(min_length=1, max_length=512)
    subject: str = Field(default="", max_length=998, repr=False)
    sender_address: str = Field(min_length=3, max_length=320)
    received_at_utc: datetime
    web_link: HttpUrl
    created_at_utc: datetime
    # A new deadline that would replace one the owner set (B06), as the action was loaded.
    replaces_owner_deadline: bool = False

    @field_validator("received_at_utc", "created_at_utc")
    @classmethod
    def normalize_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_utc(value)

    @model_validator(mode="after")
    def validate_kind(self) -> Self:
        if self.kind is FollowUpKind.NONE:
            raise ValueError("a proposal needs a follow-up kind")
        dated = self.deadline_precision is not DeadlinePrecision.NONE
        if dated != (self.kind is FollowUpKind.NEW_DEADLINE):
            raise ValueError("only a new-deadline proposal carries a deadline, and it must")
        if (self.suggested_target_date is None) != (self.target_reason is None):
            raise ValueError("suggested_target_date and target_reason must be set together")
        if self.replaces_owner_deadline and not dated:
            raise ValueError("only a new deadline replaces the owner's")
        return self


class Action(DeadlineFields):
    """An action the owner accepted and now owns, with its plan and source messages.

    Its deadline is the current one, whoever set it: ``deadline_origin`` says who, and is
    None exactly when there is none. ``stated_deadline`` is the email's deadline the owner
    last replaced or cleared, if any (B06).
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    public_id: str = Field(min_length=PUBLIC_ID_CHARS, max_length=PUBLIC_ID_CHARS)
    project: Project | None = None
    title: str = Field(min_length=1, max_length=ACTION_TITLE_MAX_CHARS, repr=False)
    ownership: ActionOwnership
    status: ActionStatus
    priority: ActionPriority | None = None  # The owner's, never the AI's.
    tags: tuple[str, ...] = Field(default=(), repr=False)  # Sorted by tag_key.
    effort: ActionEffort | None = None
    estimate_minutes: int | None = Field(default=None, ge=1, le=ESTIMATE_MAX_MINUTES)
    deadline_origin: DeadlineOrigin | None = None
    stated_deadline: StatedDeadline | None = Field(default=None, repr=False)
    suggested_target_date: date | None = None
    target_reason: TargetReason | None = None
    target_date: date | None = None  # The owner's choice.
    notes: str = Field(default="", max_length=ACTION_NOTES_MAX_CHARS, repr=False)
    evidence: str | None = Field(
        default=None, min_length=1, max_length=SUGGESTION_EVIDENCE_CHARS, repr=False
    )
    created_at_utc: datetime
    updated_at_utc: datetime
    completed_at_utc: datetime | None = None
    revision: int = Field(ge=1)
    steps: tuple[ActionStep, ...] = Field(default=(), max_length=MAX_ACTION_STEPS)
    sources: tuple[ActionSource, ...] = ()
    thread_seen_until_utc: datetime | None = None  # The owner's "seen" watermark.
    repeat: ActionRepeat | None = None  # None for a task that never repeated.
    thread: ThreadActivity | None = None  # None when no source has a thread snapshot.
    proposals: tuple[ActionProposal, ...] = ()  # Pending follow-up proposals only.
    # Recent display history; the complete audit trail stays in storage and exports.
    activity: tuple[ActionActivity, ...] = ()

    @field_validator(
        "created_at_utc",
        "updated_at_utc",
        "completed_at_utc",
        "thread_seen_until_utc",
    )
    @classmethod
    def normalize_timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_utc(value)

    @model_validator(mode="after")
    def validate_completion(self) -> Self:
        if (self.completed_at_utc is not None) != (self.status is ActionStatus.COMPLETED):
            raise ValueError("completed_at_utc must be set exactly when the action is completed")
        if (self.deadline_origin is None) != (self.deadline_precision is DeadlinePrecision.NONE):
            raise ValueError("deadline_origin must be set exactly when there is a deadline")
        return self

    @property
    def owner_deadline(self) -> bool:
        """Whether the owner set the current deadline."""
        return self.deadline_origin is DeadlineOrigin.OWNER

    def due_at_utc(self) -> datetime | None:
        """When the deadline falls due; see deadline_due_at."""
        return deadline_due_at(
            self.deadline_precision,
            self.deadline_date,
            self.deadline_at_utc,
            self.deadline_timezone,
        )

    def is_overdue(self, now_utc: datetime) -> bool:
        """Whether a dated deadline has passed; an action without one is never overdue."""
        due = self.due_at_utc()
        return due is not None and now_utc >= due

    def carried_over(self, today: date, zone: ZoneInfo) -> bool:
        """Whether the action was created on an earlier local day than ``today``."""
        return self.created_at_utc.astimezone(zone).date() < today


class ThreadLink(DomainModel):
    """A live, open action with a source in an email's thread, in the same account.

    The brief shows it as a continuation and offers to add the email to it; ``is_source``
    says the email is already one of the action's sources.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    public_id: str = Field(min_length=PUBLIC_ID_CHARS, max_length=PUBLIC_ID_CHARS)
    title: str = Field(min_length=1, max_length=ACTION_TITLE_MAX_CHARS, repr=False)
    revision: int = Field(ge=1)
    ownership: ActionOwnership
    is_source: bool


class ActionEdit(DomainModel):
    """The owner's full set of edits to an action's own fields."""

    model_config = ConfigDict(hide_input_in_errors=True)

    project_public_id: str | None = Field(default=None, min_length=36, max_length=36)
    title: str = Field(min_length=1, max_length=ACTION_TITLE_MAX_CHARS, repr=False)
    ownership: ActionOwnership
    # Required like the rest: an edit that left one out would erase the owner's choice.
    priority: ActionPriority | None
    tags: tuple[str, ...] = Field(repr=False)
    effort: ActionEffort | None
    estimate_minutes: int | None = Field(ge=1, le=ESTIMATE_MAX_MINUTES)
    target_date: date | None
    notes: str = Field(max_length=ACTION_NOTES_MAX_CHARS, repr=False)

    @field_validator("tags")
    @classmethod
    def check_tags(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return normalize_tags(value)


class StepEdit(DomainModel):
    """One step in the owner's edited plan; a step without ``step_id`` is new."""

    model_config = ConfigDict(hide_input_in_errors=True)

    step_id: int | None = Field(ge=1)
    text: str = Field(min_length=1, max_length=ACTION_STEP_MAX_CHARS, repr=False)
    done: bool


class SuggestionView(DomainModel):
    """A stored suggestion with the owner's decision and, once accepted, its action."""

    model_config = ConfigDict(hide_input_in_errors=True)

    suggestion_id: int = Field(ge=1)
    suggestion: ActionSuggestion
    state: SuggestionState
    action_public_id: str | None = Field(
        default=None, min_length=PUBLIC_ID_CHARS, max_length=PUBLIC_ID_CHARS
    )

    @model_validator(mode="after")
    def validate_action_link(self) -> Self:
        if self.action_public_id is not None and self.state is not SuggestionState.ACCEPTED:
            raise ValueError("only an accepted suggestion can link to an action")
        return self
