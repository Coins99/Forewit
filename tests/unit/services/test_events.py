"""The owner's calendar: events, revisions, ranges and the open actions' dates."""

from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.events import ActionMarker, EventEdit, MarkerKind
from mailbrief.services.events import (
    EventConflictError,
    EventNotFoundError,
    EventService,
    markers_for,
)
from mailbrief.storage.tables import ActionTable, CalendarEventTable
from tests.factories import Clock, Ids

NOW = datetime(2026, 10, 11, 16, 0, tzinfo=UTC)
TORONTO = ZoneInfo("America/Toronto")
MONDAY = date(2026, 10, 12)
SUNDAY = date(2026, 10, 18)


@pytest.fixture
def clock() -> Clock:
    return Clock(NOW)


@pytest.fixture
def ids() -> Ids:
    return Ids()


@pytest.fixture
def service(session: AsyncSession, clock: Clock, ids: Ids) -> EventService:
    return EventService(session, clock=clock, id_factory=ids)


def local(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=TORONTO)


def timed(start: datetime, end: datetime, title: str = "Dentist", **extra: Any) -> EventEdit:
    return EventEdit(
        title=title,
        all_day=False,
        starts_at_utc=start,
        ends_at_utc=end,
        time_zone="America/Toronto",
        **extra,
    )


def all_day(first: date, last: date, title: str = "Conference") -> EventEdit:
    return EventEdit(
        title=title, all_day=True, start_date=first, last_date=last, time_zone="America/Toronto"
    )


async def count_rows(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(CalendarEventTable)) or 0)


async def test_create_get_and_save_are_revisions(
    service: EventService, clock: Clock, session: AsyncSession
) -> None:
    created = await service.create(timed(local(MONDAY, 9), local(MONDAY, 10), notes="Form"))
    assert created.revision == 1
    assert created.public_id == "00000000-0000-4000-8000-000000000001"
    assert created.created_at_utc == created.updated_at_utc == NOW
    assert created.notes == "Form"
    assert await service.get(created.public_id) == created

    clock.advance(minutes=5)
    moved = await service.save(
        created.public_id, 1, timed(local(MONDAY, 11), local(MONDAY, 12), title="Dentist (moved)")
    )
    assert moved.revision == 2
    assert moved.updated_at_utc == NOW + timedelta(minutes=5)
    assert moved.created_at_utc == NOW
    assert moved.title == "Dentist (moved)" and moved.notes == ""
    assert moved.starts_at_utc == local(MONDAY, 11)

    # The same event can become all-day, and back.
    whole = await service.save(created.public_id, 2, all_day(MONDAY, MONDAY))
    assert whole.all_day and whole.starts_at_utc is None and whole.start_date == MONDAY
    assert await count_rows(session) == 1


async def test_stale_save_and_delete_change_nothing(service: EventService) -> None:
    created = await service.create(timed(local(MONDAY, 9), local(MONDAY, 10)))
    await service.save(created.public_id, 1, timed(local(MONDAY, 9), local(MONDAY, 11)))
    with pytest.raises(EventConflictError, match="changed since it was loaded"):
        await service.save(created.public_id, 1, timed(local(MONDAY, 15), local(MONDAY, 16)))
    with pytest.raises(EventConflictError):
        await service.delete(created.public_id, 1)
    current = await service.get(created.public_id)
    assert current.revision == 2 and current.ends_at_utc == local(MONDAY, 11)


async def test_delete_hides_and_restore_brings_back(service: EventService, clock: Clock) -> None:
    created = await service.create(timed(local(MONDAY, 9), local(MONDAY, 10)))
    clock.advance(minutes=1)
    await service.delete(created.public_id, 1)
    with pytest.raises(EventNotFoundError, match="not found"):
        await service.get(created.public_id)
    with pytest.raises(EventNotFoundError):
        await service.save(created.public_id, 2, timed(local(MONDAY, 9), local(MONDAY, 10)))
    assert (await service.list_range(MONDAY, SUNDAY, TORONTO)).events == ()

    clock.advance(minutes=1)
    restored = await service.restore(created.public_id)
    assert restored.revision == 3 and restored.updated_at_utc == NOW + timedelta(minutes=2)
    assert [
        event.public_id for event in (await service.list_range(MONDAY, SUNDAY, TORONTO)).events
    ] == [created.public_id]
    # Restoring a live event changes nothing.
    assert await service.restore(created.public_id) == restored
    with pytest.raises(EventNotFoundError):
        await service.restore("00000000-0000-4000-8000-999999999999")


async def test_failed_write_rolls_back(
    service: EventService, ids: Ids, session: AsyncSession
) -> None:
    await service.create(timed(local(MONDAY, 9), local(MONDAY, 10)))
    ids.repeat = True  # The next event gets the same public ID, which the table refuses.
    with pytest.raises(IntegrityError):
        await service.create(timed(local(MONDAY, 11), local(MONDAY, 12)))
    ids.repeat = False
    assert await count_rows(session) == 1
    # The session still works after the rollback.
    assert (await service.create(timed(local(MONDAY, 13), local(MONDAY, 14)))).revision == 1


