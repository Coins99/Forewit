"""P1's priorities and tags: the owner orders and finds tasks without needing a deadline."""

import asyncio
import json
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import ActionActivityKind as Kind
from mailbrief.domain.actions import ActionEdit, ActionFilter, ActionPriority
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.services.actions import ActionConflictError, ActionService
from mailbrief.services.data import export_writing
from mailbrief.services.today import needs_attention
from mailbrief.storage.backup import create_backup
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import upgrade_database
from mailbrief.storage.recovery import restore_backup
from mailbrief.storage.tables import ActionTagTable
from tests.factories import Clock
from tests.unit.services.test_actions_service import START, seed

TODAY = START.date()

TASK = ActionEdit(
    title="Renew passport",
    ownership=ActionOwnership.MINE,
    effort=None,
    priority=None,
    tags=(),
    estimate_minutes=None,
    target_date=None,
    notes="",
)


def task(**changes: Any) -> ActionEdit:
    """TASK with ``changes``, validated, so tags are normalized as the editor's would be."""
    return ActionEdit.model_validate(TASK.model_dump() | changes)


async def tag_rows(session: AsyncSession) -> list[tuple[str, str]]:
    rows = await session.execute(
        select(ActionTagTable.name, ActionTagTable.name_key).order_by(ActionTagTable.id)
    )
    return list(rows.tuples().all())


async def test_create_and_save_keep_priority_and_tags_in_one_revision(
    session: AsyncSession,
) -> None:
    service = ActionService(session, clock=Clock(START))
    created = await service.create(
        task(priority=ActionPriority.HIGH, tags=("#Errands", " home ", "HOME"))
    )
    assert created.priority is ActionPriority.HIGH
    assert created.tags == ("Errands", "home")  # Cleaned, one per key, sorted by key.
    assert [event.kind for event in created.activity] == [Kind.CREATED]

    saved = await service.save(
        created.public_id,
        1,
        task(priority=ActionPriority.LOW, tags=("Home", "Travel")),
    )
    assert saved.revision == 2
    assert saved.priority is ActionPriority.LOW
    assert saved.tags == ("Home", "Travel")
    assert [event.kind for event in saved.activity] == [Kind.CREATED, Kind.EDITED]
    # The kept tag's row stays and takes the new spelling; the dropped one goes.
    assert await tag_rows(session) == [("Home", "home"), ("Travel", "travel")]

    cleared = await service.save(saved.public_id, 2, task())
    assert (cleared.priority, cleared.tags) == (None, ())
    assert await tag_rows(session) == []


async def test_a_tag_keeps_the_spelling_other_tasks_use(session: AsyncSession) -> None:
    service = ActionService(session, clock=Clock(START))
    first = await service.create(task(title="Groceries", tags=("Home",)))
    second = await service.create(task(title="Fix tap", tags=("HOME", "diy")))
    assert second.tags == ("diy", "Home")
    # Only the task being saved may respell a tag no other live task has.
    alone = await service.save(second.public_id, 1, task(title="Fix tap", tags=("DIY",)))
    assert alone.tags == ("DIY",)
    await service.delete(first.public_id, 1)
    respelt = await service.create(task(title="Paint", tags=("hOmE",)))
    assert respelt.tags == ("hOmE",)  # The deleted task's spelling no longer counts.


async def test_respelling_a_tag_respells_it_on_every_live_task(session: AsyncSession) -> None:
    service = ActionService(session, clock=Clock(START))
    first = await service.create(task(title="Groceries", tags=("home",)))
    second = await service.create(task(title="Fix tap", tags=("home", "diy")))
    gone = await service.create(task(title="Old", tags=("home",)))
    await service.delete(gone.public_id, 1)

    respelt = await service.save(first.public_id, 1, task(title="Groceries", tags=("Home",)))
    assert respelt.tags == ("Home",)
    other = await service.get(second.public_id)
    assert other.tags == ("diy", "Home")
    assert other.revision == 2  # One revision of the other task, recorded as an edit.
    assert [event.kind for event in other.activity] == [Kind.CREATED, Kind.EDITED]
    with pytest.raises(ActionConflictError):
        await service.save(second.public_id, 1, task(title="Fix tap", tags=("home", "diy")))

    # A deleted task keeps its spelling until it returns, then reads as live tasks do.
    restored = await service.restore(gone.public_id)
    assert restored.tags == ("Home",)
    assert await service.list_tags() == ("diy", "Home")


