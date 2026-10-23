"""Deadlines the owner sets (B06): set, change, clear, go back to the email's, and keep it."""

import asyncio
import importlib.resources
import json
from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import (
    DEADLINE_OUT_OF_RANGE,
    Action,
    ActionActivityKind,
    ActionEdit,
    DeadlineChange,
)
from mailbrief.domain.actions import DeadlineOrigin as Origin
from mailbrief.domain.analysis import ActionOwnership, DeadlinePrecision, TargetReason
from mailbrief.services.actions import ActionConflictError, ActionService
from mailbrief.services.data import export_writing
from mailbrief.services.deadlines import owner_deadline
from mailbrief.services.drafting import _deadline_words
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import upgrade_database
from tests.factories import Clock, Ids, make_action
from tests.unit.services.test_actions_service import START, TORONTO, seed, suggestion

FRIDAY = date(2026, 10, 2)  # START is Monday 28 September, 09:00 in Toronto.
MONDAY = date(2026, 10, 5)


@pytest.fixture
def clock() -> Clock:
    return Clock(START)


@pytest.fixture
def service(session: AsyncSession, clock: Clock) -> ActionService:
    return ActionService(session, clock=clock, id_factory=Ids(), zone=TORONTO)


def edit(action: Action | None = None, **changes: object) -> ActionEdit:
    """The editor's fields for ``action`` as they stand, or a new task's, with ``changes``."""
    values: dict[str, object] = {
        "title": "Plan the week" if action is None else action.title,
        "ownership": ActionOwnership.MINE if action is None else action.ownership,
        "person": None if action is None else action.person,
        "priority": None,
        "tags": (),
        "effort": None if action is None else action.effort,
        "estimate_minutes": None,
        "target_date": None if action is None else action.target_date,
        "notes": "",
    }
    values.update(changes)
    return ActionEdit.model_validate(values)


async def accepted(session: AsyncSession, service: ActionService) -> Action:
    """A task accepted from an email that says "by Friday", with Thursday as its target."""
    return await service.accept((await seed(session)).suggestion_ids[0])


def deadline(action: Action) -> tuple[object, ...]:
    return (
        action.deadline_origin,
        action.deadline_precision,
        action.deadline_date,
        action.deadline_at_utc,
        action.deadline_timezone,
        action.deadline_text,
    )


async def test_a_new_task_takes_a_date_deadline_and_a_target_suggestion(
    service: ActionService,
) -> None:
    created = await service.create(edit(), deadline=DeadlineChange.to(FRIDAY))

    assert deadline(created) == (
        Origin.OWNER,
        DeadlinePrecision.DATE,
        FRIDAY,
        None,
        "America/Toronto",
        None,  # A deadline the owner set has no phrase.
    )
    assert (created.suggested_target_date, created.target_reason) == (
        date(2026, 10, 1),
        TargetReason.WORKING_DAY_BEFORE,
    )
    assert created.target_date is None  # The target date stays the owner's choice.
    assert created.stated_deadline is None
    assert created.owner_deadline
    assert [event.kind for event in created.activity] == [ActionActivityKind.CREATED]


async def test_a_deadline_with_a_time_is_an_instant_in_the_owner_s_zone(
    service: ActionService,
) -> None:
    created = await service.create(edit(), deadline=DeadlineChange.to(FRIDAY, time(17, 0, 42)))

    assert deadline(created) == (
        Origin.OWNER,
        DeadlinePrecision.DATETIME,
        FRIDAY,
        datetime(2026, 10, 2, 21, 0, tzinfo=UTC),  # 17:00 EDT, whole minutes.
        "America/Toronto",
        None,
    )
    assert created.due_at_utc() == datetime(2026, 10, 2, 21, 0, tzinfo=UTC)


async def test_a_deadline_on_today_has_today_as_its_suggested_target(
    service: ActionService,
) -> None:
    created = await service.create(edit(), deadline=DeadlineChange.to(date(2026, 9, 28)))

    assert (created.suggested_target_date, created.target_reason) == (
        date(2026, 9, 28),
        TargetReason.ON_DEADLINE,
    )


