"""Local proposals on the merged Calendar model; no provider or AI calls.

Snapshots are coherent reads. Apply and Undo acquire SQLite's writer reservation before
reading inputs. Receipts commit with their results and never retain owner text.
"""

import asyncio
import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import PRIORITY_RANK, ActionPriority
from mailbrief.domain.availability import busy_spans, working_spans
from mailbrief.domain.common import new_public_id, normalize_utc, utc_now
from mailbrief.domain.events import CalendarEvent
from mailbrief.domain.google_calendar import GoogleEvent
from mailbrief.domain.planning_intervals import (
    UtcInterval,
    clip_intervals,
    occupied_duration,
    subtract_intervals,
    union_intervals,
)
from mailbrief.domain.scheduling import (
    PlanningBlockState,
    PlanningCandidatePage,
    PlanningResult,
    PlanningReview,
    PlanningUndo,
    ScheduleConflict,
    SchedulingProposal,
    SchedulingRequest,
    SchedulingSnapshot,
    SchedulingTask,
    StoredPlanningBlock,
    TaskDemand,
    Workload,
    propose,
    verify_proposal,
    workload,
)
from mailbrief.domain.scheduling_time import planning_dates
from mailbrief.domain.work_blocks import MAX_BLOCKS_PER_TASK
from mailbrief.domain.workload import conflicts_for
from mailbrief.services.calendar import day_window
from mailbrief.services.events import EventService
from mailbrief.services.preferences import (
    PreferencesService,
    PreferencesUnavailableError,
    owner_zone,
)
from mailbrief.services.writes import commit_once
from mailbrief.storage.tables import (
    ActionTable,
    DayPlanItemTable,
    DayPlanTable,
    PlanningCommandTable,
    WorkBlockTable,
)
from mailbrief.storage.work_blocks import (
    WorkBlockRepository,
    block_from_row,
    scheduled_from_rows,
    task_due_at,
)


class SchedulingChangedError(ValueError):
    """A reviewed input or applied result changed."""


class SchedulingKnowledgeError(ValueError):
    def __init__(self, status: str, token: str) -> None:
        super().__init__(
            "Calendar knowledge is incomplete. Review and acknowledge it before applying."
        )
        self.status, self.token = status, token


