"""Bounded deterministic add-only scheduling and independent constraint verification."""

from datetime import date, datetime, timedelta
from typing import Self
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, field_validator, model_validator

from mailbrief.domain.common import DomainModel, normalize_utc
from mailbrief.domain.planning_intervals import UtcInterval, occupied_duration, subtract_intervals
from mailbrief.domain.scheduling_time import all_day_intervals, planning_dates
from mailbrief.domain.work_blocks import BlockTime, WorkBlock
from mailbrief.domain.working_hours import WorkingHours


class ProposedBlock(BlockTime):
    task_public_id: str = Field(min_length=36, max_length=36)
    locked: bool = False


MAX_TASKS = 100
MAX_PROPOSED_BLOCKS = 200
REVIEW_LEAD = timedelta(minutes=5)


class SchedulingTask(DomainModel):
    task_public_id: str = Field(min_length=36, max_length=36)
    remaining_minutes: int | None = Field(default=None, ge=1, le=10080, strict=True)
    split: bool = False
    minimum_piece_minutes: int = Field(default=15, ge=1, le=1440, strict=True)
    allow_small_remainder: bool = False


class SchedulingRequest(DomainModel):
    first_day: date
    last_day: date
    tasks: tuple[SchedulingTask, ...] = Field(default=(), max_length=MAX_TASKS)
    focus_day: date | None = None

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        planning_dates(self.first_day, self.last_day)
        if len({task.task_public_id for task in self.tasks}) != len(self.tasks):
            raise ValueError("choose each task once")
        return self


class TaskDemand(DomainModel):
    task_public_id: str
    task_revision: int
    title: str = Field(repr=False)
    requested_minutes: int | None
    priority_rank: int = 3
    deadline_utc: datetime | None = None
    focus_position: int = 1000000
    eligible: bool = True
    needs_repair: bool = False
    credited_seconds: float = 0
    outside_horizon_seconds: float = 0

    @property
    def uncovered_seconds(self) -> float | None:
        return (
            None
            if self.requested_minutes is None
            else max(
                0, self.requested_minutes * 60_000_000 - round(self.credited_seconds * 1_000_000)
            )
            / 1_000_000
        )


class ScheduleConflict(DomainModel):
    block_public_id: str
    codes: tuple[str, ...]
    messages: tuple[str, ...] = Field(repr=False)


class SchedulingSnapshot(DomainModel):
    fingerprint: str
    captured_at_utc: datetime
    owner_zone: str
    profile: WorkingHours
    working: tuple[UtcInterval, ...] | None
    busy: tuple[UtcInterval, ...]
    timed_busy: tuple[UtcInterval, ...] = ()
    all_day_commitments: int = 0
    reserved: tuple[UtcInterval, ...]
    blocks: tuple[WorkBlock, ...]
    demands: tuple[TaskDemand, ...]
    conflicts: tuple[ScheduleConflict, ...]
    knowledge: str
    knowledge_first_day: date | None = None
    knowledge_last_day: date | None = None


class UnplacedTask(DomainModel):
    task_public_id: str
    reason: str


class SchedulingProposal(DomainModel):
    request: SchedulingRequest
    fingerprint: str
    generated_at_utc: datetime
    knowledge: str
    blocks: tuple[ProposedBlock, ...]
    unplaced: tuple[UnplacedTask, ...]

    @field_validator("generated_at_utc")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return normalize_utc(value)


class PlanningBlockState(DomainModel):
    public_id: str = Field(min_length=36, max_length=36)
    revision: int = Field(ge=1)


class StoredPlanningBlock(PlanningBlockState):
    model_config = ConfigDict(strict=True, hide_input_in_errors=True)
    deleted: int = Field(ge=0, le=1, strict=True)


class PlanningUndo(DomainModel):
    command_id: str = Field(min_length=36, max_length=36)
    blocks: tuple[PlanningBlockState, ...] = Field(min_length=1, max_length=MAX_PROPOSED_BLOCKS)


class PlanningResult(DomainModel):
    blocks: tuple[WorkBlock, ...]
    undo: PlanningUndo | None = None
    replayed: bool = False


class Workload(DomainModel):
    working_minutes: float | None
    busy_minutes: float
    timed_busy_minutes: float = 0
    all_day_commitments: int = 0
    scheduled_minutes: float
    unique_scheduled_minutes: float
    future_reserved_minutes: float
    free_minutes: float | None
    uncovered_minutes: float
    unknown_tasks: int
    repair_tasks: int
    outside_horizon_credit_minutes: float
    knowledge: str
    conflicts: tuple[ScheduleConflict, ...]


class PlanningCandidatePage(DomainModel):
    items: tuple[TaskDemand, ...]
    next_offset: int | None = None


class PlanningReview(DomainModel):
    snapshot: SchedulingSnapshot
    proposal: SchedulingProposal
    workload: Workload


