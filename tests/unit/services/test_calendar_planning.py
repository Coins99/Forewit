"""AI calendar proposals: what is sent, consent, and the checks every change must pass."""

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.calendar_planning import (
    CalendarPlanningRequest,
    ChangeCandidate,
    PlanningCandidate,
    PlanningOptions,
    PlanningPart,
    PlanningPreview,
    PlanningResponse,
    PlanningStatus,
    ticked_at_first,
)
from mailbrief.domain.drafting import DraftingProblem
from mailbrief.domain.events import ChangeKind, EventEdit, wall_clock
from mailbrief.domain.google_calendar import GoogleCalendarInfo, GoogleEvent
from mailbrief.ports.errors import ProviderRateLimitError
from mailbrief.services.calendar_planning import (
    CALENDAR_SCOPE,
    CalendarPlanningService,
    PreparedPlan,
    overlaps_for,
    validate_plan,
)
from mailbrief.services.events import EventConflictError, EventService
from mailbrief.services.google_calendar import CalendarSyncService
from mailbrief.storage.repositories import OwnerConsentRepository
from mailbrief.storage.tables import ActionTable

TORONTO = ZoneInfo("America/Toronto")
NOW = datetime(2026, 10, 14, 18, 20, tzinfo=UTC)  # Wednesday 14:20 in Toronto.
MONDAY = date(2026, 10, 12)
WEDNESDAY = date(2026, 10, 14)
FRIDAY = date(2026, 10, 16)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return wall_clock(day, time(hour, minute), TORONTO)


def timed(title: str, day: date, start: int, end: int) -> EventEdit:
    return EventEdit(
        title=title,
        all_day=False,
        starts_at_utc=at(day, start),
        ends_at_utc=at(day, end),
        time_zone="America/Toronto",
    )


class Provider:
    provider_name = "groq"
    model_name = "test-model"
    planning_prompt_version = "test-1"
    privacy_notice = "Notice."
    requests_sent = 0

    def __init__(self) -> None:
        self.script: list[PlanningResponse | Exception] = []
        self.requests: list[CalendarPlanningRequest] = []
        self.key = True

    async def credentials_available(self) -> bool:
        return self.key

    async def plan_calendar(self, request: CalendarPlanningRequest) -> PlanningResponse:
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Gate:
    def __init__(self, answer: bool = True) -> None:
        self.answer = answer
        self.previews: list[PlanningPreview] = []

    async def request_planning_consent(self, preview: PlanningPreview) -> bool:
        self.previews.append(preview)
        return self.answer


@pytest.fixture
def provider() -> Provider:
    return Provider()


@pytest.fixture
def service(session: AsyncSession, provider: Provider) -> CalendarPlanningService:
    return CalendarPlanningService(session, provider, clock=lambda: NOW)


def change(**fields: Any) -> ChangeCandidate:
    return ChangeCandidate.model_validate({"op": "update", "event_key": "e1"} | fields)


def answer(*changes: ChangeCandidate, message: str | None = None) -> PlanningResponse:
    return PlanningResponse(candidate=PlanningCandidate(changes=changes, message=message))


async def seeded(
    session: AsyncSession,
    service: CalendarPlanningService,
    parts: frozenset[PlanningPart] = frozenset(),
) -> PreparedPlan:
    events = EventService(session, clock=lambda: NOW)
    await events.create(timed("Dentist", WEDNESDAY, 11, 12))
    await events.create(timed("Standup", FRIDAY, 9, 10))
    await events.create(
        EventEdit(
            title="Trip",
            all_day=True,
            start_date=date(2026, 10, 20),
            last_date=date(2026, 10, 21),
            time_zone="America/Toronto",
        )
    )
    session.add(
        ActionTable(
            public_id="11111111-0000-4000-8000-000000000001",
            title="Send the deck",
            ownership="mine",
            status="open",
            deadline_precision="date",
            deadline_date=FRIDAY,
            deadline_origin="email",
            target_date=date(2026, 10, 15),
            notes="",
            created_at_utc=NOW,
            updated_at_utc=NOW,
            revision=1,
        )
    )
    await session.commit()
    return await service.prepare(
        PlanningOptions(instruction="Move the dentist to Friday at 10", parts=parts),
        MONDAY,
        TORONTO,
    )


