"""The owner's day at a glance: what needs attention, new replies and today's calendar."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import Action, ActionEdit, ActionStatus, ThreadActivity
from mailbrief.domain.analysis import ActionOwnership, DeadlinePrecision
from mailbrief.domain.events import EventEdit
from mailbrief.domain.today import AttentionReason
from mailbrief.services.actions import ActionService
from mailbrief.services.events import EventService
from mailbrief.services.today import (
    TodayService,
    attention_reason,
    due_day,
    needs_attention,
    new_replies,
)
from mailbrief.storage.database import Database
from tests.factories import make_action

ZONE = ZoneInfo("America/Toronto")
TODAY = date(2026, 10, 13)
NOW = datetime(2026, 10, 13, 16, tzinfo=UTC)  # Noon in Toronto.


def action(key: int, **fields: object) -> Action:
    return make_action(
        public_id=f"00000000-0000-4000-8000-{key:012d}", title=f"Task {key}", **fields
    )


def timed_deadline(at: datetime) -> dict[str, object]:
    return {
        "deadline_text": "by then",
        "deadline_precision": DeadlinePrecision.DATETIME,
        "deadline_date": at.astimezone(ZONE).date(),
        "deadline_at_utc": at,
        "deadline_timezone": ZONE.key,
    }


def dated_deadline(day: date) -> dict[str, object]:
    return {
        "deadline_text": "that day",
        "deadline_precision": DeadlinePrecision.DATE,
        "deadline_date": day,
        "deadline_timezone": ZONE.key,
    }


def reason(**fields: object) -> AttentionReason | None:
    return attention_reason(action(1, **fields), today=TODAY, zone=ZONE, now=NOW)


def test_a_passed_deadline_is_overdue_whatever_the_target() -> None:
    assert reason(**timed_deadline(NOW - timedelta(hours=1))) is AttentionReason.OVERDUE
    assert (
        reason(**dated_deadline(TODAY - timedelta(days=1)), target_date=TODAY)
        is AttentionReason.OVERDUE
    )


def test_a_deadline_later_today_is_due_today() -> None:
    assert reason(**timed_deadline(NOW + timedelta(hours=5))) is AttentionReason.DUE_TODAY
    assert reason(**dated_deadline(TODAY)) is AttentionReason.DUE_TODAY  # Due at midnight.


def test_a_timed_deadline_tonight_in_toronto_is_today_even_if_utc_says_tomorrow() -> None:
    late = datetime(2026, 10, 14, 3, tzinfo=UTC)  # 23:00 in Toronto.
    assert reason(**timed_deadline(late)) is AttentionReason.DUE_TODAY


def test_target_dates_today_or_passed_are_planned() -> None:
    assert reason(target_date=TODAY) is AttentionReason.PLANNED_TODAY
    assert reason(target_date=TODAY - timedelta(days=3)) is AttentionReason.PLANNED_EARLIER


def test_nothing_due_or_planned_by_today_needs_no_attention() -> None:
    assert reason() is None
    assert reason(target_date=TODAY + timedelta(days=1)) is None
    assert reason(**dated_deadline(TODAY + timedelta(days=1))) is None
    completed = {"status": ActionStatus.COMPLETED, "completed_at_utc": NOW, "target_date": TODAY}
    assert reason(**completed) is None


def test_due_day_reads_dates_and_times_and_nothing_else() -> None:
    assert due_day(action(1, **dated_deadline(TODAY)), ZONE) == TODAY
    assert due_day(action(1), ZONE) is None
    unresolved = action(1, deadline_text="soon", deadline_precision=DeadlinePrecision.UNRESOLVED)
    assert due_day(unresolved, ZONE) is None


def test_attention_comes_most_urgent_reason_first_and_each_action_once() -> None:
    planned_earlier = action(1, target_date=TODAY - timedelta(days=2))
    planned_today = action(2, target_date=TODAY)
    due_late = action(3, **timed_deadline(NOW + timedelta(hours=6)))
    due_soon = action(4, **timed_deadline(NOW + timedelta(hours=1)))
    overdue = action(5, **dated_deadline(TODAY - timedelta(days=1)))
    nothing = action(6)

    items = needs_attention(
        [planned_earlier, planned_today, due_late, due_soon, overdue, nothing, overdue],
        today=TODAY,
        zone=ZONE,
        now=NOW,
    )

    assert [(item.action.title, item.reason) for item in items] == [
        ("Task 5", AttentionReason.OVERDUE),
        ("Task 4", AttentionReason.DUE_TODAY),
        ("Task 3", AttentionReason.DUE_TODAY),
        ("Task 2", AttentionReason.PLANNED_TODAY),
        ("Task 1", AttentionReason.PLANNED_EARLIER),
    ]


def test_new_replies_are_open_actions_with_unseen_messages_latest_first() -> None:
    def thread(new: int, hours_ago: int | None) -> ThreadActivity:
        latest = None if hours_ago is None else NOW - timedelta(hours=hours_ago)
        return ThreadActivity(
            new_messages=new, latest_at_utc=latest, latest_sender="Priya" if latest else None
        )

    older = action(1, thread=thread(1, 5))
    newer = action(2, thread=thread(2, 1), ownership=ActionOwnership.WAITING_FOR)
    seen = action(4, thread=thread(0, None))
    quiet = action(5)
    done = action(6, thread=thread(3, 0), status=ActionStatus.COMPLETED, completed_at_utc=NOW)

    replies = new_replies([older, newer, seen, quiet, done, newer])

    assert [item.title for item in replies] == ["Task 2", "Task 1"]


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    database = Database("sqlite+aiosqlite:///:memory:")
    await database.create_schema_for_tests()
    async with database.session() as active:
        yield active
    await database.dispose()


async def test_the_service_reads_open_and_waiting_actions_and_todays_calendar(
    session: AsyncSession,
) -> None:
    actions = ActionService(session)
    await actions.create(
        ActionEdit(
            title="Book the room",
            ownership=ActionOwnership.MINE,
            effort=None,
            estimate_minutes=None,
            target_date=TODAY,
            notes="",
        ),
        (),
    )
    await actions.create(
        ActionEdit(
            title="Hear back from Sam",
            ownership=ActionOwnership.WAITING_FOR,
            effort=None,
            estimate_minutes=None,
            target_date=TODAY - timedelta(days=1),
            notes="",
        ),
        (),
    )
    await actions.create(
        ActionEdit(
            title="Someday",
            ownership=ActionOwnership.MINE,
            effort=None,
            estimate_minutes=None,
            target_date=None,
            notes="",
        ),
        (),
    )
    events = EventService(session)
    await events.create(
        EventEdit(
            title="Dentist",
            notes="",
            all_day=False,
            starts_at_utc=datetime(2026, 10, 13, 18, tzinfo=UTC),
            ends_at_utc=datetime(2026, 10, 13, 19, tzinfo=UTC),
            start_date=None,
            last_date=None,
            time_zone=ZONE.key,
        )
    )
    await events.create(
        EventEdit(
            title="Tomorrow",
            notes="",
            all_day=True,
            starts_at_utc=None,
            ends_at_utc=None,
            start_date=TODAY + timedelta(days=1),
            last_date=TODAY + timedelta(days=1),
            time_zone=ZONE.key,
        )
    )

    summary = await TodayService(session).summary(TODAY, ZONE, NOW)

    assert summary.day == TODAY
    assert [(item.action.title, item.reason) for item in summary.attention] == [
        ("Book the room", AttentionReason.PLANNED_TODAY),
        ("Hear back from Sam", AttentionReason.PLANNED_EARLIER),
    ]
    assert summary.replies == ()
    assert [event.title for event in summary.calendar.events] == ["Dentist"]
    assert (summary.calendar.first_day, summary.calendar.last_day) == (TODAY, TODAY)