def free_slots(snapshot: SchedulingSnapshot) -> tuple[UtcInterval, ...] | None:
    if snapshot.working is None:
        return None
    future = tuple(
        UtcInterval(start=max(window.start, snapshot.captured_at_utc), end=window.end)
        for window in snapshot.working
        if window.end > snapshot.captured_at_utc
    )
    return subtract_intervals(future, (*snapshot.busy, *snapshot.reserved))


def workload(snapshot: SchedulingSnapshot) -> Workload:
    slots = free_slots(snapshot)
    eligible = tuple(demand for demand in snapshot.demands if demand.eligible)

    def minutes(intervals: tuple[UtcInterval, ...]) -> float:
        return occupied_duration(intervals).total_seconds() / 60

    return Workload(
        working_minutes=None if snapshot.working is None else minutes(snapshot.working),
        busy_minutes=minutes(snapshot.busy),
        timed_busy_minutes=minutes(snapshot.timed_busy),
        all_day_commitments=snapshot.all_day_commitments,
        scheduled_minutes=sum(
            interval.duration.total_seconds() / 60 for interval in snapshot.reserved
        ),
        unique_scheduled_minutes=minutes(snapshot.reserved),
        future_reserved_minutes=minutes(
            tuple(
                UtcInterval(start=max(interval.start, snapshot.captured_at_utc), end=interval.end)
                for interval in snapshot.reserved
                if interval.end > snapshot.captured_at_utc
            )
        ),
        free_minutes=None if slots is None else minutes(slots),
        uncovered_minutes=sum((demand.uncovered_seconds or 0) / 60 for demand in eligible),
        unknown_tasks=sum(demand.requested_minutes is None for demand in eligible),
        repair_tasks=sum(demand.needs_repair for demand in eligible),
        outside_horizon_credit_minutes=sum(
            demand.outside_horizon_seconds / 60 for demand in eligible
        ),
        knowledge=snapshot.knowledge,
        conflicts=snapshot.conflicts,
    )


def propose(snapshot: SchedulingSnapshot, request: SchedulingRequest) -> SchedulingProposal:
    """Greedy, stable and bounded. Failure is not proof that placement is impossible."""
    free = free_slots(snapshot)
    earliest = snapshot.captured_at_utc + REVIEW_LEAD
    if free is not None:
        free = tuple(
            UtcInterval(start=max(slot.start, earliest), end=slot.end)
            for slot in free
            if slot.end > earliest
        )
    selected = {task.task_public_id: task for task in request.tasks}
    placed: list[ProposedBlock] = []
    unplaced: list[UnplacedTask] = []
    limit = False
    ordered = sorted(
        snapshot.demands,
        key=lambda demand: (
            demand.priority_rank,
            demand.deadline_utc or datetime.max.replace(tzinfo=snapshot.captured_at_utc.tzinfo),
            demand.focus_position,
            demand.task_public_id,
        ),
    )
    for demand in ordered:
        task = selected[demand.task_public_id]
        remaining = demand.uncovered_seconds
        reason = (
            "Proposal limit reached"
            if limit
            else "Task is no longer eligible"
            if not demand.eligible
            else "Needs schedule repair"
            if demand.needs_repair
            else "Working hours not set"
            if free is None
            else "Duration is unknown"
            if remaining is None
            else ""
        )
        if reason:
            unplaced.append(UnplacedTask(task_public_id=demand.task_public_id, reason=reason))
            continue
        assert remaining is not None and free is not None
        if remaining == 0:
            continue
        slots = [
            UtcInterval(start=slot.start, end=min(slot.end, demand.deadline_utc))
            if demand.deadline_utc is not None and demand.deadline_utc < slot.end
            else slot
            for slot in free
            if demand.deadline_utc is None or slot.start < demand.deadline_utc
        ]
        pieces: list[UtcInterval] = []
        left = remaining
        for slot in slots:
            available = min(slot.duration.total_seconds(), 43200)
            if not task.split:
                if 300 <= left <= available:
                    pieces = [
                        UtcInterval(start=slot.start, end=slot.start + timedelta(seconds=left))
                    ]
                    left = 0
                    break
                continue
            cursor = slot.start
            while left > 0 and cursor < slot.end and len(pieces) < 4:
                available = min((slot.end - cursor).total_seconds(), 43200)
                minimum = task.minimum_piece_minutes * 60
                take = min(available, left)
                if 0 < left - take < minimum and not task.allow_small_remainder:
                    take -= minimum - (left - take)
                if take < 300 or (
                    take < minimum and not (task.allow_small_remainder and take == left)
                ):
                    break
                take = round(take, 6)  # Datetimes have microsecond precision; retain it exactly.
                pieces.append(UtcInterval(start=cursor, end=cursor + timedelta(seconds=take)))
                cursor += timedelta(seconds=take)
                left = round(left - take, 6)
            if left == 0 or len(pieces) == 4:
                break
        if left:
            capacity = sum(slot.duration.total_seconds() for slot in slots)
            reason = (
                "less than five schedulable minutes remains"
                if remaining < 300
                else "a contiguous block is limited to 12 elapsed hours; allow splitting"
                if not task.split and remaining > 43200
                else "insufficient available time before the deadline"
                if capacity < remaining and demand.deadline_utc is not None
                else "insufficient known free time"
                if capacity < remaining
                else "fragmented free time; a contiguous block is required"
                if not task.split
                else "the split policy prevents full placement"
            )
            unplaced.append(
                UnplacedTask(
                    task_public_id=demand.task_public_id,
                    reason="Not placed by this suggestion: " + reason,
                )
            )
            continue
        if len(placed) + len(pieces) > MAX_PROPOSED_BLOCKS:
            limit = True
            unplaced.append(
                UnplacedTask(task_public_id=demand.task_public_id, reason="Proposal limit reached")
            )
            continue
        placed.extend(
            ProposedBlock(
                task_public_id=demand.task_public_id,
                starts_at_utc=piece.start,
                ends_at_utc=piece.end,
                time_zone=snapshot.owner_zone,
            )
            for piece in pieces
        )
        free = subtract_intervals(free, pieces)
    result = SchedulingProposal(
        request=request,
        fingerprint=snapshot.fingerprint,
        generated_at_utc=snapshot.captured_at_utc,
        knowledge=snapshot.knowledge,
        blocks=tuple(placed),
        unplaced=tuple(unplaced),
    )
    verify_proposal(snapshot, result)
    return result