class Reader:
    async def list_calendars(self) -> tuple[GoogleCalendarInfo, ...]:
        return (
            GoogleCalendarInfo(
                calendar_id="owner@example.com", name="Owner", primary=True, shown=True
            ),
        )

    async def list_events(self, calendar: GoogleCalendarInfo, *_: Any) -> tuple[GoogleEvent, ...]:
        return (
            GoogleEvent(
                calendar_id=calendar.calendar_id,
                calendar_name="Owner",
                event_id="g",
                title="Team sync",
                all_day=False,
                starts_at_utc=at(FRIDAY, 10, 30),
                ends_at_utc=at(FRIDAY, 11, 30),
            ),
        )


async def test_prepare_sends_own_events_and_only_the_chosen_parts(
    session: AsyncSession, service: CalendarPlanningService
) -> None:
    plan = await seeded(session, service)
    request = plan.request
    assert (request.window_first, request.window_last) == (MONDAY, date(2026, 11, 8))
    assert (
        request.now_local == "2026-10-14 14:20 (Wednesday)"
        and request.time_zone == "America/Toronto"
    )
    assert [(e.key, e.title, e.start, e.end) for e in request.events] == [
        ("e1", "Trip", "2026-10-20", "2026-10-21"),
        ("e2", "Dentist", "2026-10-14 11:00", "2026-10-14 12:00"),
        ("e3", "Standup", "2026-10-16 09:00", "2026-10-16 10:00"),
    ]
    assert request.busy is None and request.actions is None
    assert plan.preview.first_use and len(plan.preview.lines) == 2
    assert "2026-10-12 to 2026-11-08" in plan.preview.lines[1].label

    await CalendarSyncService(session, Reader(), clock=lambda: NOW).sync(WEDNESDAY, TORONTO)
    times = await service.prepare(
        PlanningOptions(instruction="x", parts=frozenset({PlanningPart.GOOGLE_BUSY})),
        MONDAY,
        TORONTO,
    )
    assert times.request.busy is not None and [b.title for b in times.request.busy] == [
        None
    ]  # Times only: titles are a separate choice.
    full = await service.prepare(
        PlanningOptions(instruction="x", parts=frozenset(PlanningPart)), MONDAY, TORONTO
    )
    assert full.request.busy is not None and [b.title for b in full.request.busy] == ["Team sync"]
    assert full.request.actions is not None
    assert [(a.title, a.deadline, a.target) for a in full.request.actions] == [
        ("Send the deck", "2026-10-16", "2026-10-15")
    ]
    assert [line.label.split(" ")[1] for line in full.preview.lines[2:]] == ["Google", "open"]
    with pytest.raises(ValueError):
        PlanningOptions(instruction="x", parts=frozenset({PlanningPart.GOOGLE_TITLES}))


async def test_generate_needs_a_key_and_approval_and_records_consent_once(
    session: AsyncSession, service: CalendarPlanningService, provider: Provider
) -> None:
    plan = await seeded(session, service)
    provider.key = False
    assert (await service.generate(plan, Gate())).error_code == "AI_KEY_MISSING"
    provider.key = True
    declined = await service.generate(plan, Gate(answer=False))
    assert declined.status is PlanningStatus.DECLINED and provider.requests == []
    assert not await service.consent_active()

    provider.script = [answer(change(start_date="2026-10-16", start_time="10:00"))]
    gate = Gate()
    outcome = await service.generate(plan, gate)
    assert outcome.status is PlanningStatus.PROPOSED and gate.previews[0].first_use
    assert await OwnerConsentRepository(session).get_active("groq", CALENDAR_SCOPE, "1") is not None
    provider.script = [answer()]
    later = Gate()
    await service.generate(plan, later)
    assert not later.previews[0].first_use


async def test_generate_retries_once_and_reports_codes(
    session: AsyncSession, service: CalendarPlanningService, provider: Provider
) -> None:
    plan = await seeded(session, service)
    provider.script = [
        PlanningResponse(problem=DraftingProblem.INVALID_OUTPUT),
        answer(change(title="Dentist (Fri)")),
    ]
    outcome = await service.generate(plan, Gate())
    assert outcome.status is PlanningStatus.PROPOSED and len(provider.requests) == 2
    provider.script = [PlanningResponse(problem=DraftingProblem.REFUSED)] * 2
    assert (await service.generate(plan, Gate())).error_code == "AI_REFUSED"
    provider.script = [ProviderRateLimitError("slow down")]
    assert (await service.generate(plan, Gate())).error_code == "AI_RATE_LIMITED"
    cancel = asyncio.Event()
    cancel.set()
    assert (await service.generate(plan, Gate(), cancel)).status is PlanningStatus.CANCELLED


