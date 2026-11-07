"""Work blocks: time the owner sets aside on the calendar for one task (ADR 0028).

A block is planned time, never measured work. It belongs to one task but never changes it:
moving, resizing or unscheduling a block leaves the task, its revision, its history, its
deadline and its target date exactly as they were. A block keeps two UTC instants and the
IANA zone it was entered in, as a timed event does.
"""

import math
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from typing import Final, Self
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, field_validator, model_validator

from mailbrief.domain.common import ZONE_KEY_MAX_CHARS, DomainModel, known_zone, normalize_utc
from mailbrief.domain.intervals import Interval, clip, minutes, total_minutes

BLOCK_MIN_MINUTES: Final = 5
BLOCK_MAX_MINUTES: Final = 12 * 60
MAX_BLOCKS_PER_TASK: Final = 50  # Live blocks; unscheduled ones don't count.
BLOCK_STEP_MINUTES: Final = 15  # Dragging and nudging move by this much.
DEFAULT_BLOCK_MINUTES: Final = 60  # A new block for a task without an estimate.
SUGGESTED_MAX_MINUTES: Final = 120  # The longest a new block starts out.
MAX_OVERLAP_LABELS: Final = 3
_PUBLIC_ID_CHARS: Final = 36  # As actions' public IDs; importing them would be circular.

_TOO_SHORT: Final = "a block lasts at least 5 minutes"
_TOO_LONG: Final = "a block lasts at most 12 hours"


def check_block_time(starts_at_utc: datetime, ends_at_utc: datetime) -> None:
    """Raise ValueError, with a static message, unless the block lasts 5 minutes to 12 hours."""
    length = ends_at_utc - starts_at_utc
    if length < timedelta(minutes=BLOCK_MIN_MINUTES):
        raise ValueError(_TOO_SHORT)
    if length > timedelta(minutes=BLOCK_MAX_MINUTES):
        raise ValueError(_TOO_LONG)


class BlockTime(DomainModel):
    """When a block is: from one instant to a later one, entered in ``time_zone``."""

    starts_at_utc: datetime
    ends_at_utc: datetime
    time_zone: str = Field(min_length=1, max_length=ZONE_KEY_MAX_CHARS)

    @field_validator("starts_at_utc", "ends_at_utc")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return normalize_utc(value)

    @field_validator("time_zone")
    @classmethod
    def _zone(cls, value: str) -> str:
        return known_zone(value)

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        check_block_time(self.starts_at_utc, self.ends_at_utc)
        return self

    @property
    def interval(self) -> Interval:
        return (self.starts_at_utc, self.ends_at_utc)

    @property
    def minutes(self) -> int:
        return int((self.ends_at_utc - self.starts_at_utc).total_seconds() // 60)


class WorkBlock(DomainModel):
    """A live block, as stored."""

    public_id: str = Field(min_length=_PUBLIC_ID_CHARS, max_length=_PUBLIC_ID_CHARS)
    locked: bool = False
    action_public_id: str = Field(min_length=_PUBLIC_ID_CHARS, max_length=_PUBLIC_ID_CHARS)
    starts_at_utc: datetime
    ends_at_utc: datetime
    time_zone: str = Field(min_length=1, max_length=ZONE_KEY_MAX_CHARS)
    revision: int = Field(ge=1)
    created_at_utc: datetime
    updated_at_utc: datetime

    @field_validator("starts_at_utc", "ends_at_utc", "created_at_utc", "updated_at_utc")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        return normalize_utc(value)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        # A stored block always ends after it starts (the table's CHECK); its length limits
        # are checked when it is written, so a later change of limits keeps old rows readable.
        if self.ends_at_utc <= self.starts_at_utc:
            raise ValueError("a block must end after it starts")
        return self

    @property
    def time(self) -> BlockTime:
        return BlockTime(
            starts_at_utc=self.starts_at_utc,
            ends_at_utc=self.ends_at_utc,
            time_zone=self.time_zone,
        )

    @property
    def interval(self) -> Interval:
        return (self.starts_at_utc, self.ends_at_utc)

    @property
    def minutes(self) -> int:
        return int((self.ends_at_utc - self.starts_at_utc).total_seconds() // 60)

    def days(self, zone: ZoneInfo) -> tuple[date, date]:
        """The first and last day in ``zone`` the block touches; ending exactly at midnight
        doesn't touch the day that begins then."""
        first = self.starts_at_utc.astimezone(zone).date()
        last = (self.ends_at_utc - timedelta(microseconds=1)).astimezone(zone).date()
        return first, last

    def sort_key(self) -> tuple[datetime, datetime, str]:
        return (self.starts_at_utc, self.ends_at_utc, self.public_id)


class ScheduledTime(DomainModel):
    """A task's scheduled time: its live blocks, their lengths added up, and the time they
    cover together, where overlapping blocks count once."""

    blocks: int = Field(ge=0)
    total_minutes: int = Field(ge=0)
    unique_minutes: int = Field(ge=0)


def scheduled_time(
    blocks: Iterable[WorkBlock], start: datetime | None = None, end: datetime | None = None
) -> ScheduledTime:
    """The blocks' scheduled time, clipped to ``[start, end)`` when given."""
    spans = [block.interval for block in blocks]
    if start is not None and end is not None:
        spans = list(clip(spans, start, end))
    return ScheduledTime(
        blocks=len(spans),
        total_minutes=round(total_minutes(spans)),
        unique_minutes=round(minutes(spans)),
    )


def suggested_minutes(estimate_minutes: int | None, scheduled_minutes: int) -> int:
    """How long a new block for a task starts out: what is left of its estimate, rounded up
    to a step and at most SUGGESTED_MAX_MINUTES; DEFAULT_BLOCK_MINUTES without an estimate
    or with nothing left of it."""
    if estimate_minutes is None:
        return DEFAULT_BLOCK_MINUTES
    left = estimate_minutes - scheduled_minutes
    if left <= 0:
        return DEFAULT_BLOCK_MINUTES
    stepped = math.ceil(left / BLOCK_STEP_MINUTES) * BLOCK_STEP_MINUTES
    return min(SUGGESTED_MAX_MINUTES, stepped)


class BlockSaved(DomainModel):
    """What scheduling, moving or restoring a block left, with its local warnings.

    ``previous`` is the block's time before a move, for Undo. ``overlaps`` names up to
    MAX_OVERLAP_LABELS busy entries the block now overlaps, days off included, and
    ``more_overlaps`` counts the rest. ``after_deadline`` says the block ends after its
    task's deadline, ``outside_hours`` that some of it falls outside the working hours
    (ADR 0029), and ``buffered`` names up to MAX_OVERLAP_LABELS busy events whose buffer it
    sits in (ADR 0030). None of these stop a save.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    block: WorkBlock
    previous: BlockTime | None = None
    overlaps: tuple[str, ...] = Field(default=(), max_length=MAX_OVERLAP_LABELS, repr=False)
    more_overlaps: int = Field(default=0, ge=0)
    after_deadline: bool = False
    outside_hours: bool = False
    # Busy events whose buffer the block sits in, without overlapping them (ADR 0030).
    buffered: tuple[str, ...] = Field(default=(), max_length=MAX_OVERLAP_LABELS, repr=False)
