"""Owner selection for a local date; task state is a live projection, never a snapshot."""

from datetime import date, datetime

from pydantic import Field, field_validator

from mailbrief.domain.actions import Action, ActionStatus
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.common import DomainModel, normalize_utc

MAX_DAY_ITEMS = 100


class DayPlanItem(DomainModel):
    public_id: str = Field(min_length=36, max_length=36)
    task_public_id: str = Field(min_length=36, max_length=36)
    position: int = Field(ge=0)
    action: Action | None = Field(default=None, repr=False)


class DayPlan(DomainModel):
    day: date
    public_id: str | None = Field(default=None, min_length=36, max_length=36)
    creation_zone: str | None = None
    revision: int = Field(default=0, ge=0)
    created_at_utc: datetime | None = None
    updated_at_utc: datetime | None = None
    items: tuple[DayPlanItem, ...] = Field(default=(), max_length=MAX_DAY_ITEMS)

    @field_validator("created_at_utc", "updated_at_utc")
    @classmethod
    def timestamps(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_utc(value)

    @property
    def execution_tasks(self) -> tuple[Action, ...]:
        return tuple(
            item.action
            for item in self.items
            if item.action is not None
            and item.action.status is ActionStatus.OPEN
            and item.action.ownership is ActionOwnership.MINE
        )

    @property
    def known_minutes(self) -> int:
        return sum(action.estimate_minutes or 0 for action in self.execution_tasks)

    @property
    def unknown_estimates(self) -> int:
        return sum(action.estimate_minutes is None for action in self.execution_tasks)

    @property
    def waiting_count(self) -> int:
        return sum(
            item.action is not None
            and item.action.status is ActionStatus.OPEN
            and item.action.ownership is ActionOwnership.WAITING_FOR
            for item in self.items
        )


class PlanTransfer(DomainModel):
    """An explicit, revision-bound preview; exact occurrence IDs never become successors."""

    source: DayPlan
    destination: DayPlan
    move: bool = False
    task_ids: tuple[str, ...]
    skipped_ids: tuple[str, ...] = ()
