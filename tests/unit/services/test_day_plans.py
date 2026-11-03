"""Day selection owns order, never task dates or completion facts."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.day_plans import DayPlan
from mailbrief.domain.recurrence import RepeatFrequency, RepeatRule
from mailbrief.services.actions import ActionService
from mailbrief.services.data import (
    CleanupKind,
    CleanupRequest,
    apply_cleanup,
    export_writing,
    preview_cleanup,
)
from mailbrief.services.day_plans import DayPlanConflictError, DayPlanService
from mailbrief.services.today import TodayService
from mailbrief.storage.backup import create_backup
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import upgrade_database
from mailbrief.storage.recovery import restore_backup
from mailbrief.storage.tables import DayPlanItemTable, DayPlanTable
from tests.unit.services.test_manual_tasks import EDIT

DAY = date(2026, 11, 3)
ZONE = ZoneInfo("America/Toronto")
NOW = datetime(2026, 11, 3, 16, tzinfo=UTC)


async def test_empty_read_does_not_create_a_plan(session: AsyncSession) -> None:
    plan = await DayPlanService(session).load(DAY)
    assert plan.revision == 0 and not plan.items
    assert await session.scalar(select(func.count()).select_from(DayPlanTable)) == 0


async def test_select_deselect_and_noop_leave_task_fields_and_history_unchanged(
    session: AsyncSession,
) -> None:
    actions, plans = ActionService(session), DayPlanService(session)
    action = await actions.create(EDIT)
    history = await actions.history(action.public_id)
    first = await plans.select(DAY, ZONE, action.public_id, 0)
    assert first.revision == 1 and first.creation_zone == ZONE.key
    assert first.known_minutes == 90 and first.unknown_estimates == 0
    assert await plans.select(DAY, ZoneInfo("Asia/Tokyo"), action.public_id, 1) == first
    assert await actions.get(action.public_id) == action
    assert await plans.reorder(DAY, [first.items[0].public_id], 1) == first
    removed = await plans.deselect(DAY, first.items[0].public_id, 1)
    assert removed.revision == 2 and not removed.items
    assert removed.public_id == first.public_id
    assert await plans.deselect(DAY, first.items[0].public_id, 2) == removed
    assert await actions.get(action.public_id) == action
    assert await actions.history(action.public_id) == history


async def test_order_reverses_without_unique_position_collisions_and_rejects_bad_permutations(
    session: AsyncSession,
) -> None:
    actions, plans = ActionService(session), DayPlanService(session)
    ids = [
        (await actions.create(EDIT.model_copy(update={"title": f"Task {i}"}))).public_id
        for i in range(3)
    ]
    plan = await plans.load(DAY)
    for task_id in ids:
        plan = await plans.select(DAY, ZONE, task_id, plan.revision)
    original_items = [item.public_id for item in plan.items]
    reversed_plan = await plans.reorder(DAY, original_items[::-1], plan.revision)
    assert [item.task_public_id for item in reversed_plan.items] == ids[::-1]
    assert [item.position for item in reversed_plan.items] == [0, 1, 2]
    for bad in (original_items[:2], [original_items[0]] * 3, ["missing", *original_items[:2]]):
        with pytest.raises(DayPlanConflictError, match="Reorder all"):
            await plans.reorder(DAY, bad, reversed_plan.revision)
    assert await plans.load(DAY) == reversed_plan


async def test_failed_reorder_rolls_back_order_and_revision(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    actions, plans = ActionService(session), DayPlanService(session)
    plan = await plans.load(DAY)
    for _ in range(2):
        action = await actions.create(EDIT)
        plan = await plans.select(DAY, ZONE, action.public_id, plan.revision)
    with monkeypatch.context() as patch:
        patch.setattr(
            session, "commit", AsyncMock(side_effect=RuntimeError("Synthetic disk failure"))
        )
        with pytest.raises(RuntimeError):
            await plans.reorder(DAY, [item.public_id for item in plan.items][::-1], plan.revision)
    assert await plans.load(DAY) == plan


async def test_current_task_projection_completion_delete_restore_and_waiting_workload(
    session: AsyncSession,
) -> None:
    actions, plans = ActionService(session), DayPlanService(session)
    known = await actions.create(EDIT)
    unknown = await actions.create(EDIT.model_copy(update={"estimate_minutes": None}))
    waiting = await actions.create(
        EDIT.model_copy(update={"ownership": ActionOwnership.WAITING_FOR})
    )
    plan = await plans.load(DAY)
    for task in (known, unknown, waiting):
        plan = await plans.select(DAY, ZONE, task.public_id, plan.revision)
    assert (plan.known_minutes, plan.unknown_estimates, plan.waiting_count) == (90, 1, 1)
    completed = await actions.complete(known.public_id, known.revision)
    current = await plans.load(DAY)
    assert current.revision == plan.revision and len(current.items) == 3
    assert current.known_minutes == 0
    await actions.undo_status(completed.public_id, completed.revision)
    assert (await plans.load(DAY)).known_minutes == 90
    await actions.delete(unknown.public_id, unknown.revision)
    assert (await plans.load(DAY)).items[1].action is None
    await actions.restore(unknown.public_id)
    assert (await plans.load(DAY)).items[1].action is not None


async def test_invalid_selection_does_not_leave_an_empty_plan(session: AsyncSession) -> None:
    actions, plans = ActionService(session), DayPlanService(session)
    action = await actions.create(EDIT)
    await actions.complete(action.public_id, action.revision)
    for task_id in (action.public_id, "missing"):
        with pytest.raises(DayPlanConflictError, match="available, open"):
            await plans.select(DAY, ZONE, task_id, 0)
        assert (await plans.load(DAY)).revision == 0


async def test_independent_session_edits_invalidate_an_old_plan(database: Database) -> None:
    async with database.session() as first:
        actions, plans = ActionService(first), DayPlanService(first)
        a, b = await actions.create(EDIT), await actions.create(EDIT)
        old = await plans.select(DAY, ZONE, a.public_id, 0)
        async with database.session() as other:
            changed = await DayPlanService(other).select(DAY, ZONE, b.public_id, old.revision)
        with pytest.raises(DayPlanConflictError, match="changed"):
            await plans.deselect(DAY, old.items[0].public_id, old.revision)
        assert await plans.load(DAY) == changed


@pytest.mark.parametrize("existing", [False, True])
async def test_concurrent_plan_writers_require_reload(
    database: Database, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    async with database.session() as session:
        actions, plans = ActionService(session), DayPlanService(session)
        first, second = await actions.create(EDIT), await actions.create(EDIT)
        if existing:
            initial = await actions.create(EDIT)
            await plans.select(DAY, ZONE, initial.public_id, 0)
    checked = DayPlanService._checked
    both_read = asyncio.Event()
    reads = 0

    async def together(self: DayPlanService, day: date, revision: int) -> DayPlanTable | None:
        nonlocal reads
        row = await checked(self, day, revision)
        reads += 1
        if reads <= 2:
            if reads == 2:
                both_read.set()
            await asyncio.wait_for(both_read.wait(), timeout=20)
        return row

    monkeypatch.setattr(DayPlanService, "_checked", together)

    async def write(task_id: str) -> DayPlan:
        async with database.session() as session:
            return await DayPlanService(session).select(DAY, ZONE, task_id, int(existing))

    results = await asyncio.gather(
        write(first.public_id), write(second.public_id), return_exceptions=True
    )
    assert sum(isinstance(result, DayPlan) for result in results) == 1
    assert sum(isinstance(result, DayPlanConflictError) for result in results) == 1
    async with database.session() as session:
        plan = await DayPlanService(session).load(DAY)
        assert len(plan.items) == 1 + int(existing)
        assert plan.revision == 1 + int(existing)


async def test_cap_and_duplicate_selection(session: AsyncSession) -> None:
    action = await ActionService(session).create(EDIT)
    plans = DayPlanService(session)
    first = await plans.select(DAY, ZONE, action.public_id, 0)
    row = await session.scalar(select(DayPlanTable).where(DayPlanTable.local_date == DAY))
    assert row is not None
    for index in range(1, 100):
        task = await ActionService(session).create(EDIT)
        session.add(
            DayPlanItemTable(
                public_id=str(uuid4()),
                plan_id=row.id,
                task_public_id=task.public_id,
                position=index,
                created_at_utc=NOW,
                updated_at_utc=NOW,
            )
        )
    await session.commit()
    plan = await plans.select(DAY, ZONE, action.public_id, first.revision)
    assert len(plan.items) == 100 and plan.revision == first.revision
    assert len(await plans.candidates()) == 50
    another = await ActionService(session).create(EDIT)
    with pytest.raises(DayPlanConflictError, match="at most 100"):
        await plans.select(DAY, ZONE, another.public_id, plan.revision)


async def test_one_profile_date_across_zones_and_no_midnight_carry(session: AsyncSession) -> None:
    actions, plans = ActionService(session), DayPlanService(session)
    a, b = await actions.create(EDIT), await actions.create(EDIT)
    first = await plans.select(DAY, ZONE, a.public_id, 0)
    same = await plans.select(DAY, ZoneInfo("Asia/Tokyo"), b.public_id, 1)
    assert same.public_id == first.public_id and same.creation_zone == ZONE.key
    assert (await plans.load(DAY + timedelta(days=1))).items == ()


async def test_selected_tasks_are_not_duplicated_in_other_attention(session: AsyncSession) -> None:
    actions = ActionService(session)
    task = await actions.create(EDIT.model_copy(update={"target_date": DAY}))
    await DayPlanService(session).select(DAY, ZONE, task.public_id, 0)
    summary = await TodayService(session).summary(DAY, ZONE, NOW)
    assert not summary.attention
    assert summary.focus is not None and summary.focus.items[0].task_public_id == task.public_id


async def test_candidates_are_bounded_parameterized_open_owner_records(
    session: AsyncSession,
) -> None:
    actions, plans = ActionService(session), DayPlanService(session)
    literal = await actions.create(EDIT.model_copy(update={"title": "100%_literal task"}))
    other = await actions.create(EDIT.model_copy(update={"title": "Other task"}))
    assert [a.public_id for a in await plans.candidates("%_")] == [literal.public_id]
    assert not await plans.candidates("x")
    await actions.complete(other.public_id, other.revision)
    assert [a.public_id for a in await plans.candidates()] == [literal.public_id]


async def test_selected_matches_do_not_consume_the_candidate_limit(session: AsyncSession) -> None:
    actions, plans = ActionService(session), DayPlanService(session)
    tasks = [
        await actions.create(EDIT.model_copy(update={"title": "Follow up"})) for _ in range(51)
    ]
    offered = await plans.candidates("Follow up", day=DAY)
    assert len(offered) == 50
    plan = await plans.load(DAY)
    for task in offered:
        plan = await plans.select(DAY, ZONE, task.public_id, plan.revision)
    remaining = await plans.candidates("Follow up", day=DAY)
    assert {task.public_id for task in remaining} == (
        {task.public_id for task in tasks} - {item.task_public_id for item in plan.items}
    )
    assert len(remaining) == 1
    assert len(await plans.candidates("Follow up", day=DAY + timedelta(days=1))) == 50
    await plans.deselect(DAY, plan.items[0].public_id, plan.revision)
    assert len(await plans.candidates("Follow up", day=DAY)) == 2


async def test_recurring_selection_retains_exact_occurrence_and_owner_selected_successor(
    session: AsyncSession,
) -> None:
    actions, plans = ActionService(session), DayPlanService(session)
    original = await actions.create(EDIT, repeat=RepeatRule(frequency=RepeatFrequency.DAILY))
    selected = await plans.select(DAY, ZONE, original.public_id, 0)
    completed = await actions.complete(original.public_id, original.revision)
    plan = await plans.load(DAY)
    assert plan.items[0].task_public_id == original.public_id and plan.revision == selected.revision
    from mailbrief.domain.actions import ActionFilter

    successor = next(
        a
        for a in await actions.list_actions(ActionFilter.OPEN)
        if a.public_id != original.public_id
    )
    plan = await plans.select(DAY, ZONE, successor.public_id, plan.revision)
    await actions.undo_status(completed.public_id, completed.revision)
    assert len((await plans.load(DAY)).items) == 2
    assert (await actions.get(successor.public_id)).public_id == successor.public_id


async def test_restart_backup_export_restore_and_purge(tmp_path: Path) -> None:
    path = tmp_path / "owner.sqlite3"
    await asyncio.to_thread(upgrade_database, path)
    db = Database.from_path(path)
    async with db.session() as session:
        actions, plans = ActionService(session), DayPlanService(session)
        task = await actions.create(EDIT)
        plan = await plans.select(DAY, ZONE, task.public_id, 0)
    exported, backup, restored_path = (
        tmp_path / name for name in ("writing.json", "backup.zip", "restored.sqlite3")
    )
    await export_writing(db, exported)
    records = json.loads(exported.read_text("utf-8"))["records"]
    assert len(records["day_plans"]) == len(records["day_plan_items"]) == 1
    await db.dispose()
    await asyncio.to_thread(create_backup, path, backup)
    await asyncio.to_thread(restore_backup, backup, restored_path)
    restored = Database.from_path(restored_path)
    try:
        async with restored.session() as session:
            assert await DayPlanService(session).load(DAY) == plan
            await ActionService(session).delete(task.public_id, task.revision)
        preview = await preview_cleanup(
            restored,
            CleanupRequest(CleanupKind.DELETED, before=datetime.now(UTC) + timedelta(days=1)),
        )
        assert preview.day_selections == 1
        assert "keep their day plans" in preview.summary
        await apply_cleanup(restored, preview)
        async with restored.session() as session:
            removed = await DayPlanService(session).load(DAY)
            assert removed.revision == plan.revision + 1 and not removed.items
            assert removed.public_id == plan.public_id
    finally:
        await restored.dispose()
