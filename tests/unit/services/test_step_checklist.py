"""B04: an open task's steps ticked, unticked and added in place, one revision each."""

import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import (
    ACTION_STEP_MAX_CHARS,
    MAX_ACTION_STEPS,
    Action,
    ActionEdit,
    ActionFilter,
    ActionStatus,
    StepEdit,
)
from mailbrief.domain.actions import ActionActivityKind as Kind
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.recurrence import RepeatFrequency, RepeatRule
from mailbrief.services.actions import ActionConflictError, ActionNotFoundError, ActionService
from mailbrief.services.data import export_writing
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import upgrade_database
from mailbrief.storage.tables import ActionActivityTable, ActionStepTable
from tests.factories import Clock, Ids
from tests.unit.services.test_actions_service import seed

START = datetime(2026, 10, 15, 13, 0, tzinfo=UTC)  # A Thursday; 09:00 in Toronto.
TORONTO = ZoneInfo("America/Toronto")
DAILY = RepeatRule(frequency=RepeatFrequency.DAILY)
STALE = "changed since it was loaded"
CLOSED = "Reopen the task to change its plan."

TASK = ActionEdit(
    title="Plan the party",
    ownership=ActionOwnership.MINE,
    person=None,
    effort=None,
    priority=None,
    tags=(),
    estimate_minutes=None,
    target_date=date(2026, 10, 20),
    notes="",
)


def new_steps(*texts: str, done: bool = False) -> list[StepEdit]:
    return [StepEdit(step_id=None, text=text, done=done) for text in texts]


def plan(action: Action) -> list[tuple[str, bool]]:
    return [(step.text, step.done) for step in action.steps]


def kinds(action: Action) -> list[Kind]:
    return [event.kind for event in action.activity]


@pytest.fixture
def clock() -> Clock:
    return Clock(START)


@pytest.fixture
def service(session: AsyncSession, clock: Clock) -> ActionService:
    return ActionService(session, clock=clock, id_factory=Ids(), zone=TORONTO)


async def history_count(session: AsyncSession) -> int:
    return await session.scalar(select(func.count()).select_from(ActionActivityTable)) or 0


async def test_ticking_a_step_is_one_revision_recorded_as_an_edit(
    service: ActionService, clock: Clock
) -> None:
    created = await service.create(TASK, new_steps("Book the venue", "Send invites"))
    first, second = created.steps
    clock.advance(minutes=5)

    ticked = await service.set_step_done(created.public_id, 1, first.step_id, True)

    assert ticked.revision == 2
    assert ticked.updated_at_utc == clock.now
    assert plan(ticked) == [("Book the venue", True), ("Send invites", False)]
    assert ticked.steps[0].done_at_utc == clock.now
    assert ticked.steps[1] == second  # The other step is as it was.
    assert kinds(ticked) == [Kind.CREATED, Kind.EDITED]  # Older builds read an edit.
    assert (ticked.title, ticked.target_date, ticked.status) == (
        created.title,
        created.target_date,
        ActionStatus.OPEN,
    )
    assert await service.get(created.public_id) == ticked


async def test_a_done_step_keeps_its_time_until_it_is_unticked(
    service: ActionService, clock: Clock
) -> None:
    created = await service.create(TASK, new_steps("Book the venue"))
    step_id = created.steps[0].step_id
    ticked_at = clock.now
    ticked = await service.set_step_done(created.public_id, 1, step_id, True)
    clock.advance(hours=1)

    again = await service.set_step_done(created.public_id, 2, step_id, True)

    assert again == ticked  # Already done: no revision, and it keeps the time it was done.
    assert again.steps[0].done_at_utc == ticked_at

    unticked = await service.set_step_done(created.public_id, 2, step_id, False)
    assert unticked.revision == 3
    assert (unticked.steps[0].done, unticked.steps[0].done_at_utc) == (False, None)
    assert await service.set_step_done(created.public_id, 3, step_id, False) == unticked

    clock.advance(hours=1)
    retaken = await service.set_step_done(created.public_id, 3, step_id, True)
    assert retaken.steps[0].done_at_utc == clock.now
    assert kinds(retaken) == [Kind.CREATED, Kind.EDITED, Kind.EDITED, Kind.EDITED]