def fingerprint(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _intervals(spans: tuple[tuple[datetime, datetime], ...]) -> tuple[UtcInterval, ...]:
    return tuple(UtcInterval(start=start, end=end) for start, end in spans if end > start)


class SchedulingService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = new_public_id,
        zone: ZoneInfo | None = None,
    ) -> None:
        self.session, self.clock, self.ids = session, clock, id_factory
        self._zone = zone

    async def _snapshot(self, request: SchedulingRequest, now: datetime) -> SchedulingSnapshot:
        dates = planning_dates(request.first_day, request.last_day)
        try:
            saved_zone = owner_zone(await PreferencesService(self.session).get())
        except PreferencesUnavailableError:
            raise SchedulingChangedError(
                "Owner preferences are unavailable. Repair them before planning."
            ) from None
        zone = self._zone or saved_zone
        first, last = dates[0], dates[-1]
        # Credit all future reservations for selected tasks, including outside the request.
        selected = {choice.task_public_id: choice for choice in request.tasks}
        pairs = list(
            (
                await self.session.execute(
                    select(WorkBlockTable, ActionTable)
                    .join(ActionTable, WorkBlockTable.action_id == ActionTable.id)
                    .where(
                        ActionTable.public_id.in_(selected),
                        ActionTable.deleted_at_utc.is_(None),
                        WorkBlockTable.deleted_at_utc.is_(None),
                        WorkBlockTable.ends_at_utc > now,
                    )
                    .execution_options(populate_existing=True)
                )
            ).tuples()
        )
        credit_first = min(
            (first, *(row.starts_at_utc.astimezone(zone).date() for row, _ in pairs))
        )
        credit_last = max(
            (
                last,
                *(
                    (row.ends_at_utc - timedelta(microseconds=1)).astimezone(zone).date()
                    for row, _ in pairs
                ),
            )
        )
        needed = set(dates)
        for row, _ in pairs:
            start_day = row.starts_at_utc.astimezone(zone).date()
            end_day = row.ends_at_utc.astimezone(zone).date()
            needed.update(
                start_day + timedelta(days=index) for index in range((end_day - start_day).days + 1)
            )
        # Include adjacent dates so a buffer across midnight remains an obstacle.
        needed |= {day + timedelta(days=step) for day in tuple(needed) for step in (-1, 1)}
        groups: list[tuple[date, date]] = []
        for day in sorted(needed):
            if (
                groups
                and day == groups[-1][1] + timedelta(days=1)
                and (day - groups[-1][0]).days < 42
            ):
                groups[-1] = (groups[-1][0], day)
            else:
                groups.append((day, day))
        ranges = [
            await EventService(self.session).list_range(start, end, zone) for start, end in groups
        ]
        hours = ranges[0].hours
        events = {("owner", event.public_id): event for shown in ranges for event in shown.events}
        google = {
            (event.calendar_id, event.event_id): event
            for shown in ranges
            for event in shown.google_events
        }
        fixed: list[CalendarEvent | GoogleEvent] = [*events.values(), *google.values()]
        blocks = {block.public_id: block for shown in ranges for block in shown.blocks}
        horizon = UtcInterval(
            start=day_window(first, zone).start_utc, end=day_window(last, zone).end_utc
        )
        busy = _intervals(
            busy_spans(fixed, zone, hours.before_minutes, after_minutes=hours.after_minutes)
        )
        working = (
            union_intervals(
                part for day in dates for part in _intervals(working_spans(hours, day, zone))
            )
            if hours.is_set
            else None
        )
        task_rows = list(
            (
                await self.session.scalars(
                    select(ActionTable)
                    .where(ActionTable.public_id.in_(selected))
                    .execution_options(populate_existing=True)
                )
            ).all()
        )
        tasks = {task.public_id: task for task in task_rows}
        positions: dict[str, int] = {}
        focus = None
        if request.focus_day is not None:
            focus = (
                await self.session.scalars(
                    select(DayPlanTable).where(DayPlanTable.local_date == request.focus_day)
                )
            ).one_or_none()
            if focus is not None:
                positions = {
                    item.task_public_id: item.position
                    for item in (
                        await self.session.scalars(
                            select(DayPlanItemTable).where(DayPlanItemTable.plan_id == focus.id)
                        )
                    ).all()
                }
        conflicts: list[ScheduleConflict] = []
        valid: dict[str, list[UtcInterval]] = {}
        repair: set[str] = set()
        for row, task in pairs:
            shown = scheduled_from_rows(row, task)
            warnings = conflicts_for(
                shown, zone, events=fixed, blocks=list(blocks.values()), hours=hours
            )
            if warnings:
                repair.add(task.public_id)
                conflicts.append(
                    ScheduleConflict(
                        block_public_id=row.public_id,
                        codes=tuple(item.kind.value for item in warnings),
                        messages=tuple(item.label or item.kind.value for item in warnings),
                    )
                )
            elif shown.open:
                # A task's future blocks count toward it with or without working hours, so
                # a scheduled task never reads as needing all of its time (C4-4).
                valid.setdefault(task.public_id, []).append(
                    UtcInterval(start=max(now, row.starts_at_utc), end=row.ends_at_utc)
                )
        demands: list[TaskDemand] = []
        for choice in request.tasks:
            chosen_task = tasks.get(choice.task_public_id)
            credit = union_intervals(valid.get(choice.task_public_id, ()))
            eligible = (
                chosen_task is not None
                and chosen_task.deleted_at_utc is None
                and chosen_task.status == "open"
                and chosen_task.ownership == "mine"
            )
            demands.append(
                TaskDemand(
                    task_public_id=choice.task_public_id,
                    task_revision=0 if chosen_task is None else chosen_task.revision,
                    title="Unavailable task" if chosen_task is None else chosen_task.title,
                    requested_minutes=choice.remaining_minutes
                    if choice.remaining_minutes is not None
                    else (None if chosen_task is None else chosen_task.estimate_minutes),
                    priority_rank=3
                    if chosen_task is None or chosen_task.priority is None
                    else PRIORITY_RANK[ActionPriority(chosen_task.priority)],
                    deadline_utc=None if chosen_task is None else task_due_at(chosen_task),
                    eligible=eligible,
                    focus_position=positions.get(choice.task_public_id, 1000000),
                    needs_repair=choice.task_public_id in repair,
                    credited_seconds=occupied_duration(credit).total_seconds(),
                    outside_horizon_seconds=occupied_duration(
                        subtract_intervals(credit, (horizon,))
                    ).total_seconds(),
                )
            )
        payload = {
            "request": request.model_dump(mode="json"),
            "zone": zone.key,
            "ranges": [item.model_dump(mode="json") for item in ranges],
            "tasks": [
                (
                    task.public_id,
                    task.revision,
                    task.status,
                    task.deleted_at_utc,
                    task.estimate_minutes,
                    task.deadline_precision,
                    task.deadline_date,
                    task.deadline_at_utc,
                )
                for task in task_rows
            ],
            "focus": None if focus is None else (focus.public_id, focus.revision, positions),
        }
        knowledge = ranges[0].google_knowledge.status(credit_first, credit_last, zone, now)
        in_range = tuple(
            block
            for block in blocks.values()
            if block.starts_at_utc < horizon.end and block.ends_at_utc > horizon.start
        )
        return SchedulingSnapshot(
            fingerprint=fingerprint(payload),
            captured_at_utc=now,
            owner_zone=zone.key,
            profile=hours,
            working=working,
            busy=clip_intervals(busy, horizon),
            timed_busy=clip_intervals(
                _intervals(
                    busy_spans(
                        [event for event in fixed if not event.all_day],
                        zone,
                        hours.before_minutes,
                        after_minutes=hours.after_minutes,
                    )
                ),
                horizon,
            ),
            all_day_commitments=sum(
                event.busy
                and event.all_day
                and event.days(zone)[0] <= last
                and event.days(zone)[1] >= first
                for event in fixed
            ),
            reserved=tuple(
                UtcInterval(
                    start=max(block.starts_at_utc, horizon.start),
                    end=min(block.ends_at_utc, horizon.end),
                )
                for block in in_range
                if block.open
            ),
            blocks=tuple(block.block for block in in_range),
            demands=tuple(demands),
            conflicts=tuple(conflicts),
            knowledge=knowledge,
            knowledge_first_day=credit_first,
            knowledge_last_day=credit_last,
        )

    async def snapshot(self, request: SchedulingRequest) -> SchedulingSnapshot:
        try:
            await self.session.execute(text("BEGIN"))
            return await self._snapshot(request, normalize_utc(self.clock()))
        finally:
            await self.session.rollback()

    async def candidates(
        self,
        first: date,
        last: date,
        *,
        query: str = "",
        offset: int = 0,
        needs_time: bool = True,
        project_id: str | None = None,
        focus_only: bool = False,
        focus_day: date | None = None,
    ) -> PlanningCandidatePage:
        planning_dates(first, last)
        if offset < 0:
            raise ValueError("The candidate offset must be nonnegative.")
        statement = select(ActionTable).where(
            ActionTable.deleted_at_utc.is_(None),
            ActionTable.status == "open",
            ActionTable.ownership == "mine",
        )
        for term in query.split():
            statement = statement.where(ActionTable.title.contains(term, autoescape=True))
        if project_id is not None:
            statement = statement.where(ActionTable.project_public_id == project_id)
        if focus_only:
            statement = statement.where(
                ActionTable.public_id.in_(
                    select(DayPlanItemTable.task_public_id)
                    .join(DayPlanTable)
                    .where(DayPlanTable.local_date == (focus_day or first))
                )
            )
        items: list[TaskDemand] = []
        try:
            await self.session.execute(text("BEGIN"))
            now = normalize_utc(self.clock())
            while True:
                rows = list(
                    (
                        await self.session.scalars(
                            statement.order_by(ActionTable.public_id).offset(offset).limit(50)
                        )
                    ).all()
                )
                if not rows:
                    return PlanningCandidatePage(items=tuple(items))
                snapshot = await self._snapshot(
                    SchedulingRequest(
                        first_day=first,
                        last_day=last,
                        focus_day=focus_day,
                        tasks=tuple(SchedulingTask(task_public_id=row.public_id) for row in rows),
                    ),
                    now,
                )
                for demand in snapshot.demands:
                    offset += 1
                    if (
                        not needs_time
                        or demand.needs_repair
                        or demand.uncovered_seconds is None
                        or demand.uncovered_seconds > 0
                    ):
                        items.append(demand)
                    if len(items) == 50:
                        return PlanningCandidatePage(items=tuple(items), next_offset=offset)
                if len(rows) < 50:
                    return PlanningCandidatePage(items=tuple(items))
        finally:
            await self.session.rollback()

    async def review(self, request: SchedulingRequest) -> PlanningReview:
        snapshot = await self.snapshot(request)
        proposal, shown = await asyncio.gather(
            asyncio.to_thread(propose, snapshot, request), asyncio.to_thread(workload, snapshot)
        )
        return PlanningReview(snapshot=snapshot, proposal=proposal, workload=shown)

    async def suggest(self, request: SchedulingRequest) -> SchedulingProposal:
        return (await self.review(request)).proposal

    async def workload(self, request: SchedulingRequest) -> Workload:
        return (await self.review(request)).workload

    async def _command(
        self, command_id: str
    ) -> tuple[PlanningCommandTable, tuple[StoredPlanningBlock, ...]] | None:
        try:
            receipt = await self.session.get(
                PlanningCommandTable, command_id, populate_existing=True
            )
            if receipt is None:
                return None
            states = _stored_states(receipt.results)
            return receipt, states
        except (ValidationError, json.JSONDecodeError, TypeError):
            raise SchedulingChangedError(
                "The saved planning receipt is unavailable. Nothing was recreated."
            ) from None

    async def _replay(
        self, command_id: str, operation: str, input_hash: str
    ) -> PlanningResult | None:
        saved = await self._command(command_id)
        if saved is None:
            return None
        receipt, states = saved
        if receipt.operation != operation or receipt.input_hash != input_hash:
            raise SchedulingChangedError("That planning command already has different input.")
        blocks = []
        for state in states:
            rows = await WorkBlockRepository(self.session).get(
                state.public_id, include_deleted=True
            )
            if (
                rows is None
                or rows[0].revision != state.revision
                or int(rows[0].deleted_at_utc is not None) != state.deleted
            ):
                raise SchedulingChangedError(
                    "That batch already applied; its results changed or were removed. "
                    "It will not be recreated."
                )
            blocks.append(block_from_row(rows[0], rows[1].public_id))
        undo = (
            None
            if operation != "apply"
            else PlanningUndo(
                command_id=command_id,
                blocks=tuple(
                    PlanningBlockState(public_id=block.public_id, revision=block.revision)
                    for block in blocks
                ),
            )
        )
        return PlanningResult(blocks=tuple(blocks), undo=undo, replayed=True)

    async def apply(
        self,
        proposal: SchedulingProposal,
        selected: tuple[str, ...],
        command_id: str,
        *,
        acknowledged_knowledge: str | None = None,
    ) -> PlanningResult:
        try:
            command_id = str(UUID(command_id))
        except ValueError:
            raise SchedulingChangedError("A planning command needs a valid command ID.") from None
        if not selected or len(set(selected)) != len(selected):
            raise SchedulingChangedError("Choose each proposed task once.")
        input_hash = fingerprint((proposal.model_dump(mode="json"), sorted(selected)))

        async def change(now: datetime) -> PlanningResult:
            await self.session.execute(text("BEGIN IMMEDIATE"))
            replay = await self._replay(command_id, "apply", input_hash)
            if replay is not None:
                return replay
            if now < proposal.generated_at_utc:
                raise SchedulingChangedError("The clock changed. Generate a new suggestion.")
            snapshot = await self._snapshot(proposal.request, proposal.generated_at_utc)
            if snapshot.fingerprint != proposal.fingerprint:
                raise SchedulingChangedError("Planning inputs changed. Generate a new suggestion.")
            # Placement runs off-thread during review. Under the writer lock we only
            # compare the coherent inputs and independently verify postconditions.
            try:
                verify_proposal(snapshot, proposal)
            except ValueError:
                raise SchedulingChangedError(
                    "The suggestion changed or violates constraints. Generate it again."
                ) from None
            edits = tuple(block for block in proposal.blocks if block.task_public_id in selected)
            if set(selected) != {block.task_public_id for block in edits}:
                raise SchedulingChangedError("Choose whole proposed tasks.")
            if any(edit.starts_at_utc < now for edit in edits):
                raise SchedulingChangedError(
                    "Suggested time has elapsed. Generate a new suggestion."
                )
            knowledge = (
                await EventService(self.session).list_range(
                    proposal.request.first_day,
                    proposal.request.last_day,
                    ZoneInfo(snapshot.owner_zone),
                )
            ).google_knowledge
            status = knowledge.status(
                snapshot.knowledge_first_day or proposal.request.first_day,
                snapshot.knowledge_last_day or proposal.request.last_day,
                ZoneInfo(snapshot.owner_zone),
                now,
            )
            token = fingerprint((snapshot.fingerprint, status))
            if status != "fresh shown-calendar coverage" and acknowledged_knowledge != token:
                raise SchedulingKnowledgeError(status, token)
            counts: dict[str, int] = {}
            for edit in edits:
                counts[edit.task_public_id] = counts.get(edit.task_public_id, 0) + 1
            for task_id, count in counts.items():
                action_id = (
                    await self.session.scalars(
                        select(ActionTable.id).where(ActionTable.public_id == task_id)
                    )
                ).one()
                if (
                    await WorkBlockRepository(self.session).live_count(action_id) + count
                    > MAX_BLOCKS_PER_TASK
                ):
                    raise SchedulingChangedError("A task can have at most 50 scheduled times.")
            rows = []
            for edit in edits:
                row = WorkBlockTable(
                    public_id=self.ids(),
                    action_id=(
                        await self.session.scalars(
                            select(ActionTable.id).where(
                                ActionTable.public_id == edit.task_public_id
                            )
                        )
                    ).one(),
                    starts_at_utc=edit.starts_at_utc,
                    ends_at_utc=edit.ends_at_utc,
                    time_zone=edit.time_zone,
                    locked=False,
                    revision=1,
                    created_at_utc=now,
                    updated_at_utc=now,
                )
                self.session.add(row)
                rows.append(row)
            await self.session.flush()
            results = []
            for row in rows:
                pair = await WorkBlockRepository(self.session).get(row.public_id)
                assert pair is not None
                results.append(block_from_row(pair[0], pair[1].public_id))
            self.session.add(
                PlanningCommandTable(
                    command_id=command_id,
                    operation="apply",
                    input_hash=input_hash,
                    results=[
                        {"public_id": row.public_id, "revision": row.revision, "deleted": 0}
                        for row in rows
                    ],
                    created_at_utc=now,
                )
            )
            return PlanningResult(
                blocks=tuple(results),
                undo=PlanningUndo(
                    command_id=command_id,
                    blocks=tuple(
                        PlanningBlockState(public_id=row.public_id, revision=row.revision)
                        for row in rows
                    ),
                ),
            )

        return await commit_once(self.session, self.clock, change)

    async def undo(self, token: PlanningUndo, command_id: str) -> PlanningResult:
        try:
            command_id = str(UUID(command_id))
        except ValueError:
            raise SchedulingChangedError("A planning command needs a valid command ID.") from None
        if len({state.public_id for state in token.blocks}) != len(token.blocks):
            raise SchedulingChangedError("Choose each result once.")
        input_hash = fingerprint(token.model_dump(mode="json"))

        async def change(now: datetime) -> PlanningResult:
            await self.session.execute(text("BEGIN IMMEDIATE"))
            replay = await self._replay(command_id, "undo", input_hash)
            if replay is not None:
                return replay
            applied = await self._command(token.command_id)
            if (
                applied is None
                or applied[0].operation != "apply"
                or sorted((state.public_id, state.revision) for state in applied[1])
                != sorted((state.public_id, state.revision) for state in token.blocks)
            ):
                raise SchedulingChangedError("This Undo does not match an applied planning batch.")
            rows = []
            for state in token.blocks:
                pair = await WorkBlockRepository(self.session).get(
                    state.public_id, include_deleted=True
                )
                if (
                    pair is None
                    or pair[0].revision != state.revision
                    or pair[0].deleted_at_utc is not None
                    or pair[0].locked
                ):
                    raise SchedulingChangedError("A batch result changed. Nothing was undone.")
                rows.append(pair)
            for row, _ in rows:
                row.deleted_at_utc = row.updated_at_utc = now
                row.revision += 1
            self.session.add(
                PlanningCommandTable(
                    command_id=command_id,
                    operation="undo",
                    input_hash=input_hash,
                    results=[
                        {"public_id": row.public_id, "revision": row.revision, "deleted": 1}
                        for row, _ in rows
                    ],
                    created_at_utc=now,
                )
            )
            return PlanningResult(
                blocks=tuple(block_from_row(pair[0], pair[1].public_id) for pair in rows)
            )

        return await commit_once(self.session, self.clock, change)


def _stored_states(value: object) -> tuple[StoredPlanningBlock, ...]:
    if not isinstance(value, list) or not 1 <= len(value) <= 200:
        raise SchedulingChangedError(
            "The saved planning receipt is unavailable. Nothing was recreated."
        )
    states = tuple(StoredPlanningBlock.model_validate(item) for item in value)
    if len({state.public_id for state in states}) != len(states):
        raise SchedulingChangedError(
            "The saved planning receipt is unavailable. Nothing was recreated."
        )
    return states