async def test_checks_keep_good_changes_and_drop_bad_ones(
    session: AsyncSession, service: CalendarPlanningService
) -> None:
    plan = await seeded(session, service)
    checked = validate_plan(
        PlanningCandidate(
            changes=(
                change(
                    event_key="e2",
                    start_date="2026-10-16",
                    start_time="09:30",
                    reason="Moves it\x1b[31m to Friday.",
                ),
                change(event_key="e2", title="Again"),  # The same event twice.
                change(event_key="e9", title="Unknown"),
                change(op="explode"),
                change(
                    op="create",
                    event_key=None,
                    title="Deck work\n",
                    all_day=False,
                    start_date="2026-10-15",
                    start_time="09:00",
                    end_time="11:00",
                ),
                change(
                    op="create",
                    event_key=None,
                    title="Holiday",
                    all_day=True,
                    start_date="2026-10-23",
                    end_date="2026-10-23",
                ),
                change(
                    op="create", event_key=None, title="Far", all_day=True, start_date="2026-12-01"
                ),  # Outside the window.
                change(
                    op="create",
                    event_key=None,
                    title="Backwards",
                    all_day=False,
                    start_date="2026-10-15",
                    start_time="11:00",
                    end_time="10:00",
                ),
                change(
                    op="create", event_key=None, title="  ", all_day=True, start_date="2026-10-15"
                ),
                change(
                    op="create",
                    event_key=None,
                    title="No time",
                    all_day=False,
                    start_date="2026-10-15",
                ),
                change(
                    op="create",
                    event_key=None,
                    title="Bad day",
                    all_day=True,
                    start_date="2026-02-30",
                ),
                change(event_key="e3", start_time="09:00"),  # Changes nothing.
                change(op="delete", event_key="e1"),
            ),
            message="Done\u202e.",
        ),
        plan,
    )
    kinds = [
        (
            p.change.kind,
            p.change.edit.title
            if p.change.edit
            else p.change.event.title
            if p.change.event
            else None,
        )
        for p in checked.proposals
    ]
    assert kinds == [
        (ChangeKind.UPDATE, "Dentist"),
        (ChangeKind.CREATE, "Deck work"),
        (ChangeKind.CREATE, "Holiday"),
        (ChangeKind.DELETE, "Trip"),
    ]
    moved = checked.proposals[0].change.edit
    assert (
        moved is not None
        and moved.starts_at_utc == at(FRIDAY, 9, 30)
        and moved.ends_at_utc == at(FRIDAY, 10, 30)
    )
    assert checked.proposals[0].reason == "Moves it[31m to Friday."
    assert checked.proposals[0].overlaps == ("Standup",)  # 09:30-10:30 against 09:00-10:00.
    assert checked.ignored == 9 and checked.message == "Done."


