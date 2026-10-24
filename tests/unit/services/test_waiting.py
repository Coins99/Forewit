"""B08: who a task waits on or is for, and since when a waiting task waits."""

import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import Action, ActionEdit, ActionFilter, ActionStatus
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.recurrence import RepeatEdit, RepeatFrequency, RepeatRule
from mailbrief.domain.search import SearchKind
from mailbrief.services.actions import ActionConflictError, ActionService
from mailbrief.services.data import export_writing
from mailbrief.services.search import SearchService
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import upgrade_database
from mailbrief.storage.tables import ActionSeriesTable, MessageTable
from tests.factories import Clock, Ids
from tests.unit.services.test_actions_service import seed, suggestion

START = datetime(2026, 10, 15, 13, 0, tzinfo=UTC)  # A Thursday; 09:00 in Toronto.
TORONTO = ZoneInfo("America/Toronto")
DAILY = RepeatRule(frequency=RepeatFrequency.DAILY)

TASK = ActionEdit(
    title="Get the signed lease",
    ownership=ActionOwnership.WAITING_FOR,
    person="Alex Kim",
    effort=None,
    priority=None,
    tags=(),
    estimate_minutes=None,
    target_date=None,
    notes="",
)


def task(**changes: Any) -> ActionEdit:
    return ActionEdit.model_validate(TASK.model_dump() | changes)


def same(action: Action, **changes: Any) -> ActionEdit:
    """The edit that leaves ``action`` as it is, with ``changes``."""
    fields: dict[str, Any] = {
        "title": action.title,
        "ownership": action.ownership,
        "person": action.person,
        "target_date": action.target_date,
        "notes": action.notes,
    }
    return task(**(fields | changes))


@pytest.fixture
def clock() -> Clock:
    return Clock(START)


@pytest.fixture
def service(session: AsyncSession, clock: Clock) -> ActionService:
    return ActionService(session, clock=clock, id_factory=Ids(), zone=TORONTO)


async def test_a_new_waiting_task_waits_from_its_creation_and_names_its_person(
    service: ActionService,
) -> None:
    created = await service.create(task(person="  Alex \t Kim​ "))

    assert (created.ownership, created.person) == (ActionOwnership.WAITING_FOR, "Alex Kim")
    assert created.waiting_since_utc == START
    assert await service.get(created.public_id) == created


async def test_the_owner_s_own_task_never_waits_and_can_be_for_someone(
    service: ActionService,
) -> None:
    created = await service.create(task(ownership=ActionOwnership.MINE, person="Sam"))

    assert (created.person, created.waiting_since_utc) == ("Sam", None)
    plain = await service.create(task(ownership=ActionOwnership.MINE, person="  "))
    assert plain.person is None  # A blank name names no one.


async def test_the_wait_starts_when_a_task_becomes_someone_else_s_and_ends_with_it(
    service: ActionService, clock: Clock
) -> None:
    created = await service.create(task(ownership=ActionOwnership.MINE, person=None))
    clock.advance(days=2)

    waiting = await service.save(
        created.public_id, created.revision, same(created, ownership=ActionOwnership.WAITING_FOR)
    )
    assert waiting.waiting_since_utc == clock.now
    started = clock.now
    clock.advance(days=1)

    renamed = await service.save(waiting.public_id, waiting.revision, same(waiting, title="Lease"))
    assert renamed.waiting_since_utc == started  # Still waiting: the wait goes on.

    mine = await service.save(
        renamed.public_id, renamed.revision, same(renamed, ownership=ActionOwnership.MINE)
    )
    assert mine.waiting_since_utc is None
    clock.advance(days=1)

    again = await service.save(
        mine.public_id, mine.revision, same(mine, ownership=ActionOwnership.WAITING_FOR)
    )
    assert again.waiting_since_utc == clock.now  # A new wait, from now.


async def test_completing_and_reopening_keep_the_wait(service: ActionService, clock: Clock) -> None:
    created = await service.create(task())
    clock.advance(days=3)

    received = await service.complete(created.public_id, created.revision)
    assert received.waiting_since_utc == START
    reopened = await service.reopen(received.public_id, received.revision)
    assert reopened.waiting_since_utc == START


async def test_an_edit_sets_changes_and_clears_the_person(service: ActionService) -> None:
    created = await service.create(task())

    renamed = await service.save(created.public_id, created.revision, same(created, person="Sam"))
    assert renamed.person == "Sam"
    cleared = await service.save(renamed.public_id, renamed.revision, same(renamed, person=None))
    assert cleared.person is None
    assert await service.list_people() == ()


async def test_a_stale_edit_changes_neither_the_person_nor_the_wait(
    service: ActionService, clock: Clock
) -> None:
    created = await service.create(task())
    saved = await service.save(created.public_id, created.revision, same(created, person="Sam"))
    clock.advance(days=1)

    with pytest.raises(ActionConflictError):
        await service.save(
            created.public_id,
            created.revision,
            same(created, ownership=ActionOwnership.MINE, person="Priya"),
        )

    assert await service.get(created.public_id) == saved


async def test_accepting_a_waiting_suggestion_waits_from_then_and_names_no_one(
    session: AsyncSession, service: ActionService
) -> None:
    seeded = await seed(
        session,
        (suggestion(0, "Wait for the signed copy", ownership=ActionOwnership.WAITING_FOR),),
    )

    accepted = await service.accept(seeded.suggestion_ids[0])

    assert accepted.ownership is ActionOwnership.WAITING_FOR
    assert (accepted.waiting_since_utc, accepted.person) == (START, None)
    (source,) = accepted.sources
    # The cached message names its sender; the snapshot keeps only the address.
    assert (source.sender_name, source.sender_address) == ("Alex", "alex@example.com")

    await session.execute(delete(MessageTable))
    await session.commit()
    (gone,) = (await service.get(accepted.public_id)).sources
    assert (gone.available, gone.sender_name, gone.sender_address) == (
        False,
        None,
        "alex@example.com",
    )