def verify_proposal(snapshot: SchedulingSnapshot, proposal: SchedulingProposal) -> None:
    """Independent postcondition checks; never trust placement arithmetic or caller payloads."""
    if snapshot.working is None and proposal.blocks:
        raise ValueError("Working hours must be set before suggesting blocks.")
    if len(proposal.blocks) > MAX_PROPOSED_BLOCKS:
        raise ValueError("Proposal limit reached.")
    demands = {demand.task_public_id: demand for demand in snapshot.demands}
    choices = {choice.task_public_id: choice for choice in proposal.request.tasks}
    horizon = all_day_intervals(
        proposal.request.first_day, proposal.request.last_day, ZoneInfo(snapshot.owner_zone)
    )
    intervals: list[UtcInterval] = []
    by_task: dict[str, list[UtcInterval]] = {}
    for block in proposal.blocks:
        demand = demands.get(block.task_public_id)
        if (
            demand is None
            or block.task_public_id not in choices
            or not demand.eligible
            or demand.needs_repair
            or block.locked
        ):
            raise ValueError("A proposed task is not eligible.")
        interval = UtcInterval(start=block.starts_at_utc, end=block.ends_at_utc)
        if (
            interval.start < snapshot.captured_at_utc + REVIEW_LEAD
            or subtract_intervals((interval,), horizon)
            or block.time_zone != snapshot.owner_zone
            or (demand.deadline_utc is not None and interval.end > demand.deadline_utc)
            or subtract_intervals((interval,), snapshot.working or ())
            or any(
                interval.overlaps(busy) for busy in (*snapshot.busy, *snapshot.reserved, *intervals)
            )
        ):
            raise ValueError("A proposed block violates scheduling constraints.")
        intervals.append(interval)
        by_task.setdefault(block.task_public_id, []).append(interval)
    for task_id, pieces in by_task.items():
        pieces.sort(key=lambda piece: piece.start)
        task = choices[task_id]
        if len(pieces) > (4 if task.split else 1):
            raise ValueError("The proposal violates the split policy.")
        if sum(
            (piece.duration.days * 43200 + piece.duration.seconds) * 1_000_000
            + piece.duration.microseconds
            for piece in pieces
        ) != round((demands[task_id].uncovered_seconds or 0) * 1_000_000):
            raise ValueError("The proposal does not conserve the requested duration.")
        if task.split and any(
            piece.duration.total_seconds() < task.minimum_piece_minutes * 60
            for piece in pieces[:-1]
            if pieces
        ):
            raise ValueError("A split piece is too short.")
        if (
            task.split
            and not task.allow_small_remainder
            and pieces[-1].duration.total_seconds() < task.minimum_piece_minutes * 60
        ):
            raise ValueError("A small final remainder was not allowed.")
    unplaced_ids = {item.task_public_id for item in proposal.unplaced}
    if (
        len(unplaced_ids) != len(proposal.unplaced)
        or not unplaced_ids <= choices.keys()
        or unplaced_ids & by_task.keys()
    ):
        raise ValueError("Task outcomes are duplicated or unknown.")
    for task_id, demand in demands.items():
        needs_result = (
            not demand.eligible
            or demand.needs_repair
            or snapshot.working is None
            or demand.uncovered_seconds is None
            or demand.uncovered_seconds > 0
        )
        if needs_result and task_id not in by_task and task_id not in unplaced_ids:
            raise ValueError("A task outcome is missing from the proposal.")