async def test_missing_fields_come_from_the_event_and_bad_ones_refuse_the_change(
    session: AsyncSession, service: CalendarPlanningService
) -> None:
    await seeded(session, service)
    events = EventService(session, clock=lambda: NOW)
    await events.create(
        EventEdit(
            title="Late shift",
            all_day=False,
            starts_at_utc=at(date(2026, 10, 15), 22),
            ends_at_utc=at(FRIDAY, 1),
            time_zone="America/Toronto",
        )
    )
    await events.create(
        EventEdit(
            title="Call",
            all_day=False,
            starts_at_utc=at(date(2026, 10, 15), 13),
            ends_at_utc=at(date(2026, 10, 15), 14),
            time_zone="America/Vancouver",
        )
    )
    await events.create(
        EventEdit(
            title="Conference",
            all_day=True,
            start_date=date(2026, 11, 7),
            last_date=date(2026, 11, 10),  # Past the window's last day, November 8.
            time_zone="America/Toronto",
        )
    )
    plan = await service.prepare(PlanningOptions(instruction="x"), MONDAY, TORONTO)
    key = {event.title: k for k, event in plan.keys.items()}

    def edit(**fields: Any) -> EventEdit | None:
        checked = validate_plan(PlanningCandidate(changes=(change(**fields),)), plan)
        return checked.proposals[0].change.edit if checked.proposals else None

    trip = edit(event_key=key["Trip"], start_date="2026-10-27")  # The length is kept.
    assert trip is not None and (trip.start_date, trip.last_date) == (
        date(2026, 10, 27),
        date(2026, 10, 28),
    )
    shift = edit(event_key=key["Late shift"], end_time="02:00")  # It still ends the next day.
    assert shift is not None and shift.ends_at_utc == at(FRIDAY, 2)
    early = edit(event_key=key["Dentist"], start_date="2026-11-01", start_time="01:30")
    assert early is not None and early.starts_at_utc and early.ends_at_utc
    assert early.ends_at_utc - early.starts_at_utc == timedelta(hours=1)  # Across fall-back.
    renamed = edit(event_key=key["Conference"], title="Summit")  # It keeps its later days.
    assert renamed is not None and renamed.last_date == date(2026, 11, 10)
    assert edit(event_key=key["Conference"], start_date="2026-11-08") is None  # Moves out.
    assert edit(event_key=key["Call"], start_time="13:00") is None  # Only its zone differs.
    blank = edit(event_key=key["Dentist"], title="Dentist (moved)", start_time="")
    assert blank is not None and blank.starts_at_utc == at(WEDNESDAY, 11)
    friday = edit(event_key=key["Dentist"], start_date="2026-10-16")  # It keeps its time.
    assert friday is not None and (friday.starts_at_utc, friday.ends_at_utc) == (
        at(FRIDAY, 11),
        at(FRIDAY, 12),
    )
    for bad in (
        {"event_key": key["Dentist"], "start_date": "2026-10-16", "start_time": "9:30"},
        {"event_key": key["Dentist"], "start_date": "2026-10-32", "start_time": "15:00"},
        {"event_key": key["Dentist"], "start_date": "Oct 16", "start_time": "15:00"},
        {"event_key": key["Dentist"], "end_time": "24:00"},
        {
            "op": "create",
            "event_key": None,
            "title": "Far",
            "all_day": False,
            "start_date": "9999-12-31",
            "start_time": "23:30",
        },
    ):
        assert edit(**bad) is None  # Refused, never read as "keep the current value".


async def test_overlaps_name_events_and_busy_google_times(
    session: AsyncSession, service: CalendarPlanningService
) -> None:
    await CalendarSyncService(session, Reader(), clock=lambda: NOW).sync(WEDNESDAY, TORONTO)
    plan = await seeded(session, service)
    checked = validate_plan(
        PlanningCandidate(
            changes=(
                change(event_key="e2", start_date="2026-10-16", start_time="10:00"),
                change(op="delete", event_key="e3"),
            )
        ),
        plan,
    )
    # Friday 10:00 touches Standup, 9:00 to 10:00, without overlapping it.
    assert checked.proposals[0].overlaps == ("Team sync (Owner)",)


async def test_overlaps_follow_what_is_ticked(
    session: AsyncSession, service: CalendarPlanningService
) -> None:
    plan = await seeded(session, service)
    checked = validate_plan(
        PlanningCandidate(
            changes=(
                change(op="delete", event_key="e3"),  # Standup, Friday 9:00 to 10:00.
                change(event_key="e2", start_date="2026-10-16", start_time="09:00"),
            )
        ),
        plan,
    )
    changes = [p.change for p in checked.proposals]
    assert [ticked_at_first(c) for c in changes] == [False, True]  # A delete waits for a tick.
    assert checked.proposals[1].overlaps == ("Standup",)  # As it starts: Standup stays.
    assert overlaps_for(changes, [True, True], plan) == ((), ())
    assert overlaps_for(changes, [False, False], plan) == ((), ("Standup",))


async def test_new_events_in_one_plan_overlap_each_other(
    session: AsyncSession, service: CalendarPlanningService
) -> None:
    plan = await seeded(session, service)
    blocks = (("Deck work", "09:00", "10:00"), ("Email", "09:30", "10:30"))
    checked = validate_plan(
        PlanningCandidate(
            changes=tuple(
                change(
                    op="create",
                    event_key=None,
                    title=title,
                    all_day=False,
                    start_date="2026-10-15",
                    start_time=start,
                    end_time=end,
                )
                for title, start, end in blocks
            )
        ),
        plan,
    )
    assert [p.overlaps for p in checked.proposals] == [("Email",), ("Deck work",)]