async def test_a_new_task_has_no_email_deadline_to_go_back_to(service: ActionService) -> None:
    with pytest.raises(ActionConflictError, match="no deadline from an email"):
        await service.create(edit(), deadline=DeadlineChange.from_email())
    cleared = await service.create(edit(), deadline=DeadlineChange.cleared())

    assert deadline(cleared)[:2] == (None, DeadlinePrecision.NONE)


async def test_an_accepted_email_deadline_is_the_email_s(
    session: AsyncSession, service: ActionService
) -> None:
    action = await accepted(session, service)

    assert deadline(action)[:3] == (Origin.EMAIL, DeadlinePrecision.DATE, FRIDAY)
    assert not action.owner_deadline
    assert action.stated_deadline is None


async def test_replacing_the_email_s_deadline_keeps_it_and_the_owner_s_target(
    session: AsyncSession, service: ActionService
) -> None:
    action = await accepted(session, service)

    moved = await service.save(
        action.public_id, 1, edit(action), deadline=DeadlineChange.to(MONDAY)
    )

    assert deadline(moved)[:3] == (Origin.OWNER, DeadlinePrecision.DATE, MONDAY)
    assert moved.stated_deadline is not None
    assert (
        moved.stated_deadline.deadline_text,
        moved.stated_deadline.deadline_precision,
        moved.stated_deadline.deadline_date,
    ) == ("by Friday", DeadlinePrecision.DATE, FRIDAY)
    # The suggestion follows the new deadline; the target date the owner had stays.
    assert (moved.suggested_target_date, moved.target_date) == (FRIDAY, date(2026, 10, 1))
    assert moved.revision == 2
    assert moved.activity[-1].kind is ActionActivityKind.EDITED


async def test_changing_the_owner_s_deadline_again_keeps_the_email_s(
    session: AsyncSession, service: ActionService
) -> None:
    action = await accepted(session, service)
    moved = await service.save(
        action.public_id, 1, edit(action), deadline=DeadlineChange.to(MONDAY)
    )

    again = await service.save(
        moved.public_id, 2, edit(moved), deadline=DeadlineChange.to(date(2026, 10, 9))
    )

    assert deadline(again)[:3] == (Origin.OWNER, DeadlinePrecision.DATE, date(2026, 10, 9))
    assert again.stated_deadline == moved.stated_deadline


async def test_going_back_to_the_email_s_deadline(
    session: AsyncSession, service: ActionService
) -> None:
    action = await accepted(session, service)
    moved = await service.save(
        action.public_id, 1, edit(action), deadline=DeadlineChange.to(MONDAY)
    )

    back = await service.save(moved.public_id, 2, edit(moved), deadline=DeadlineChange.from_email())

    assert deadline(back) == deadline(action)
    assert (back.suggested_target_date, back.target_reason) == (
        date(2026, 10, 1),
        TargetReason.WORKING_DAY_BEFORE,
    )
    # Going back when the deadline is the email's already changes nothing.
    same = await service.save(back.public_id, 3, edit(back), deadline=DeadlineChange.from_email())
    assert deadline(same) == deadline(action)


async def test_clearing_an_email_s_deadline_keeps_it_to_go_back_to(
    session: AsyncSession, service: ActionService
) -> None:
    action = await accepted(session, service)

    cleared = await service.save(
        action.public_id, 1, edit(action), deadline=DeadlineChange.cleared()
    )

    assert deadline(cleared) == (None, DeadlinePrecision.NONE, None, None, None, None)
    assert (cleared.suggested_target_date, cleared.target_reason) == (None, None)
    assert cleared.target_date == date(2026, 10, 1)
    assert cleared.stated_deadline is not None
    assert cleared.stated_deadline.deadline_date == FRIDAY
    assert not cleared.is_overdue(datetime(2026, 12, 1, tzinfo=UTC))
    back = await service.save(
        cleared.public_id, 2, edit(cleared), deadline=DeadlineChange.from_email()
    )
    assert deadline(back) == deadline(action)
    # Clearing twice clears nothing more.
    none = await service.save(back.public_id, 3, edit(back), deadline=DeadlineChange.cleared())
    again = await service.save(none.public_id, 4, edit(none), deadline=DeadlineChange.cleared())
    assert again.stated_deadline == cleared.stated_deadline