async def test_a_stale_screen_changes_no_step(
    service: ActionService, session: AsyncSession
) -> None:
    created = await service.create(TASK, new_steps("Book the venue"))
    step = created.steps[0]
    await service.save(created.public_id, 1, TASK.model_copy(update={"notes": "Changed"}))
    written = await history_count(session)

    with pytest.raises(ActionConflictError, match=STALE):
        await service.set_step_done(created.public_id, 1, step.step_id, True)
    with pytest.raises(ActionConflictError, match=STALE):
        await service.set_step_done(created.public_id, 1, step.step_id, False)  # Even as it is.
    with pytest.raises(ActionConflictError, match=STALE):
        await service.add_step(created.public_id, 1, "Call the caterer")

    current = await service.get(created.public_id)
    assert current.revision == 2
    assert plan(current) == [("Book the venue", False)]
    assert await history_count(session) == written


async def test_only_the_task_s_own_steps_are_ticked(service: ActionService) -> None:
    mine = await service.create(TASK, new_steps("Book the venue"))
    other = await service.create(TASK, new_steps("Buy candles"))

    with pytest.raises(ActionConflictError, match="no longer in this task's plan"):
        await service.set_step_done(mine.public_id, 1, other.steps[0].step_id, True)

    removed = await service.save(mine.public_id, 1, TASK, [])  # The editor removed the step.
    with pytest.raises(ActionConflictError, match="no longer in this task's plan"):
        await service.set_step_done(mine.public_id, removed.revision, mine.steps[0].step_id, True)
    assert plan(await service.get(other.public_id)) == [("Buy candles", False)]


async def test_a_deleted_task_s_plan_is_not_found(service: ActionService) -> None:
    created = await service.create(TASK, new_steps("Book the venue"))
    await service.delete(created.public_id, 1)

    with pytest.raises(ActionNotFoundError):
        await service.set_step_done(created.public_id, 2, created.steps[0].step_id, True)
    with pytest.raises(ActionNotFoundError):
        await service.add_step(created.public_id, 2, "Call the caterer")


async def test_a_closed_task_s_plan_changes_once_it_is_reopened(
    service: ActionService, session: AsyncSession
) -> None:
    created = await service.create(TASK, new_steps("Book the venue"))
    step_id = created.steps[0].step_id
    done = await service.complete(created.public_id, 1)
    written = await history_count(session)

    with pytest.raises(ActionConflictError, match=CLOSED):
        await service.set_step_done(done.public_id, done.revision, step_id, True)
    with pytest.raises(ActionConflictError, match=CLOSED):
        await service.add_step(done.public_id, done.revision, "Call the caterer")
    assert await history_count(session) == written

    opened = await service.reopen(done.public_id, done.revision)
    ticked = await service.set_step_done(opened.public_id, opened.revision, step_id, True)
    added = await service.add_step(ticked.public_id, ticked.revision, "Call the caterer")
    assert plan(added) == [("Book the venue", True), ("Call the caterer", False)]


async def test_an_added_step_comes_last_and_not_done(service: ActionService, clock: Clock) -> None:
    empty = await service.create(TASK)
    clock.advance(minutes=1)

    first = await service.add_step(empty.public_id, 1, "  Book the venue  ")

    assert plan(first) == [("Book the venue", False)]  # The owner's text, trimmed.
    assert first.steps[0].position == 0
    assert first.revision == 2
    assert first.updated_at_utc == clock.now
    assert kinds(first) == [Kind.CREATED, Kind.EDITED]

    reordered = await service.save(
        first.public_id,
        2,
        TASK,
        [
            *new_steps("Send invites", done=True),
            *[
                StepEdit(step_id=step.step_id, text=step.text, done=step.done)
                for step in first.steps
            ],
        ],
    )
    added = await service.add_step(reordered.public_id, reordered.revision, "Buy candles")
    assert plan(added) == [
        ("Send invites", True),
        ("Book the venue", False),
        ("Buy candles", False),
    ]
    assert [step.position for step in added.steps] == [0, 1, 2]


@pytest.mark.parametrize("text", ["", "   ", "x" * (ACTION_STEP_MAX_CHARS + 1)])
async def test_a_step_needs_one_to_500_characters(
    service: ActionService, session: AsyncSession, text: str
) -> None:
    created = await service.create(TASK)
    written = await history_count(session)

    with pytest.raises(ActionConflictError, match="A step needs 1 to 500 characters."):
        await service.add_step(created.public_id, 1, text)

    assert (await service.get(created.public_id)).revision == 1
    assert await history_count(session) == written


async def test_the_longest_step_is_kept_whole(service: ActionService) -> None:
    created = await service.create(TASK)
    longest = "x" * ACTION_STEP_MAX_CHARS

    added = await service.add_step(created.public_id, 1, f" {longest} ")

    assert plan(added) == [(longest, False)]