async def test_tags_filter_and_list_only_live_tasks(session: AsyncSession) -> None:
    service = ActionService(session, clock=Clock(START))
    home = await service.create(task(title="Groceries", tags=("Home",)))
    await service.create(task(title="Gym", tags=("Health",)))
    waiting = await service.create(
        task(title="Plumber quote", ownership=ActionOwnership.WAITING_FOR, tags=("home",))
    )
    gone = await service.create(task(title="Old", tags=("Archive",)))
    await service.delete(gone.public_id, 1)
    done = await service.create(task(title="Bins", tags=("Home",)))
    await service.complete(done.public_id, 1)

    assert await service.list_tags() == ("Health", "Home")
    for key in ("home", "HOME"):
        listed = await service.list_actions(ActionFilter.OPEN, group="tag", group_id=key)
        assert [action.public_id for action in listed] == [home.public_id]
    waiting_listed = await service.list_actions(ActionFilter.WAITING, group="tag", group_id="home")
    assert [action.public_id for action in waiting_listed] == [waiting.public_id]
    assert await service.count_actions(ActionFilter.COMPLETED, group="tag", group_id="home") == 1
    assert await service.list_actions(ActionFilter.OPEN, group="tag", group_id="archive") == ()
    with pytest.raises(ValueError, match="tag"):
        await service.list_actions(ActionFilter.OPEN, group="tag", group_id=None)


async def test_priority_orders_tasks_with_the_same_dates(session: AsyncSession) -> None:
    clock = Clock(START)
    service = ActionService(session, clock=clock)
    titles = ("None", "Low", "High", "Medium")
    priorities = (None, ActionPriority.LOW, ActionPriority.HIGH, ActionPriority.MEDIUM)
    for title, priority in zip(titles, priorities, strict=True):
        clock.advance(minutes=1)
        await service.create(task(title=title, priority=priority))
    clock.advance(minutes=1)
    await service.create(task(title="Dated low", priority=ActionPriority.LOW, target_date=TODAY))
    clock.advance(minutes=1)
    await service.create(task(title="Dated none", target_date=TODAY))

    listed = [action.title for action in await service.list_actions(ActionFilter.OPEN)]
    # A target date still comes first; among equal dates, priority before creation.
    assert listed == ["Dated low", "Dated none", "High", "Medium", "Low", "None"]


async def test_today_orders_equal_reasons_by_priority(session: AsyncSession) -> None:
    clock = Clock(START)
    service = ActionService(session, clock=clock)
    for title, priority in (("Later", None), ("First", ActionPriority.HIGH)):
        clock.advance(minutes=1)
        await service.create(task(title=title, priority=priority, target_date=TODAY))
    actions = await service.list_actions(ActionFilter.OPEN)
    # In creation order, as another view might pass them: priority still decides.
    attention = needs_attention(
        sorted(actions, key=lambda action: action.created_at_utc),
        today=TODAY,
        zone=ZoneInfo("UTC"),
        now=START,
    )
    assert [item.action.title for item in attention] == ["First", "Later"]


async def test_accepted_suggestions_start_without_priority_or_tags(
    session: AsyncSession,
) -> None:
    seeded = await seed(session)
    accepted = await ActionService(session, clock=Clock(START)).accept(seeded.suggestion_ids[0])
    assert (accepted.priority, accepted.tags) == (None, ())


async def test_priority_and_tags_survive_restart_backup_restore_and_export(
    tmp_path: Path,
) -> None:
    path = tmp_path / "owner.sqlite3"
    await asyncio.to_thread(upgrade_database, path)
    database = Database.from_path(path)
    async with database.session() as session:
        created = await ActionService(session, clock=Clock(START)).create(
            task(priority=ActionPriority.MEDIUM, tags=("Travel", "Admin"))
        )
    await database.dispose()

    database = Database.from_path(path)
    async with database.session() as session:
        reread = await ActionService(session).get(created.public_id)
        assert (reread.priority, reread.tags) == (ActionPriority.MEDIUM, ("Admin", "Travel"))
    await asyncio.to_thread(create_backup, path, tmp_path / "backup.zip")
    await export_writing(database, tmp_path / "export.json")
    await database.dispose()

    payload = json.loads((tmp_path / "export.json").read_text(encoding="utf-8"))
    assert [row["name"] for row in payload["records"]["action_tags"]] == ["Admin", "Travel"]
    assert payload["records"]["actions"][0]["priority"] == "medium"

    restored = tmp_path / "restored.sqlite3"
    await asyncio.to_thread(restore_backup, tmp_path / "backup.zip", restored)
    database = Database.from_path(restored)
    async with database.session() as session:
        back = await ActionService(session).get(created.public_id)
        assert (back.priority, back.tags) == (ActionPriority.MEDIUM, ("Admin", "Travel"))
    await database.dispose()