async def test_at_most_ten_changes_are_kept(
    session: AsyncSession, service: CalendarPlanningService
) -> None:
    plan = await seeded(session, service)
    many = tuple(
        change(
            op="create", event_key=None, title=f"Block {n}", all_day=True, start_date="2026-10-15"
        )
        for n in range(12)
    )
    checked = validate_plan(PlanningCandidate(changes=many), plan)
    assert len(checked.proposals) == 10 and checked.ignored == 2


async def test_applied_changes_undo_together_and_refuse_stale_ones(
    session: AsyncSession, service: CalendarPlanningService
) -> None:
    plan = await seeded(session, service)
    checked = validate_plan(
        PlanningCandidate(
            changes=(
                change(event_key="e2", start_date="2026-10-16", start_time="13:00"),
                change(
                    op="create",
                    event_key=None,
                    title="Deck work",
                    all_day=False,
                    start_date="2026-10-15",
                    start_time="09:00",
                    end_time="11:00",
                ),
                change(op="delete", event_key="e1"),
            )
        ),
        plan,
    )
    events = EventService(session, clock=lambda: NOW)
    batch = await events.apply_changes([p.change for p in checked.proposals])
    titles = sorted(
        e.title for e in (await events.list_range(MONDAY, date(2026, 11, 8), TORONTO)).events
    )
    assert titles == ["Deck work", "Dentist", "Standup"]
    with pytest.raises(EventConflictError):  # The plan was read before these changes.
        await events.apply_changes([p.change for p in checked.proposals[:1]])
    await events.undo_changes(batch)
    after = sorted(
        e.title for e in (await events.list_range(MONDAY, date(2026, 11, 8), TORONTO)).events
    )
    assert after == ["Dentist", "Standup", "Trip"]
    with pytest.raises(EventConflictError):
        await events.undo_changes(batch)  # Already undone: the events moved on.
    with pytest.raises(ValueError):
        await events.apply_changes([])


async def test_a_conflict_applies_nothing(
    session: AsyncSession, service: CalendarPlanningService
) -> None:
    plan = await seeded(session, service)
    checked = validate_plan(
        PlanningCandidate(
            changes=(
                change(
                    op="create", event_key=None, title="New", all_day=True, start_date="2026-10-15"
                ),
                change(event_key="e2", title="Dentist, renamed"),
            )
        ),
        plan,
    )
    events = EventService(session, clock=lambda: NOW)
    dentist = plan.keys["e2"]
    await events.save(
        dentist.public_id,
        dentist.revision,
        dentist.edit().model_copy(update={"notes": "Changed elsewhere"}),
    )
    with pytest.raises(EventConflictError):
        await events.apply_changes([p.change for p in checked.proposals])
    titles = sorted(
        e.title for e in (await events.list_range(MONDAY, date(2026, 11, 8), TORONTO)).events
    )
    assert "New" not in titles

    standup = plan.keys["e3"]
    await events.delete(standup.public_id, standup.revision)
    renamed = validate_plan(
        PlanningCandidate(changes=(change(event_key="e3", title="Sync"),)), plan
    )
    with pytest.raises(EventConflictError):  # Deleted since the plan was read.
        await events.apply_changes([p.change for p in renamed.proposals])


async def test_moving_only_an_all_day_start_keeps_its_length(
    session: AsyncSession, service: CalendarPlanningService
) -> None:
    plan = await seeded(session, service)  # e1 is the two-day Trip, Oct 20 to 21.
    checked = validate_plan(
        PlanningCandidate(changes=(change(event_key="e1", start_date="2026-10-27"),)), plan
    )
    edit = checked.proposals[0].change.edit
    assert edit is not None and (edit.start_date, edit.last_date) == (
        date(2026, 10, 27),
        date(2026, 10, 28),
    )
    one_day = validate_plan(
        PlanningCandidate(
            changes=(change(event_key="e1", start_date="2026-10-27", end_date="2026-10-27"),)
        ),
        plan,
    )
    edit = one_day.proposals[0].change.edit
    assert edit is not None and edit.last_date == date(2026, 10, 27)


