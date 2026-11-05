"""Offline capture review, dated plans and known calendar workload."""

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import event
from sqlalchemy.engine import Connection, ExecutionContext
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import Action
from mailbrief.domain.drafts import Draft, DraftEdit
from mailbrief.domain.events import ActionMarker, CalendarRange, MarkerKind
from mailbrief.domain.google_calendar import GoogleCoverage, GoogleEvent
from mailbrief.services.actions import ActionService
from mailbrief.services.capture import CaptureConflictError, CaptureService
from mailbrief.services.day_plans import DayPlanConflictError, DayPlanService
from mailbrief.services.drafts import DraftService, NoteCreateConflictError
from mailbrief.services.events import EventService
from mailbrief.services.today import TodayService
from mailbrief.services.upcoming import UpcomingService, overview
from tests.unit.services.test_events import all_day, timed
from tests.unit.services.test_manual_tasks import EDIT

DAY = date(2026, 11, 3)
ZONE = ZoneInfo("America/Toronto")
NOW = datetime(2026, 11, 3, 16, tzinfo=UTC)


async def test_today_does_not_hydrate_task_edit_timelines(session: AsyncSession) -> None:
    task = await ActionService(session).create(EDIT.model_copy(update={"target_date": DAY}))
    statements: list[str] = []

    def record(
        connection: Connection,
        cursor: object,
        statement: str,
        parameters: object,
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    engine = session.get_bind()
    event.listen(engine, "before_cursor_execute", record)
    try:
        shown = await TodayService(session).summary(DAY, ZONE, NOW)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert not any("action_activity" in statement for statement in statements)
    assert shown.attention[0].action.public_id == task.public_id
    assert shown.attention[0].action.activity == ()
    assert (await ActionService(session).get(task.public_id)).activity


async def test_capture_inbox_is_explicit_and_processing_is_revisioned(
    session: AsyncSession,
) -> None:
    actions, notes, captures = (
        ActionService(session),
        DraftService(session),
        CaptureService(session),
    )
    ordinary = await actions.create(EDIT)
    kept = await actions.create(EDIT, capture_pending=False)
    task = await actions.create(EDIT, capture_pending=True)
    note = await notes.create_note(
        DraftEdit(body="Owner text"), command_id=str(uuid4()), capture_pending=True
    )
    assert {record.public_id for record in await captures.inbox()} == {
        task.public_id,
        note.public_id,
    }
    assert ordinary.capture_origin == "legacy"
    assert kept.capture_origin == "quick_capture" and kept.capture_processed_at_utc
    versions = await notes.versions(note.public_id)
    processed = await captures.keep("note", note.public_id, note.revision)
    assert processed.revision == note.revision + 1
    assert await notes.versions(note.public_id) == versions
    assert await captures.keep("note", processed.public_id, processed.revision) == processed
    with pytest.raises(CaptureConflictError):
        await captures.keep("note", note.public_id, note.revision)
    history = await actions.history(task.public_id)
    processed_task = await captures.keep("task", task.public_id, task.revision)
    assert isinstance(processed_task, Action)
    assert processed_task.revision == task.revision + 1
    assert len((await actions.history(task.public_id)).effective) == len(history.effective) + 1
    assert await captures.inbox() == ()
    assert processed_task.status == task.status
    await actions.delete(ordinary.public_id, ordinary.revision)
    for kind, key in (("task", ordinary.public_id), ("note", str(uuid4()))):
        with pytest.raises(CaptureConflictError):
            await captures.keep(kind, key, 1)  # type: ignore[arg-type]


async def test_capture_retry_checks_the_processing_payload(session: AsyncSession) -> None:
    service = DraftService(session)
    command = str(uuid4())
    original = await service.create_note(
        DraftEdit(body="Text"), command_id=command, capture_pending=True
    )
    assert (
        await service.create_note(DraftEdit(body="Text"), command_id=command, capture_pending=True)
        == original
    )
    with pytest.raises(NoteCreateConflictError):
        await service.create_note(DraftEdit(body="Text"), command_id=command, capture_pending=False)
    await CaptureService(session).keep("note", original.public_id, original.revision)
    replay = await service.create_note(
        DraftEdit(body="Text"), command_id=command, capture_pending=True
    )
    assert replay.revision == 2 and replay.capture_processed_at_utc


@pytest.mark.parametrize("newer_kind", ["task", "note"])
async def test_capture_inbox_keeps_each_type_before_the_display_limit(
    session: AsyncSession, newer_kind: str
) -> None:
    actions = ActionService(session, clock=lambda: NOW)
    notes = DraftService(session, clock=lambda: NOW)
    older: Action | Draft
    if newer_kind == "task":
        older = await notes.create_note(
            DraftEdit(body="Older pending note"), command_id=str(uuid4()), capture_pending=True
        )
    else:
        older = await actions.create(EDIT, capture_pending=True)
    later = NOW + timedelta(minutes=1)
    actions = ActionService(session, clock=lambda: later)
    notes = DraftService(session, clock=lambda: later)
    for _ in range(101):
        if newer_kind == "task":
            await actions.create(EDIT, capture_pending=True)
        else:
            await notes.create_note(
                DraftEdit(body="Newer pending note"), command_id=str(uuid4()), capture_pending=True
            )

    records = await CaptureService(session).inbox()
    assert len(records) == 101  # At most 100 of the newer type, plus the older other type.
    assert records[-1].public_id == older.public_id
    assert all(record.updated_at_utc == later for record in records[:-1])


async def test_carry_and_move_deduplicate_and_preserve_task_dates(session: AsyncSession) -> None:
    actions, plans = ActionService(session), DayPlanService(session)
    task = await actions.create(EDIT)
    source = await plans.select(DAY, ZONE, task.public_id, 0)
    destination_day = DAY + timedelta(days=1)
    preview = await plans.preview_transfer(DAY, destination_day)
    destination = await plans.transfer(preview, ZONE)
    assert destination.items[0].task_public_id == task.public_id
    assert await plans.load(DAY) == source
    repeated = await plans.preview_transfer(DAY, destination_day)
    assert await plans.transfer(repeated, ZONE) == destination
    move = await plans.preview_transfer(DAY, destination_day, move=True)
    assert await plans.transfer(move, ZONE) == destination
    assert not (await plans.load(DAY)).items
    assert (await plans.load(DAY)).revision == source.revision + 1
    assert await actions.get(task.public_id) == task
    with pytest.raises(DayPlanConflictError):
        await plans.transfer(move, ZONE)
    with pytest.raises(DayPlanConflictError):
        await plans.preview_transfer(DAY, DAY)


async def test_plan_preview_refuses_task_edits_and_skips_unavailable(session: AsyncSession) -> None:
    actions, plans = ActionService(session), DayPlanService(session)
    task = await actions.create(EDIT)
    source = await plans.select(DAY, ZONE, task.public_id, 0)
    preview = await plans.preview_transfer(DAY, DAY + timedelta(days=1), move=True)
    await actions.complete(task.public_id, task.revision)
    with pytest.raises(DayPlanConflictError):
        await plans.transfer(preview, ZONE)
    assert (await plans.load(preview.destination.day)).revision == 0
    assert (await plans.load(DAY)).revision == source.revision
    revised = await plans.preview_transfer(DAY, preview.destination.day)
    assert revised.skipped_ids == (task.public_id,) and not revised.task_ids
    assert (await plans.transfer(revised, ZONE)).revision == 0


async def test_transfer_rolls_back_both_plans_on_commit_failure(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    actions, plans = ActionService(session), DayPlanService(session)
    task = await actions.create(EDIT)
    source = await plans.select(DAY, ZONE, task.public_id, 0)
    preview = await plans.preview_transfer(DAY, DAY + timedelta(days=1), move=True)
    with monkeypatch.context() as changes:
        changes.setattr(
            session, "commit", AsyncMock(side_effect=RuntimeError("Synthetic disk failure"))
        )
        with pytest.raises(RuntimeError):
            await plans.transfer(preview, ZONE)
    assert await plans.load(DAY) == source
    assert (await plans.load(preview.destination.day)).revision == 0


@pytest.mark.parametrize(("day", "hours"), [(date(2026, 3, 8), 23), (date(2026, 11, 1), 25)])
async def test_upcoming_clips_instant_intervals_through_dst(
    session: AsyncSession, day: date, hours: int
) -> None:
    events = EventService(session)
    left = datetime(day.year, day.month, day.day, tzinfo=ZONE).astimezone(UTC)
    right = datetime.combine(day + timedelta(days=1), datetime.min.time(), ZONE).astimezone(UTC)
    long = await events.create(timed(left - timedelta(hours=1), right + timedelta(hours=1)))
    overlap = await events.create(timed(left, left + timedelta(hours=2)))
    whole = await events.create(all_day(day, day))
    calendar = CalendarRange(
        first_day=day, last_day=day, events=(long, overlap, whole, long), markers=()
    )
    known = overview(calendar, ZONE)
    assert known.timed_minutes == hours * 60
    assert known.all_day_events == 1 and len(known.items) == 3
    assert not known.google_complete and known.google_synced_at_utc is None


async def test_upcoming_deduplicates_task_reasons_and_keeps_distinct_google_identity(
    session: AsyncSession,
) -> None:
    events = EventService(session)
    local = await events.create(timed(NOW, NOW + timedelta(hours=1), title="Same title"))
    foreign = GoogleEvent(
        calendar_id="work",
        calendar_name="Work",
        event_id="meeting",
        title="Same title",
        all_day=False,
        starts_at_utc=NOW,
        ends_at_utc=NOW + timedelta(hours=2),
    )
    task_id = str(uuid4())
    markers = tuple(
        ActionMarker(
            kind=kind, day=DAY, action_public_id=task_id, action_title="Do it", waiting=True
        )
        for kind in MarkerKind
    )
    calendar = CalendarRange(
        first_day=DAY,
        last_day=DAY + timedelta(days=6),
        events=(local,),
        google_events=(foreign, foreign),
        markers=markers,
        google_coverage=GoogleCoverage(
            first_day=DAY, last_day=DAY + timedelta(days=6), synced_at_utc=NOW
        ),
    )
    known = overview(calendar, ZONE)
    assert known.timed_minutes == 120 and known.google_complete
    assert len(known.items) == 3
    assert len(next(item for item in known.items if item.identity[0] == "task").reasons) == 2
    assert known == overview(calendar, ZONE)
    assert (await UpcomingService(session).load(DAY, ZONE)).calendar.last_day == DAY + timedelta(
        days=6
    )
    assert (
        await UpcomingService(session).load(DAY, ZONE, days=30)
    ).calendar.last_day == DAY + timedelta(days=29)
    with pytest.raises(ValueError):
        await UpcomingService(session).load(DAY, ZONE, days=31)