async def test_clearing_an_email_s_words_keeps_them_to_go_back_to(
    session: AsyncSession, service: ActionService
) -> None:
    words = suggestion(
        0,
        "Call the client",
        deadline_text="ASAP",
        deadline_precision=DeadlinePrecision.UNRESOLVED,
        evidence="Please call the client ASAP",
    )
    action = await service.accept((await seed(session, (words,))).suggestion_ids[0])

    cleared = await service.save(
        action.public_id, 1, edit(action), deadline=DeadlineChange.cleared()
    )

    assert deadline(cleared) == (None, DeadlinePrecision.NONE, None, None, None, None)
    assert cleared.stated_deadline is not None
    assert cleared.stated_deadline.deadline_text == "ASAP"
    back = await service.save(
        cleared.public_id, 2, edit(cleared), deadline=DeadlineChange.from_email()
    )
    assert deadline(back) == (Origin.EMAIL, DeadlinePrecision.UNRESOLVED, None, None, None, "ASAP")
    assert (back.suggested_target_date, back.target_reason) == (None, None)


async def test_clearing_the_owner_s_deadline_keeps_no_copy(service: ActionService) -> None:
    created = await service.create(edit(), deadline=DeadlineChange.to(FRIDAY))

    cleared = await service.save(
        created.public_id, 1, edit(created), deadline=DeadlineChange.cleared()
    )

    assert deadline(cleared)[:2] == (None, DeadlinePrecision.NONE)
    assert cleared.stated_deadline is None


async def test_the_email_s_own_day_changes_nothing(
    session: AsyncSession, service: ActionService
) -> None:
    action = await accepted(session, service)

    saved = await service.save(
        action.public_id, 1, edit(action), deadline=DeadlineChange.to(FRIDAY)
    )

    assert deadline(saved) == deadline(action)
    assert saved.stated_deadline is None


async def test_saving_without_a_deadline_change_keeps_the_deadline(
    service: ActionService,
) -> None:
    created = await service.create(edit(), deadline=DeadlineChange.to(FRIDAY, time(9, 30)))

    saved = await service.save(created.public_id, 1, edit(created, title="Plan next week"))

    assert deadline(saved) == deadline(created)
    assert saved.title == "Plan next week"


async def test_a_stale_deadline_change_is_refused_and_changes_nothing(
    session: AsyncSession, service: ActionService
) -> None:
    action = await accepted(session, service)
    await service.save(action.public_id, 1, edit(action, notes="Ask Sam"))

    with pytest.raises(ActionConflictError):
        await service.save(action.public_id, 1, edit(action), deadline=DeadlineChange.to(MONDAY))

    current = await service.get(action.public_id)
    assert deadline(current) == deadline(action)
    assert current.revision == 2


async def test_an_owner_deadline_survives_a_restart(
    session: AsyncSession, service: ActionService
) -> None:
    action = await accepted(session, service)
    moved = await service.save(
        action.public_id, 1, edit(action), deadline=DeadlineChange.to(MONDAY, time(12))
    )

    reloaded = await ActionService(session).get(moved.public_id)

    assert reloaded == moved
    assert reloaded.is_overdue(datetime(2026, 10, 5, 16, tzinfo=UTC))
    assert not reloaded.is_overdue(datetime(2026, 10, 5, 15, 59, tzinfo=UTC))


async def test_the_writing_export_says_who_set_each_deadline(tmp_path: Path) -> None:
    path = tmp_path / "deadlines.sqlite3"
    await asyncio.to_thread(upgrade_database, path)
    database = Database.from_path(path)
    async with database.session() as session:
        service = ActionService(session, clock=Clock(START), id_factory=Ids(), zone=TORONTO)
        action = await accepted(session, service)
        await service.save(action.public_id, 1, edit(action), deadline=DeadlineChange.to(MONDAY))
    exported = tmp_path / "writing.json"

    await export_writing(database, exported)
    await database.dispose()

    (record,) = json.loads(exported.read_text(encoding="utf-8"))["records"]["actions"]
    assert (record["deadline_origin"], record["deadline_date"]) == ("owner", "2026-10-05")
    assert record["deadline_text"] is None
    assert (record["stated_deadline_text"], record["stated_deadline_date"]) == (
        "by Friday",
        "2026-10-02",
    )