async def test_accepting_one_of_the_owner_s_suggestions_never_waits(
    session: AsyncSession, service: ActionService
) -> None:
    seeded = await seed(session, (suggestion(0, "Send the deck"),))

    accepted = await service.accept(seeded.suggestion_ids[0])

    assert (accepted.ownership, accepted.waiting_since_utc) == (ActionOwnership.MINE, None)


async def test_people_match_whatever_their_case_or_width(service: ActionService) -> None:
    first = await service.create(task(person="Alex"))
    await service.create(task(title="Keys", person="ALEX"))
    await service.create(task(title="Deposit", person="Ａｌｅｘ"))  # Full width.
    await service.create(task(title="Plans", ownership=ActionOwnership.MINE, person="Sam"))
    await service.create(task(title="Nobody", person=None))

    assert await service.list_people() == ("Alex", "Sam")  # The earliest spelling of each.
    assert await service.list_people(ActionFilter.WAITING) == ("Alex",)
    assert await service.list_people(ActionFilter.OPEN) == ("Sam",)
    alex = await service.list_actions(ActionFilter.WAITING, group="person", group_id="aLeX")
    assert {action.title for action in alex} == {first.title, "Keys", "Deposit"}
    assert await service.count_actions(ActionFilter.WAITING, group="person", group_id="alex") == 3
    assert await service.list_actions(ActionFilter.OPEN, group="person", group_id="alex") == ()


async def test_a_deleted_task_s_person_is_offered_no_more(service: ActionService) -> None:
    created = await service.create(task(person="Priya"))

    await service.delete(created.public_id, created.revision)

    assert await service.list_people() == ()


async def test_search_finds_a_task_by_whom_it_waits_on(
    session: AsyncSession, service: ActionService
) -> None:
    created = await service.create(task())

    results = await SearchService(session).search("kim lease")

    assert [(hit.kind, hit.public_id, hit.waiting) for hit in results.hits] == [
        (SearchKind.ACTION, created.public_id, True)
    ]


async def test_a_repeat_keeps_the_person_and_waits_from_when_it_is_made(
    session: AsyncSession, service: ActionService, clock: Clock
) -> None:
    first = await service.create(task(), (), DAILY)
    clock.advance(days=1)

    await service.complete(first.public_id, first.revision)

    (following,) = await service.list_actions(ActionFilter.WAITING)
    assert following.public_id != first.public_id
    assert (following.person, following.waiting_since_utc) == ("Alex Kim", clock.now)
    series = await session.get(ActionSeriesTable, 1)
    assert series is not None and series.person == "Alex Kim"


async def test_change_later_repeats_too_carries_a_new_person(
    service: ActionService, clock: Clock
) -> None:
    first = await service.create(task(), (), DAILY)
    assert first.repeat is not None
    renamed = await service.save(
        first.public_id,
        first.revision,
        same(first, person="Sam"),
        repeat=RepeatEdit(rule=DAILY, series_revision=first.repeat.series_revision),
    )
    assert renamed.repeat is not None
    clock.advance(days=1)

    await service.complete(renamed.public_id, renamed.revision)

    (following,) = await service.list_actions(ActionFilter.WAITING)
    assert following.person == "Sam"
    assert following.repeat is not None
    only_this = await service.save(
        following.public_id,
        following.revision,
        same(following, person="Priya"),
        repeat=RepeatEdit(
            rule=DAILY, series_revision=following.repeat.series_revision, later_too=False
        ),
    )
    clock.advance(days=1)
    await service.complete(only_this.public_id, only_this.revision)
    (third,) = await service.list_actions(ActionFilter.WAITING)
    assert third.person == "Sam"  # The template's, not the occurrence's own change.


async def test_a_waiting_occurrence_becoming_the_owner_s_makes_mine_ones(
    service: ActionService, clock: Clock
) -> None:
    first = await service.create(task(), (), DAILY)
    assert first.repeat is not None
    mine = await service.save(
        first.public_id,
        first.revision,
        same(first, ownership=ActionOwnership.MINE),
        repeat=RepeatEdit(rule=DAILY, series_revision=first.repeat.series_revision),
    )
    clock.advance(days=1)

    await service.complete(mine.public_id, mine.revision)

    (following,) = await service.list_actions(ActionFilter.OPEN)
    assert (following.ownership, following.waiting_since_utc) == (ActionOwnership.MINE, None)
    assert following.status is ActionStatus.OPEN


async def test_the_writing_export_carries_the_person_and_the_wait(tmp_path: Path) -> None:
    path = tmp_path / "waiting.sqlite3"
    await asyncio.to_thread(upgrade_database, path)
    database = Database.from_path(path)
    async with database.session() as session:
        service = ActionService(session, clock=Clock(START), id_factory=Ids(), zone=TORONTO)
        await service.create(task(target_date=date(2026, 10, 19)), (), DAILY)
    exported = tmp_path / "writing.json"

    await export_writing(database, exported)
    await database.dispose()

    records = json.loads(exported.read_text(encoding="utf-8"))["records"]
    (action,) = records["actions"]
    assert (action["person"], action["person_key"]) == ("Alex Kim", "alex kim")
    assert action["waiting_since_utc"].startswith("2026-10-15")
    (series,) = records["action_series"]
    assert series["person"] == "Alex Kim"