async def test_range_holds_events_touching_its_local_days(service: EventService) -> None:
    before = await service.create(
        timed(local(MONDAY, 0) - timedelta(hours=1), local(MONDAY, 0), "Ends at the start")
    )
    overnight = await service.create(
        timed(local(SUNDAY, 23), local(SUNDAY, 23) + timedelta(hours=2), "Overnight")
    )
    after = await service.create(
        timed(
            local(SUNDAY + timedelta(days=1), 0),
            local(SUNDAY + timedelta(days=1), 1),
            "Starts after",
        )
    )
    inside = await service.create(timed(local(MONDAY, 9), local(MONDAY, 10), "Standup"))
    week_long = await service.create(all_day(MONDAY - timedelta(days=3), MONDAY, "Ends Monday"))
    outside_day = await service.create(
        all_day(SUNDAY + timedelta(days=1), SUNDAY + timedelta(days=2), "Next week")
    )

    shown = await service.list_range(MONDAY, SUNDAY, TORONTO)

    assert shown.first_day == MONDAY and shown.last_day == SUNDAY
    assert [event.title for event in shown.events] == ["Ends Monday", "Standup", "Overnight"]
    hidden = {before.public_id, after.public_id, outside_day.public_id}
    assert hidden.isdisjoint(event.public_id for event in shown.events)
    assert {inside.public_id, overnight.public_id, week_long.public_id} == {
        event.public_id for event in shown.events
    }


async def test_range_follows_the_zone_across_a_dst_change(service: EventService) -> None:
    # Toronto falls back on Sunday 1 November 2026: that local day lasts 25 hours.
    sunday = date(2026, 11, 1)
    late = await service.create(timed(local(sunday, 23, 30), local(sunday, 23, 45), "Late"))
    next_day = await service.create(
        timed(local(sunday + timedelta(days=1), 0, 10), local(sunday + timedelta(days=1), 0, 20))
    )
    shown = await service.list_range(sunday, sunday, TORONTO)
    assert [event.public_id for event in shown.events] == [late.public_id]
    assert next_day.public_id not in {event.public_id for event in shown.events}


@pytest.mark.parametrize(
    ("first", "last"), [(SUNDAY, MONDAY), (MONDAY, MONDAY + timedelta(days=42))]
)
async def test_range_bounds(service: EventService, first: date, last: date) -> None:
    with pytest.raises(ValueError, match="1 to 42 days"):
        await service.list_range(first, last, TORONTO)


async def test_longest_range_is_allowed(service: EventService) -> None:
    shown = await service.list_range(MONDAY, MONDAY + timedelta(days=41), TORONTO)
    assert shown.events == () and shown.markers == ()


def action_row(number: int, **fields: Any) -> ActionTable:
    values: dict[str, Any] = {
        "public_id": f"11111111-0000-4000-8000-{number:012d}",
        "title": f"Action {number}",
        "ownership": "mine",
        "status": "open",
        "deadline_precision": "none",
        "notes": "",
        "created_at_utc": NOW,
        "updated_at_utc": NOW,
        "revision": 1,
    }
    values |= fields
    if values["deadline_precision"] != "none":
        values.setdefault("deadline_origin", "email")
    if values["ownership"] == "waiting_for":
        values.setdefault("waiting_since_utc", values["created_at_utc"])
    return ActionTable(**values)


async def test_range_shows_open_actions_dates_as_markers(
    service: EventService, session: AsyncSession
) -> None:
    session.add_all(
        [
            action_row(1, title="Send the deck", target_date=date(2026, 10, 14)),
            action_row(
                2,
                title="Pay the invoice",
                deadline_precision="date",
                deadline_date=date(2026, 10, 15),
                deadline_text="by Thursday",
            ),
            # 03:30 UTC on Friday is 23:30 on Thursday in Toronto.
            action_row(
                3,
                title="Reply to Priya",
                ownership="waiting_for",
                deadline_precision="datetime",
                deadline_at_utc=datetime(2026, 10, 16, 3, 30, tzinfo=UTC),
                deadline_date=date(2026, 10, 15),
                deadline_text="Thursday 11:30 pm",
                target_date=date(2026, 10, 15),
            ),
            action_row(4, title="Done already", status="completed", target_date=date(2026, 10, 14)),
            action_row(5, title="Deleted", target_date=date(2026, 10, 14), deleted_at_utc=NOW),
            action_row(
                6,
                title="Only words",
                deadline_precision="unresolved",
                deadline_text="soon",
                target_date=date(2026, 10, 30),
            ),
            action_row(7, title="Next week", target_date=date(2026, 10, 19)),
        ]
    )
    await session.commit()

    markers = (await service.list_range(MONDAY, SUNDAY, TORONTO)).markers

    assert [(m.day, m.kind, m.action_title, m.waiting) for m in markers] == [
        (date(2026, 10, 14), MarkerKind.TARGET, "Send the deck", False),
        (date(2026, 10, 15), MarkerKind.DEADLINE, "Pay the invoice", False),
        (date(2026, 10, 15), MarkerKind.DEADLINE, "Reply to Priya", True),
        (date(2026, 10, 15), MarkerKind.TARGET, "Reply to Priya", True),
    ]
    assert markers[1].due_at_utc is None
    assert markers[2].due_at_utc == datetime(2026, 10, 16, 3, 30, tzinfo=UTC)
    # Reading markers changes no action.
    assert await session.scalar(select(func.max(ActionTable.revision))) == 1


def test_markers_for_a_zone_where_the_instant_is_the_next_day() -> None:
    row = action_row(
        8,
        deadline_precision="datetime",
        deadline_at_utc=datetime(2026, 10, 16, 3, 30, tzinfo=UTC),
        deadline_date=date(2026, 10, 15),
    )
    tokyo = markers_for([row], MONDAY, SUNDAY, ZoneInfo("Asia/Tokyo"))
    assert [(m.day, m.kind) for m in tokyo] == [(date(2026, 10, 16), MarkerKind.DEADLINE)]
    assert markers_for([row], MONDAY, date(2026, 10, 15), ZoneInfo("Asia/Tokyo")) == ()
    assert isinstance(tokyo[0], ActionMarker)
