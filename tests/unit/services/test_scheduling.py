"""Local deterministic scheduling, coherent snapshots, atomic apply and durable replay."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.events import EventEdit
from mailbrief.domain.scheduling import (
    PlanningBlockState,
    PlanningResult,
    PlanningUndo,
    SchedulingRequest,
    SchedulingTask,
    verify_proposal,
)
from mailbrief.domain.work_blocks import BlockTime
from mailbrief.domain.working_hours import HoursEdit, WorkingWindow
from mailbrief.services.actions import ActionService
from mailbrief.services.availability import AvailabilityService
from mailbrief.services.events import EventService
from mailbrief.services.scheduling import (
    SchedulingChangedError,
    SchedulingKnowledgeError,
    SchedulingService,
)
from mailbrief.storage.database import Database
from mailbrief.storage.tables import PlanningCommandTable, WorkBlockTable
from tests.unit.services.test_manual_tasks import EDIT

DAY = date(2026, 11, 9)
START = datetime(2026, 11, 9, 9, tzinfo=UTC)
ZONE = ZoneInfo("UTC")


def command_id() -> str:
    return str(uuid4())


def edit(task: str) -> BlockTime:
    return BlockTime(starts_at_utc=START, ends_at_utc=START + timedelta(hours=1), time_zone="UTC")


async def seeded(session: AsyncSession) -> tuple[None, None, str]:
    task = await ActionService(session).create(EDIT.model_copy(update={"estimate_minutes": 90}))
    return None, None, task.public_id


NOW = START.replace(hour=8)


async def setup(session: AsyncSession) -> tuple[SchedulingService, str]:
    _, _, task = await seeded(session)
    await AvailabilityService(session).save_hours(
        HoursEdit(
            days=tuple((WorkingWindow(start_minute=540, end_minute=1020),) for _ in range(7))
        ),
        0,
    )
    return SchedulingService(session, clock=lambda: NOW, zone=ZONE), task


def request(task: str, **fields: object) -> SchedulingRequest:
    return SchedulingRequest(
        first_day=DAY,
        last_day=DAY,
        tasks=(SchedulingTask.model_validate({"task_public_id": task} | fields),),
    )


async def test_apply_review_retry_undo_and_task_semantics(session: AsyncSession) -> None:
    service, task = await setup(session)
    proposal = await service.suggest(request(task))
    assert len(proposal.blocks) == 1 and not proposal.unplaced
    assert proposal.blocks[0].starts_at_utc == NOW + timedelta(hours=1)
    key = command_id()
    with pytest.raises(SchedulingKnowledgeError) as caught:
        await service.apply(proposal, (task,), key)
    saved = await service.apply(proposal, (task,), key, acknowledged_knowledge=caught.value.token)
    assert saved.undo is not None and len(saved.blocks) == 1
    assert (await service.apply(proposal, (task,), key)).replayed
    undo_key = command_id()
    await service.undo(saved.undo, undo_key)
    assert (await session.scalars(select(WorkBlockTable))).one().deleted_at_utc is not None
    await session.rollback()
    assert (await service.undo(saved.undo, undo_key)).replayed
    with pytest.raises(SchedulingChangedError, match="already applied"):
        await service.apply(proposal, (task,), key)


async def test_new_obstacle_invalidates_absence_snapshot(session: AsyncSession) -> None:
    service, task = await setup(session)
    proposal = await service.suggest(request(task))
    await EventService(session).create(
        EventEdit(
            title="New meeting",
            all_day=False,
            starts_at_utc=NOW + timedelta(hours=1),
            ends_at_utc=NOW + timedelta(hours=2),
            time_zone="UTC",
        )
    )
    with pytest.raises(SchedulingChangedError, match="inputs changed"):
        await service.apply(proposal, (task,), command_id())
    assert not (await session.scalars(select(WorkBlockTable))).all()


async def test_workload_preserves_overlapping_bookings_and_clips_each_to_horizon(
    session: AsyncSession,
) -> None:
    from mailbrief.services.work_blocks import WorkBlockService

    service, task = await setup(session)
    blocks = WorkBlockService(session)
    for start, end in (
        (START, START + timedelta(hours=1)),
        (START + timedelta(minutes=30), START + timedelta(minutes=90)),
        (START.replace(hour=23, minute=30), START + timedelta(days=1)),
    ):
        await blocks.schedule(
            task, BlockTime(starts_at_utc=start, ends_at_utc=end, time_zone="UTC")
        )
    review = await service.review(request(task))
    assert review.workload.scheduled_minutes == 150
    assert review.workload.unique_scheduled_minutes == 120
    assert review.workload.free_minutes == 390
    assert review.snapshot.demands[0].needs_repair


async def test_split_is_opt_in_and_never_leaves_partial_task(session: AsyncSession) -> None:
    service, task = await setup(session)
    for hour in (10, 12, 14, 16):
        await EventService(session).create(
            EventEdit(
                title="Fixed meeting",
                all_day=False,
                starts_at_utc=NOW.replace(hour=hour),
                ends_at_utc=NOW.replace(hour=hour + 1),
                time_zone="UTC",
            )
        )
    contiguous = await service.suggest(request(task, remaining_minutes=90))
    assert not contiguous.blocks and contiguous.unplaced
    split = await service.suggest(request(task, remaining_minutes=90, split=True))
    assert len(split.blocks) == 2 and not split.unplaced
    assert (
        sum((block.ends_at_utc - block.starts_at_utc).total_seconds() for block in split.blocks)
        == 5400
    )
    too_long = await service.suggest(request(task, remaining_minutes=300, split=True))
    assert not too_long.blocks and too_long.unplaced


async def test_unknown_hours_duration_and_workload_are_honest(session: AsyncSession) -> None:
    _, _, task = await seeded(session)
    service = SchedulingService(session, clock=lambda: NOW, zone=ZONE)
    proposal = await service.suggest(request(task))
    assert proposal.unplaced[0].reason == "Working hours not set"
    shown = await service.workload(request(task))
    assert shown.working_minutes is None and shown.free_minutes is None


async def test_valid_outside_horizon_credit_and_invalid_booking_repair(
    session: AsyncSession,
) -> None:
    service, task = await setup(session)
    from mailbrief.services.work_blocks import WorkBlockService

    blocks = WorkBlockService(session)
    await blocks.schedule(task, edit(task))
    snapshot = await service.snapshot(request(task))
    assert snapshot.demands[0].credited_seconds == 3600
    assert (await service.workload(request(task))).uncovered_minutes == 30
    await EventService(session).create(
        EventEdit(
            title="New conflict",
            all_day=False,
            starts_at_utc=START,
            ends_at_utc=START + timedelta(hours=1),
            time_zone="UTC",
        )
    )
    proposal = await service.suggest(request(task))
    assert not proposal.blocks and proposal.unplaced[0].reason == "Needs schedule repair"


async def test_a_done_task_s_block_takes_no_time_and_is_no_conflict(
    session: AsyncSession,
) -> None:
    """A suggestion may use a done task's time, as free time counts it. The conflict rule
    once counted the overlap, so the task needed repair as soon as it was applied."""
    from mailbrief.services.work_blocks import WorkBlockService

    service, task = await setup(session)
    actions = ActionService(session)
    done = await actions.create(EDIT.model_copy(update={"title": "Finished"}))
    await WorkBlockService(session).schedule(done.public_id, edit(done.public_id))
    await actions.complete(done.public_id, done.revision)
    review = await service.review(request(task))
    (block,) = review.proposal.blocks
    assert block.starts_at_utc == START  # Where the done task's block is.
    assert review.workload.scheduled_minutes == 0
    assert review.workload.free_minutes == 480
    with pytest.raises(SchedulingKnowledgeError) as caught:
        await service.apply(review.proposal, (task,), command_id())
    await service.apply(
        review.proposal, (task,), command_id(), acknowledged_knowledge=caught.value.token
    )
    after = await service.snapshot(request(task))
    assert not after.conflicts and not after.demands[0].needs_repair
    saved = await WorkBlockService(session).schedule(task, edit(task))
    assert saved.overlaps == ("Task · Plan the week",)  # Its own block, never the done one.


async def test_failed_batch_commit_rolls_back_every_block_and_receipt(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    service, task = await setup(session)
    proposal = await service.suggest(request(task))
    with pytest.raises(SchedulingKnowledgeError) as caught:
        await service.apply(proposal, (task,), command_id())
    with monkeypatch.context() as patch:
        patch.setattr(session, "commit", AsyncMock(side_effect=RuntimeError("Synthetic failure")))
        with pytest.raises(RuntimeError):
            await service.apply(
                proposal, (task,), command_id(), acknowledged_knowledge=caught.value.token
            )
    assert not (await session.scalars(select(WorkBlockTable))).all()
    assert not (await session.scalars(select(PlanningCommandTable))).all()


async def test_independent_sessions_only_one_apply_wins(database: Database) -> None:
    async with database.session() as session:
        service, task = await setup(session)
        proposal = await service.suggest(request(task))
        with pytest.raises(SchedulingKnowledgeError) as caught:
            await service.apply(proposal, (task,), command_id())
        acknowledgement = caught.value.token

    async def apply() -> object:
        async with database.session() as session:
            try:
                return await SchedulingService(session, clock=lambda: NOW, zone=ZONE).apply(
                    proposal, (task,), str(uuid4()), acknowledged_knowledge=acknowledgement
                )
            except SchedulingChangedError as error:
                return error

    results = await asyncio.gather(apply(), apply())
    assert sum(isinstance(result, SchedulingChangedError) for result in results) == 1


async def test_tampering_elapsed_and_changed_undo_are_refused(session: AsyncSession) -> None:
    service, task = await setup(session)
    proposal = await service.suggest(request(task))
    snapshot = await service.snapshot(request(task))
    forged = proposal.model_copy(
        update={"blocks": (proposal.blocks[0].model_copy(update={"locked": True}),)}
    )
    with pytest.raises(ValueError):
        verify_proposal(snapshot, forged)
    with pytest.raises(SchedulingChangedError, match="suggestion changed"):
        await service.apply(forged, (task,), command_id())
    late = SchedulingService(session, clock=lambda: NOW + timedelta(hours=2), zone=ZONE)
    with pytest.raises(SchedulingChangedError, match="elapsed"):
        await late.apply(proposal, (task,), command_id())


async def acknowledged(service: SchedulingService, task: str) -> PlanningResult:
    proposal = await service.suggest(request(task))
    try:
        return await service.apply(proposal, (task,), command_id())
    except SchedulingKnowledgeError as error:
        return await service.apply(
            proposal, (task,), command_id(), acknowledged_knowledge=error.token
        )


async def test_candidate_paging_filters_unknown_and_focus(session: AsyncSession) -> None:
    from mailbrief.services.actions import ActionService
    from mailbrief.services.day_plans import DayPlanService
    from tests.unit.services.test_manual_tasks import EDIT

    service, task = await setup(session)
    actions = ActionService(session)
    unknown = await actions.create(
        EDIT.model_copy(update={"title": "Unknown duration", "estimate_minutes": None})
    )
    await DayPlanService(session).select(DAY, ZONE, unknown.public_id, 0)
    page = await service.candidates(DAY, DAY, query="Unknown", focus_only=True, focus_day=DAY)
    assert len(page.items) == 1 and page.items[0].requested_minutes is None
    assert not (await service.candidates(DAY, DAY, query="x")).items
    assert not (await service.candidates(DAY, DAY, project_id=str(uuid4()))).items
    with pytest.raises(ValueError):
        await service.candidates(DAY, DAY, offset=-1)
    await acknowledged(service, task)
    assert task not in {item.task_public_id for item in (await service.candidates(DAY, DAY)).items}
    assert task in {
        item.task_public_id for item in (await service.candidates(DAY, DAY, needs_time=False)).items
    }
    for index in range(53):
        await actions.create(EDIT.model_copy(update={"title": f"Paged task {index}"}))
    page = await service.candidates(DAY, DAY, query="Paged", needs_time=False)
    assert len(page.items) == 50 and page.next_offset is not None
    page = await service.candidates(
        DAY, DAY, query="Paged", needs_time=False, offset=page.next_offset
    )
    assert len(page.items) == 3 and page.next_offset is None


async def test_inactive_all_day_and_missing_task(session: AsyncSession) -> None:
    from mailbrief.services.actions import ActionService

    service, task = await setup(session)
    result = await acknowledged(service, task)
    assert result.undo is not None
    await ActionService(session).complete(task, 1)
    inactive = await service.workload(request(task))
    assert inactive.scheduled_minutes == 0
    assert inactive.uncovered_minutes == 0 and inactive.unknown_tasks == 0
    assert (await service.suggest(request(str(uuid4())))).unplaced[
        0
    ].reason == "Task is no longer eligible"
    await EventService(session).create(
        EventEdit(
            title="All-day commitment",
            all_day=True,
            busy=True,
            start_date=DAY,
            last_date=DAY,
            time_zone="UTC",
        )
    )
    shown = await service.workload(request(task))
    assert shown.free_minutes == 0
    assert shown.all_day_commitments == 1 and shown.timed_busy_minutes == 0
    await service.undo(result.undo, command_id())


async def test_all_day_summary_counts_only_commitments_in_requested_dates(
    session: AsyncSession,
) -> None:
    from mailbrief.services.work_blocks import WorkBlockService

    service, task = await setup(session)
    await WorkBlockService(session).schedule(
        task,
        BlockTime(
            starts_at_utc=START + timedelta(days=7),
            ends_at_utc=START + timedelta(days=7, hours=1),
            time_zone="UTC",
        ),
    )
    for offset in (-1, 0, 1, 7):
        await EventService(session).create(
            EventEdit(
                title="All-day commitment",
                all_day=True,
                busy=True,
                start_date=DAY + timedelta(days=offset),
                last_date=DAY + timedelta(days=offset),
                time_zone="UTC",
            )
        )
    assert (await service.workload(request(task))).all_day_commitments == 1


async def test_outside_horizon_credit_tracks_its_obstacles(session: AsyncSession) -> None:
    from mailbrief.services.work_blocks import WorkBlockService

    service, task = await setup(session)
    future_edit = edit(task).model_copy(
        update={
            "starts_at_utc": START + timedelta(days=7),
            "ends_at_utc": START + timedelta(days=7, hours=1),
        }
    )
    await WorkBlockService(session).schedule(task, future_edit)
    assert (await service.workload(request(task))).outside_horizon_credit_minutes == 60
    proposal = await service.suggest(request(task))
    await EventService(session).create(
        EventEdit(
            title="Outside-horizon conflict",
            all_day=False,
            starts_at_utc=future_edit.starts_at_utc,
            ends_at_utc=future_edit.ends_at_utc,
            time_zone="UTC",
        )
    )
    with pytest.raises(SchedulingChangedError):
        await service.apply(proposal, (task,), command_id())
    assert (await service.suggest(request(task))).unplaced[0].reason == "Needs schedule repair"


async def test_coverage_age_generation_and_clock_rechecked(session: AsyncSession) -> None:
    from mailbrief.storage.google_calendar import GoogleCalendarRepository

    service, task = await setup(session)
    repository = GoogleCalendarRepository(session)
    await repository.replace_calendars(
        (), NOW, DAY - timedelta(days=2), DAY + timedelta(days=2), ZONE.key
    )
    await session.commit()
    proposal = await service.suggest(request(task))
    assert proposal.knowledge == "fresh shown-calendar coverage"
    delayed = SchedulingService(session, clock=lambda: NOW + timedelta(minutes=16), zone=ZONE)
    with pytest.raises(SchedulingKnowledgeError) as caught:
        await delayed.apply(proposal, (task,), command_id())
    assert caught.value.status == "stale"
    result = await delayed.apply(
        proposal, (task,), command_id(), acknowledged_knowledge=caught.value.token
    )
    assert result.undo is not None
    await service.undo(result.undo, command_id())
    proposal = await service.suggest(request(task))
    await repository.replace_calendars(
        (), NOW, DAY - timedelta(days=2), DAY + timedelta(days=2), ZONE.key
    )
    await session.commit()
    with pytest.raises(SchedulingChangedError):
        await service.apply(proposal, (task,), command_id())
    backwards = SchedulingService(session, clock=lambda: NOW - timedelta(seconds=1), zone=ZONE)
    with pytest.raises(SchedulingChangedError, match="clock"):
        await backwards.apply(proposal, (task,), command_id())


async def test_command_shapes_changed_undo_and_corrupt_receipt(session: AsyncSession) -> None:
    service, task = await setup(session)
    proposal = await service.suggest(request(task))
    for selected, key in (
        ((), command_id()),
        ((task, task), command_id()),
        ((task,), "bad"),
        ((str(uuid4()),), command_id()),
    ):
        with pytest.raises(SchedulingChangedError):
            await service.apply(proposal, selected, key)
    saved = await acknowledged(service, task)
    assert saved.undo is not None
    for token, key in (
        (saved.undo, "bad"),
        (saved.undo.model_copy(update={"blocks": saved.undo.blocks * 2}), command_id()),
        (PlanningUndo(command_id=command_id(), blocks=saved.undo.blocks), command_id()),
        (
            saved.undo.model_copy(
                update={"blocks": (PlanningBlockState(public_id=str(uuid4()), revision=1),)}
            ),
            command_id(),
        ),
    ):
        with pytest.raises(SchedulingChangedError):
            await service.undo(token, key)
    row = (await session.scalars(select(WorkBlockTable))).one()
    row.revision += 1
    await session.commit()
    with pytest.raises(SchedulingChangedError, match="changed"):
        await service.undo(saved.undo, command_id())
    receipt = (await session.scalars(select(PlanningCommandTable))).one()
    with pytest.raises(SchedulingChangedError, match="different input"):
        await service.apply(proposal, (task, str(uuid4())), receipt.command_id)
    receipt.results = [{"public_id": "bad", "revision": 1, "deleted": 0}]
    await session.commit()
    with pytest.raises(SchedulingChangedError, match="unavailable"):
        await service.undo(saved.undo, command_id())


async def test_unreadable_owner_preferences_do_not_guess_deadline_zone(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mailbrief.services.preferences import PreferencesService, PreferencesUnavailableError

    _, task = await setup(session)
    monkeypatch.setattr(
        PreferencesService, "get", AsyncMock(side_effect=PreferencesUnavailableError())
    )
    with pytest.raises(SchedulingChangedError, match="preferences are unavailable"):
        await SchedulingService(session, clock=lambda: NOW).suggest(request(task))


async def test_today_suggestion_leaves_time_for_review_before_apply(session: AsyncSession) -> None:
    """At least five minutes ahead, on the first quarter hour of the clock (C4-3): reviewed
    at 12:03:27, the suggestion starts at 12:15, not 12:08:27."""
    _, task = await setup(session)
    reviewed = NOW.replace(hour=12, minute=3, second=27, microsecond=5)
    service = SchedulingService(session, clock=lambda: reviewed, zone=ZONE)
    proposal = await service.suggest(request(task))
    assert proposal.blocks[0].starts_at_utc == NOW.replace(hour=12, minute=15)
    applying = SchedulingService(session, clock=lambda: reviewed + timedelta(minutes=1), zone=ZONE)
    with pytest.raises(SchedulingKnowledgeError) as caught:
        await applying.apply(proposal, (task,), command_id())
    result = await applying.apply(
        proposal, (task,), command_id(), acknowledged_knowledge=caught.value.token
    )
    assert len(result.blocks) == 1


@pytest.mark.parametrize(
    "value", [None, [], {}, [{"public_id": "bad", "revision": 1, "deleted": 0}]]
)
async def test_malformed_batch_control_data_cannot_replay(
    session: AsyncSession, value: object
) -> None:
    import json

    from sqlalchemy import text

    service, task = await setup(session)
    saved = await acknowledged(service, task)
    assert saved.undo is not None
    await session.execute(
        text("UPDATE planning_commands SET results=:value"), {"value": json.dumps(value)}
    )
    await session.commit()
    with pytest.raises(SchedulingChangedError, match="unavailable"):
        await service.undo(saved.undo, command_id())
