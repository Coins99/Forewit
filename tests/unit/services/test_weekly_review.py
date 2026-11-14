"""The weekly review (P5-1): local records only, totals shared with Insights."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from mailbrief.domain.actions import ActionFilter
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.weekly_review import WeeklyReviewRecord, week_dates, week_start
from mailbrief.services.actions import ActionService
from mailbrief.services.day_plans import DayPlanService
from mailbrief.services.metrics import MetricService
from mailbrief.services.weekly_review import (
    WeeklyReviewConflictError,
    WeeklyReviewService,
    done_query,
)
from mailbrief.storage.database import Database
from tests.factories import Clock
from tests.unit.services.test_recurring_tasks import DAILY, task

MONDAY = date(2026, 11, 9)
NOW = datetime(2026, 11, 11, 15, tzinfo=UTC)  # Wednesday, 10:00 in Toronto.
ZONE = ZoneInfo("America/Toronto")
NOTE_ID = "00000000-0000-4000-8000-000000000001"


def review(database: Database, clock: Clock) -> WeeklyReviewService:
    return WeeklyReviewService(database, google_on=False, clock=clock)


def test_weeks_start_on_monday() -> None:
    assert week_start(date(2026, 11, 15)) == MONDAY
    assert week_start(MONDAY) == MONDAY
    assert week_dates(MONDAY)[-1] == date(2026, 11, 15)
    with pytest.raises(ValueError, match="Monday"):
        WeeklyReviewRecord(week_start=date(2026, 11, 10), revision=1)


async def test_done_matches_insights_and_skips_are_apart(database: Database) -> None:
    clock = Clock(NOW)
    async with database.session() as session:
        actions = ActionService(session, clock=clock, zone=ZONE)
        kept = await actions.create(task(title="Kept done"))
        kept = await actions.complete(kept.public_id, kept.revision)
        back = await actions.create(task(title="Reopened"))
        back = await actions.complete(back.public_id, back.revision)
        back = await actions.undo_status(back.public_id, back.revision)
        series = await actions.create(task(title="Daily"), (), DAILY)
        await actions.skip(series.public_id, series.revision)
        clock.advance(days=7)
        late = await actions.create(task(title="Next week"))
        await actions.complete(late.public_id, late.revision)
    clock.now = NOW

    shown = await review(database, clock).load(MONDAY, ZONE)

    assert [row.title for row in shown.done] == ["Kept done"]
    assert shown.done[0].route.view is ActionFilter.COMPLETED
    assert shown.done[0].detail.startswith("Done 2026-11-11")
    assert shown.done_total == 1
    assert [row.title for row in shown.skipped] == ["Daily"]
    assert shown.partial_tasks == 0
    insights = await MetricService(database, google_on=False, clock=clock).query(
        done_query(MONDAY, ZONE)
    )
    assert insights.total == shown.done_total
    assert {row.source_id for row in insights.contributions} == {
        row.public_id for row in shown.done
    }


async def test_still_open_waiting_focus_and_next_week(database: Database) -> None:
    clock = Clock(NOW)
    async with database.session() as session:
        actions = ActionService(session, clock=clock, zone=ZONE)
        planned = await actions.create(task(title="Planned", target_date=date(2026, 11, 10)))
        await actions.create(task(title="Later", target_date=date(2026, 11, 18)))
        focused = await actions.create(task(title="Focused", target_date=None))
        await actions.create(task(title="Undated", target_date=None))
        await actions.create(
            task(
                title="Waiting",
                target_date=None,
                ownership=ActionOwnership.WAITING_FOR,
                person="Sam",
            )
        )
        finished = await actions.create(task(title="Finished", target_date=date(2026, 11, 10)))
        await actions.complete(finished.public_id, finished.revision)
        plans = DayPlanService(session, clock=clock)
        day = date(2026, 11, 12)
        await plans.select(day, ZONE, focused.public_id, (await plans.load(day)).revision)
    clock.advance(days=3)

    shown = await review(database, clock).load(MONDAY, ZONE)

    assert [(row.title, row.detail) for row in shown.still_open] == [
        ("Planned", "Planned 2026-11-10"),
        ("Focused", "In focus 2026-11-12"),
    ]
    assert shown.still_open[0].public_id == planned.public_id
    assert [(row.title, row.detail) for row in shown.waiting] == [("Waiting", "Waiting 3 days")]
    assert shown.waiting[0].route.view is ActionFilter.WAITING
    assert [item.title for item in shown.next_week.items] == ["Later"]
    assert tuple(found.day for found in shown.next_days) == week_dates(MONDAY + timedelta(days=7))
    assert shown.week_end == date(2026, 11, 15)
    assert shown.next_start == date(2026, 11, 16)
    assert shown.record is None


async def test_review_record_needs_the_revision_it_read(database: Database) -> None:
    clock = Clock(NOW)
    service = review(database, clock)
    assert await service.record(MONDAY) is None
    with pytest.raises(WeeklyReviewConflictError):
        await service.mark_reviewed(MONDAY, 1)

    marked = await service.mark_reviewed(MONDAY, None)
    assert marked.revision == 1
    assert marked.reviewed and marked.reviewed_at_utc == NOW
    with pytest.raises(WeeklyReviewConflictError):
        await service.mark_reviewed(MONDAY, None)

    linked = await service.attach_reflection(MONDAY, 1, NOTE_ID)
    assert (linked.revision, linked.reflection_note_public_id) == (2, NOTE_ID)
    same = await service.attach_reflection(MONDAY, 2, NOTE_ID)
    assert same.revision == 2  # Nothing changed, so no new revision.
    clock.advance(hours=1)
    cleared = await service.mark_reviewed(MONDAY, 2, reviewed=False)
    assert (cleared.revision, cleared.reviewed) == (3, False)
    assert cleared.reflection_note_public_id == NOTE_ID
    assert await service.record(MONDAY) == cleared
    assert (await service.load(MONDAY, ZONE)).record == cleared

    with pytest.raises(ValueError, match="Monday"):
        await service.mark_reviewed(MONDAY + timedelta(days=1), None)
    with pytest.raises(ValueError, match="Monday"):
        await service.load(MONDAY + timedelta(days=1), ZONE)
    with pytest.raises(ValueError, match="saved note"):
        await service.attach_reflection(MONDAY, 3, "short")