async def test_a_change_only_to_the_stored_zone_is_no_change(
    session: AsyncSession, service: CalendarPlanningService
) -> None:
    await EventService(session, clock=lambda: NOW).create(
        EventEdit(
            title="Tokyo holiday",
            all_day=True,
            start_date=date(2026, 10, 22),
            last_date=date(2026, 10, 22),
            time_zone="Asia/Tokyo",
        )
    )
    plan = await service.prepare(PlanningOptions(instruction="x"), MONDAY, TORONTO)
    key = next(k for k, e in plan.keys.items() if e.title == "Tokyo holiday")
    checked = validate_plan(
        PlanningCandidate(changes=(change(event_key=key, title="Tokyo holiday"),)), plan
    )
    assert checked.proposals == () and checked.ignored == 1


async def test_an_end_time_alone_is_the_first_after_the_start(
    session: AsyncSession, service: CalendarPlanningService
) -> None:
    await seeded(session, service)
    events = EventService(session, clock=lambda: NOW)
    thursday = date(2026, 10, 15)
    await events.create(
        timed("Party", thursday, 22, 23).model_copy(update={"ends_at_utc": at(FRIDAY, 0)})
    )
    await events.create(timed("Film", thursday, 21, 23))
    await events.create(
        timed("Retreat", date(2026, 10, 19), 10, 12).model_copy(
            update={"ends_at_utc": at(date(2026, 10, 20), 12)}  # 26 hours.
        )
    )
    plan = await service.prepare(PlanningOptions(instruction="x"), MONDAY, TORONTO)
    key = {event.title: k for k, event in plan.keys.items()}

    def edit(**fields: Any) -> EventEdit | None:
        checked = validate_plan(PlanningCandidate(changes=(change(**fields),)), plan)
        return checked.proposals[0].change.edit if checked.proposals else None

    party = edit(event_key=key["Party"], end_time="23:30")  # It ended at midnight.
    assert party is not None and party.ends_at_utc == at(thursday, 23, 30)  # Not 25½ hours.
    film = edit(event_key=key["Film"], end_time="01:00")  # Overnight: the next morning.
    assert film is not None and film.ends_at_utc == at(FRIDAY, 1)
    retreat = edit(event_key=key["Retreat"], end_time="14:00")  # A day or more keeps its day.
    assert retreat is not None and retreat.ends_at_utc == at(date(2026, 10, 20), 14)
    late = edit(
        op="create",
        event_key=None,
        title="Late",
        all_day=False,
        start_date="2026-10-15",
        start_time="22:00",
        end_time="02:00",
    )
    assert late is not None and late.ends_at_utc == at(FRIDAY, 2)
    assert edit(event_key=key["Dentist"], end_time="09:00") is None  # 21 hours on: no date.


async def test_the_preview_says_which_days_google_times_cover(
    session: AsyncSession, service: CalendarPlanningService, monkeypatch: pytest.MonkeyPatch
) -> None:
    times = PlanningOptions(instruction="x", parts=frozenset({PlanningPart.GOOGLE_BUSY}))
    plan = await seeded(session, service, times.parts)
    assert plan.preview.lines[2].label == (
        "0 Google Calendar times, without titles (none saved for these dates)"
    )
    await CalendarSyncService(session, Reader(), clock=lambda: NOW).sync(WEDNESDAY, TORONTO)
    plan = await service.prepare(times, MONDAY, TORONTO)  # The saved copy covers it all.
    assert plan.preview.lines[2].label == "1 Google Calendar times, without titles"
    later = await service.prepare(times, date(2026, 11, 23), TORONTO)  # Saved up to Dec 13.
    assert later.preview.lines[2].label.endswith("(saved only for 2026-11-23 to 2026-12-13)")
    from mailbrief.services import calendar_planning

    monkeypatch.setattr(calendar_planning, "MAX_SENT_BUSY", 0)
    cut = await service.prepare(times, MONDAY, TORONTO)
    assert cut.preview.lines[2].label == "First 0 of 1 Google Calendar times, without titles"


async def test_the_preview_says_when_the_event_cap_left_some_out(
    session: AsyncSession, service: CalendarPlanningService, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mailbrief.services import calendar_planning

    monkeypatch.setattr(calendar_planning, "MAX_SENT_EVENTS", 2)
    plan = await seeded(session, service)
    assert len(plan.request.events) == 2
    assert plan.preview.lines[1].label.startswith("Your first 2 of 3 events from 2026-10-12")