def test_a_time_a_clock_change_skips_is_read_as_a_clock_shows_it() -> None:
    # 02:30 doesn't exist in Toronto on 8 March 2026: clocks go from 02:00 to 03:00.
    found = owner_deadline(date(2026, 3, 8), time(2, 30), TORONTO)

    assert (found.date, found.at_utc, found.text) == (
        date(2026, 3, 8),
        datetime(2026, 3, 8, 7, 30, tzinfo=UTC),  # 03:30 EDT.
        None,
    )


def test_a_repeated_time_is_its_first() -> None:
    # 01:30 comes twice in Toronto on 1 November 2026; the first is still daylight time.
    found = owner_deadline(date(2026, 11, 1), time(1, 30), ZoneInfo("America/Toronto"))

    assert found.at_utc == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)


@pytest.mark.parametrize("day", [date(1999, 12, 31), date(2100, 1, 1), date(9999, 12, 31)])
def test_a_deadline_outside_2000_to_2099_is_refused(day: date) -> None:
    with pytest.raises(ValidationError, match=DEADLINE_OUT_OF_RANGE):
        DeadlineChange.to(day)

    assert DeadlineChange.to(date(2099, 12, 31)).due_date == date(2099, 12, 31)


def unnamed_toronto() -> ZoneInfo:
    """Toronto's rules under no IANA name, as tzlocal returns a zone it found only as a file."""
    source = importlib.resources.files("tzdata.zoneinfo") / "America" / "Toronto"
    with source.open("rb") as handle:
        return ZoneInfo.from_file(handle, key="local")


def test_a_zone_without_an_iana_name_is_stored_as_utc() -> None:
    zone = unnamed_toronto()

    day = owner_deadline(FRIDAY, None, zone)
    moment = owner_deadline(FRIDAY, time(21, 30), zone)

    assert (day.date, day.timezone) == (FRIDAY, "UTC")
    # 21:30 in Toronto is 01:30 UTC on Saturday: the instant is kept, dated in UTC.
    assert (moment.at_utc, moment.date, moment.timezone) == (
        datetime(2026, 10, 3, 1, 30, tzinfo=UTC),
        date(2026, 10, 3),
        "UTC",
    )


async def test_a_deadline_set_in_an_unnamed_zone_loads_and_is_suggested_locally(
    session: AsyncSession,
) -> None:
    service = ActionService(session, clock=Clock(START), id_factory=Ids(), zone=unnamed_toronto())

    created = await service.create(edit(), deadline=DeadlineChange.to(FRIDAY, time(21, 30)))

    assert (created.deadline_timezone, created.deadline_at_utc) == (
        "UTC",
        datetime(2026, 10, 3, 1, 30, tzinfo=UTC),
    )
    # Due on Friday where the owner is, so Thursday is the working day before.
    assert created.suggested_target_date == date(2026, 10, 1)
    assert await ActionService(session).get(created.public_id) == created


def test_drafting_sends_the_owner_s_deadline_as_a_date_or_a_local_time() -> None:
    owner_day = make_action(
        deadline_precision=DeadlinePrecision.DATE,
        deadline_date=FRIDAY,
        deadline_timezone="America/Toronto",
        deadline_origin=Origin.OWNER,
    )
    owner_moment = make_action(
        deadline_precision=DeadlinePrecision.DATETIME,
        deadline_date=FRIDAY,
        deadline_at_utc=datetime(2026, 10, 2, 21, 0, tzinfo=UTC),
        deadline_timezone="America/Toronto",
        deadline_origin=Origin.OWNER,
    )
    email = make_action(
        deadline_text="by Friday",
        deadline_precision=DeadlinePrecision.DATE,
        deadline_date=FRIDAY,
        deadline_timezone="America/Toronto",
    )

    assert _deadline_words(owner_day, TORONTO) == "2026-10-02"
    assert _deadline_words(owner_moment, TORONTO) == "2026-10-02 17:00"
    assert _deadline_words(email, TORONTO) == "by Friday"
    assert _deadline_words(make_action(), TORONTO) is None