async def test_a_plan_holds_at_most_30_steps(service: ActionService, session: AsyncSession) -> None:
    full = await service.create(
        TASK, new_steps(*(f"Step {number}" for number in range(MAX_ACTION_STEPS)))
    )

    with pytest.raises(ActionConflictError, match="at most 30 steps"):
        await service.add_step(full.public_id, 1, "One more")

    assert len((await service.get(full.public_id)).steps) == MAX_ACTION_STEPS
    count = await session.scalar(select(func.count()).select_from(ActionStepTable))
    assert count == MAX_ACTION_STEPS


async def test_an_email_action_s_steps_tick_and_grow_as_a_task_s(
    service: ActionService, session: AsyncSession
) -> None:
    seeded = await seed(session)
    accepted = await service.accept(seeded.suggestion_ids[0])
    assert plan(accepted) == [("Collect the figures", False), ("Draft the slides", False)]

    ticked = await service.set_step_done(accepted.public_id, 1, accepted.steps[0].step_id, True)
    added = await service.add_step(ticked.public_id, ticked.revision, "Ask Sam for the chart")

    assert plan(added) == [
        ("Collect the figures", True),
        ("Draft the slides", False),
        ("Ask Sam for the chart", False),
    ]
    assert added.sources == accepted.sources
    assert added.deadline_text == accepted.deadline_text
    assert kinds(added) == [Kind.CREATED, Kind.EDITED, Kind.EDITED]
    # Changed, it is no longer an acceptance Undo can take back.
    with pytest.raises(ActionConflictError):
        await service.unaccept(added.public_id, added.revision)


async def test_a_repeat_s_next_occurrence_starts_from_the_series_plan(
    service: ActionService,
) -> None:
    """An added step and the ticks are this occurrence's; the next starts from the template."""
    first = await service.create(TASK, new_steps("Water the fern"), DAILY)
    ticked = await service.set_step_done(first.public_id, 1, first.steps[0].step_id, True)
    added = await service.add_step(ticked.public_id, ticked.revision, "Feed the fish")
    assert plan(added) == [("Water the fern", True), ("Feed the fish", False)]

    await service.complete(added.public_id, added.revision)

    following = [
        action
        for action in await service.list_actions(ActionFilter.OPEN)
        if action.public_id != first.public_id
    ]
    assert [plan(action) for action in following] == [[("Water the fern", False)]]


async def test_a_ticked_next_occurrence_stays_when_the_last_is_reopened(
    service: ActionService,
) -> None:
    """Ticking a step touches the occurrence, so reopening the one before keeps it."""
    first = await service.create(TASK, new_steps("Water the fern"), DAILY)
    done = await service.complete(first.public_id, 1)
    (following,) = [
        action
        for action in await service.list_actions(ActionFilter.OPEN)
        if action.public_id != first.public_id
    ]
    await service.set_step_done(following.public_id, 1, following.steps[0].step_id, True)

    await service.reopen(done.public_id, done.revision)

    still_open = {action.public_id for action in await service.list_actions(ActionFilter.OPEN)}
    assert still_open == {first.public_id, following.public_id}


async def test_a_skipped_occurrence_s_plan_is_closed(service: ActionService) -> None:
    first = await service.create(TASK, new_steps("Water the fern"), DAILY)
    skipped = await service.skip(first.public_id, 1)

    with pytest.raises(ActionConflictError, match=CLOSED):
        await service.set_step_done(
            skipped.public_id, skipped.revision, first.steps[0].step_id, True
        )


async def test_ticks_and_added_steps_survive_restart_and_the_writing_export(
    tmp_path: Path,
) -> None:
    """A database brought up to date by the existing migrations holds them: B04 adds none."""
    path = tmp_path / "steps.sqlite3"
    await asyncio.to_thread(upgrade_database, path)
    clock = Clock(START)
    database = Database.from_path(path)
    async with database.session() as session:
        service = ActionService(session, clock=clock, zone=TORONTO)
        created = await service.create(TASK, new_steps("Book the venue"))
        clock.advance(minutes=3)
        ticked = await service.set_step_done(created.public_id, 1, created.steps[0].step_id, True)
        final = await service.add_step(ticked.public_id, ticked.revision, "Send invites")
    await database.dispose()

    restarted = Database.from_path(path)
    try:
        async with restarted.session() as session:
            assert await ActionService(session).get(final.public_id) == final
        exported = tmp_path / "writing.json"
        await export_writing(restarted, exported)
    finally:
        await restarted.dispose()
    records: dict[str, list[dict[str, Any]]] = json.loads(exported.read_text(encoding="utf-8"))[
        "records"
    ]
    assert [(step["text"], step["done"]) for step in records["action_steps"]] == [
        ("Book the venue", True),
        ("Send invites", False),
    ]
    assert [event["kind"] for event in records["action_activity"]] == [
        "created",
        "edited",
        "edited",
    ]
