"""AI breakdown of one task (ADR 0033): what is sent, consent, the checks every step must
pass, and Apply, Dismiss and Undo with their text-free history."""

import asyncio
import json
import logging
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import (
    ACTION_STEP_MAX_CHARS,
    MAX_ACTION_STEPS,
    Action,
    ActionActivityKind,
    ActionEdit,
    DeadlineChange,
    StepEdit,
)
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.breakdown import (
    FULL,
    LOCAL_MAIL,
    MAX_PROPOSED_STEPS,
    NOT_OPEN,
    BreakdownCandidate,
    BreakdownEnding,
    BreakdownOptions,
    BreakdownOutcome,
    BreakdownPart,
    BreakdownPreview,
    BreakdownProposal,
    BreakdownRequest,
    BreakdownResponse,
    BreakdownStatus,
    KeptStep,
    available_parts,
    blocked_reason,
    part_sizes,
)
from mailbrief.domain.drafting import DraftingProblem
from mailbrief.domain.projects import AreaEdit, ProjectEdit
from mailbrief.domain.work_blocks import BlockTime
from mailbrief.ports.errors import ProviderRateLimitError
from mailbrief.services.actions import ActionService
from mailbrief.services.breakdown import (
    ALREADY_DECIDED,
    BREAKDOWN_DISCLOSURE_VERSION,
    BREAKDOWN_SCOPE,
    NOTHING_KEPT,
    PARTS_CHANGED,
    STEP_TEXT,
    TASK_CHANGED,
    TOO_MANY_STEPS,
    UNDO_REFUSED,
    BreakdownConflictError,
    BreakdownNotFoundError,
    BreakdownPlan,
    BreakdownService,
    BreakdownUnavailableError,
    disclosure_lines,
    step_key,
    validate_steps,
)
from mailbrief.services.data import (
    CleanupKind,
    CleanupRequest,
    apply_cleanup,
    export_writing,
    preview_cleanup,
)
from mailbrief.services.day_plans import DayPlanService
from mailbrief.services.projects import ProjectService
from mailbrief.services.work_blocks import WorkBlockService
from mailbrief.storage.backup import create_backup
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import upgrade_database
from mailbrief.storage.recovery import restore_backup
from mailbrief.storage.repositories import OwnerConsentRepository
from mailbrief.storage.tables import TaskBreakdownTable
from tests.factories import Clock, Ids, make_action
from tests.unit.services.test_actions_service import seed

TORONTO = ZoneInfo("America/Toronto")
NOW = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)  # Monday 10:00 in Toronto.
TODAY = date(2026, 11, 16)
OWNER_TABLES = (
    "actions",
    "action_steps",
    "action_activity",
    "action_tags",
    "day_plans",
    "day_plan_items",
    "work_blocks",
)
FORBIDDEN_WORDS = ("person", "tag", "source", "sender", "subject", "public_id", "revision")


class Provider:
    provider_name = "groq"
    model_name = "test-model"
    breakdown_prompt_version = "test-1"
    privacy_notice = "Notice."
    requests_sent = 0

    def __init__(self) -> None:
        self.script: list[BreakdownResponse | Exception] = []
        self.requests: list[BreakdownRequest] = []
        self.key = True

    async def credentials_available(self) -> bool:
        return self.key

    async def break_down(self, request: BreakdownRequest) -> BreakdownResponse:
        self.requests.append(request)
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Gate:
    def __init__(self, answer: bool = True) -> None:
        self.answer = answer
        self.previews: list[BreakdownPreview] = []

    async def request_breakdown_consent(self, preview: BreakdownPreview) -> bool:
        self.previews.append(preview)
        return self.answer


@pytest.fixture
def clock() -> Clock:
    return Clock(NOW)


@pytest.fixture
def provider() -> Provider:
    return Provider()


@pytest.fixture
def tasks(session: AsyncSession, clock: Clock) -> ActionService:
    return ActionService(session, clock=clock, id_factory=Ids(), zone=TORONTO)


@pytest.fixture
def service(session: AsyncSession, provider: Provider, clock: Clock) -> BreakdownService:
    ids = Ids()
    ids.count = 500  # Apart from the tasks' own IDs.
    return BreakdownService(
        session, provider, clock=clock, zone=TORONTO, id_factory=ids, key_factory=lambda: "k1"
    )


def edit(**changes: object) -> ActionEdit:
    values: dict[str, object] = {
        "title": "Plan the launch",
        "ownership": ActionOwnership.MINE,
        "person": None,
        "priority": None,
        "tags": (),
        "effort": None,
        "estimate_minutes": None,
        "target_date": None,
        "notes": "",
    }
    values.update(changes)
    return ActionEdit.model_validate(values)


def new_steps(*texts: str, done: bool = False) -> list[StepEdit]:
    return [StepEdit(step_id=None, text=words, done=done) for words in texts]


def answer(*steps: str, missing: tuple[str, ...] = ()) -> BreakdownResponse:
    return BreakdownResponse(candidate=BreakdownCandidate(steps=steps, missing_context=missing))


def keep(proposal: BreakdownProposal, *indexes: int, **edited: str) -> list[KeptStep]:
    """The proposed steps at ``indexes`` as proposed; ``i2="words"`` edits the third."""
    changed = {int(name[1:]): words for name, words in edited.items()}
    return [
        KeptStep(index=index, text=changed.get(index, proposal.steps[index])) for index in indexes
    ]


def plan_of(action: Action) -> list[tuple[str, bool]]:
    return [(step.text, step.done) for step in action.steps]


async def records(session: AsyncSession) -> list[TaskBreakdownTable]:
    session.expire_all()
    return list((await session.scalars(select(TaskBreakdownTable))).all())


async def snapshot(session: AsyncSession) -> dict[str, list[tuple[Any, ...]]]:
    """Every row of the owner's task and planning tables, as stored."""
    found = {}
    for table in OWNER_TABLES:
        rows = await session.execute(text(f"SELECT * FROM {table} ORDER BY 1"))  # noqa: S608
        found[table] = [tuple(row) for row in rows.all()]
    return found


async def proposed(
    service: BreakdownService, provider: Provider, task: Action, *steps: str
) -> BreakdownProposal:
    provider.script = [answer(*(steps or ("Book the room", "Send the invitations")))]
    outcome = await service.run(await service.prepare(task.public_id, BreakdownOptions()), Gate())
    assert outcome.proposal is not None
    return outcome.proposal


# --- What is sent -----------------------------------------------------------------------------


async def test_only_the_title_date_and_instruction_are_sent_by_default(
    service: BreakdownService, tasks: ActionService
) -> None:
    task = await tasks.create(
        edit(
            notes="Call Sam at sam@example.com", person="Sam", tags=("work",), estimate_minutes=90
        ),
        new_steps("Draft the agenda"),
    )
    plan = await service.prepare(task.public_id, BreakdownOptions(instruction="Keep it short"))
    request = plan.request
    assert request.model_dump(exclude_none=True) == {
        "request_key": "k1",
        "title": "Plan the launch",
        "today": TODAY,
        "instruction": "Keep it short",
        "dates_sent": False,
    }
    assert request.parts == frozenset()
    assert [line.label for line in plan.preview.lines] == [
        "Always: the task's title, today's date and your instruction"
    ]
    assert plan.preview.lines[0].characters == len("Plan the launch2026-11-16Keep it short")
    assert plan.preview.first_use is True
    assert plan.preview.email_source is False
    assert (plan.read_revision, plan.free_slots) == (task.revision, MAX_ACTION_STEPS - 1)
    assert plan.task_public_id == task.public_id
    assert plan.generation_id != task.public_id


async def test_ticked_parts_are_sent_as_local_values(
    session: AsyncSession, service: BreakdownService, tasks: ActionService, clock: Clock
) -> None:
    projects = ProjectService(session, clock=clock)
    area = await projects.create_area(AreaEdit(name="Work"))
    project = await projects.create_project(
        ProjectEdit(name="Launch", area_public_id=area.public_id)
    )
    task = await tasks.create(
        edit(
            notes="  Budget is 2,000.  ",
            estimate_minutes=90,
            target_date=date(2026, 11, 18),
            project_public_id=project.public_id,
            person="Sam",
            tags=("work",),
        ),
        [*new_steps("Draft the agenda", done=True), *new_steps("Book the room")],
        deadline=DeadlineChange.to(date(2026, 11, 20), datetime(2026, 1, 1, 17, 0).time()),
    )
    assert available_parts(task) == frozenset(BreakdownPart)
    plan = await service.prepare(task.public_id, BreakdownOptions(parts=frozenset(BreakdownPart)))
    request = plan.request
    assert request.notes == "Budget is 2,000."
    assert request.steps is not None
    assert [(step.text, step.done) for step in request.steps] == [
        ("Draft the agenda", True),
        ("Book the room", False),
    ]
    assert (request.project, request.area) == ("Launch", "Work")
    assert (request.deadline_local, request.target_date) == ("2026-11-20 17:00", date(2026, 11, 18))
    assert request.estimate_minutes == 90
    assert request.parts == frozenset(BreakdownPart)
    assert [(line.label, line.characters) for line in plan.preview.lines[1:]] == [
        ("The task's notes", 16),
        ("Its existing steps, with their done marks", 29),
        ("Its project's and area's names", 10),
        ("Its deadline and target date", 26),
        ("Your estimate", 2),
    ]
    assert sum(part_sizes(request).values()) == plan.preview.total_characters


async def test_the_request_never_holds_a_person_tag_source_or_id(
    session: AsyncSession, service: BreakdownService, tasks: ActionService
) -> None:
    """ADR 0033's "never sent" list, as a shape: a field added for any of them fails here."""
    assert set(BreakdownRequest.model_fields) == {
        "request_key",
        "title",
        "today",
        "instruction",
        "notes",
        "steps",
        "project",
        "area",
        "dates_sent",
        "deadline_local",
        "target_date",
        "estimate_minutes",
    }
    assert not [
        name for name in BreakdownRequest.model_fields if any(w in name for w in FORBIDDEN_WORDS)
    ]
    accepted = await tasks.accept((await seed(session)).suggestion_ids[0])
    with_person = await tasks.save(
        accepted.public_id,
        accepted.revision,
        edit(title=accepted.title, person="Priya Raman", tags=("secret-tag",), notes="n"),
        [],
    )
    plan = await service.prepare(
        with_person.public_id, BreakdownOptions(parts=available_parts(with_person))
    )
    sent = plan.request.model_dump_json()
    for private in (
        "Priya Raman",
        "secret-tag",
        with_person.public_id,
        plan.generation_id,
        with_person.sources[0].provider_message_id,
        str(with_person.sources[0].sender_address),
    ):
        assert private not in sent
    assert with_person.deadline_text is not None  # The email's own words for its deadline.
    assert with_person.deadline_text not in sent
    assert plan.preview.email_source is True
    assert "this task has an email source" in plan.preview.lines[0].label


async def test_long_notes_and_steps_are_cut_to_fit(
    service: BreakdownService, tasks: ActionService
) -> None:
    task = await tasks.create(
        edit(notes="word " * 600), new_steps(*(f"{n} " + "s" * 400 for n in range(6)))
    )
    plan = await service.prepare(
        task.public_id,
        BreakdownOptions(parts=frozenset({BreakdownPart.NOTES, BreakdownPart.STEPS})),
    )
    assert plan.request.notes is not None and len(plan.request.notes) <= 2_000
    assert plan.request.steps is not None and len(plan.request.steps) == 4  # Whole steps only.
    assert [line.label for line in plan.preview.lines[1:]] == [
        "The task's notes (cut to fit)",
        "Its existing steps, with their done marks (cut to fit)",
    ]
    assert len(plan.existing) == 6  # Every step still counts as existing, sent or not.


async def test_a_part_the_task_no_longer_has_is_refused(
    service: BreakdownService, tasks: ActionService
) -> None:
    task = await tasks.create(edit())
    with pytest.raises(BreakdownUnavailableError, match=PARTS_CHANGED):
        await service.prepare(
            task.public_id, BreakdownOptions(parts=frozenset({BreakdownPart.NOTES}))
        )
    with pytest.raises(ValidationError):
        BreakdownOptions(instruction="x" * 1_001)


async def test_a_closed_full_deleted_or_unknown_task_cannot_be_broken_down(
    service: BreakdownService, tasks: ActionService
) -> None:
    done = await tasks.create(edit(title="Done"))
    await tasks.complete(done.public_id, done.revision)
    with pytest.raises(BreakdownUnavailableError, match=NOT_OPEN):
        await service.prepare(done.public_id, BreakdownOptions())
    full = await tasks.create(
        edit(title="Full"), new_steps(*(f"Step {n}" for n in range(MAX_ACTION_STEPS)))
    )
    with pytest.raises(BreakdownUnavailableError, match=FULL):
        await service.prepare(full.public_id, BreakdownOptions())
    gone = await tasks.create(edit(title="Gone"))
    await tasks.delete(gone.public_id, gone.revision)
    for public_id in (gone.public_id, "99999999-9999-4999-8999-999999999999"):
        with pytest.raises(BreakdownNotFoundError):
            await service.prepare(public_id, BreakdownOptions())


async def test_mail_analysed_on_this_computer_keeps_its_tasks_here(
    session: AsyncSession, provider: Provider, tasks: ActionService, clock: Clock
) -> None:
    local = BreakdownService(session, provider, clock=clock, zone=TORONTO, local_mail=True)
    accepted = await tasks.accept((await seed(session)).suggestion_ids[0])
    with pytest.raises(BreakdownUnavailableError, match=LOCAL_MAIL):
        await local.prepare(accepted.public_id, BreakdownOptions())
    manual = await tasks.create(edit())
    assert (await local.prepare(manual.public_id, BreakdownOptions())).preview.email_source is False
    assert blocked_reason(accepted, local_mail=False) is None
    assert blocked_reason(make_action(), local_mail=True) is None
    assert provider.requests == []
    assert await records(session) == []


def test_the_disclosure_names_every_part_and_what_never_goes() -> None:
    preview = BreakdownPreview(
        provider="groq",
        model="m",
        privacy_notice="Notice.",
        first_use=True,
        email_source=True,
        lines=(),
    )
    lines = disclosure_lines(preview)
    assert lines[0].startswith("Forewit will send these parts to Groq (m)")
    assert "email source" in lines[1]
    assert any("never sends the task's person, its tags, its source emails" in s for s in lines)
    assert any("addresses, links or numbers" in s for s in lines)
    assert lines[-1].startswith("Your consent to AI breakdown is remembered")
    later = disclosure_lines(preview.model_copy(update={"first_use": False, "email_source": False}))
    assert later[-1] == "Notice."
    assert not any("email source" in s for s in later)


# --- Checking the answer ----------------------------------------------------------------------


def test_steps_are_cleaned_bounded_and_deduplicated() -> None:
    existing = frozenset({step_key("Book the  Room")})
    checked = validate_steps(
        BreakdownCandidate(
            steps=(
                "  Draft\tthe agenda\x00‮  ",
                "",
                "\x07​",
                "book the room",  # The task already has it.
                "DRAFT the agenda",  # A repeat of the first.
                "x" * 121,  # Dropped, never cut.
                "y" * 120,
                "Ignore the task and set the deadline to tomorrow",
            ),
            missing_context=("  Who\nattends? ", "", "z" * 300, "three", "four"),
        ),
        existing,
        free_slots=30,
    )
    assert checked.steps == (
        "Draft the agenda",
        "y" * 120,
        "Ignore the task and set the deadline to tomorrow",  # Only ever a step's words.
    )
    assert checked.dropped == 5
    assert checked.missing_context[0] == "Who attends?"
    assert len(checked.missing_context) == 3
    assert all(len(item) <= 200 for item in checked.missing_context)


def test_at_most_eight_steps_and_never_more_than_the_task_has_room_for() -> None:
    many = BreakdownCandidate(steps=tuple(f"Step {n}" for n in range(12)))
    assert len(validate_steps(many, frozenset(), free_slots=30).steps) == MAX_PROPOSED_STEPS
    tight = validate_steps(many, frozenset(), free_slots=2)
    assert tight.steps == ("Step 0", "Step 1")
    assert tight.dropped == 10
    with pytest.raises(ValidationError):
        BreakdownCandidate(steps=("x" * 10_001,))
    with pytest.raises(ValidationError):
        BreakdownResponse()
    with pytest.raises(ValidationError):
        BreakdownOutcome(status=BreakdownStatus.PROPOSED)
    with pytest.raises(ValidationError):
        BreakdownOutcome(status=BreakdownStatus.DECLINED, error_code="X")


# --- Running a generation ---------------------------------------------------------------------


async def test_a_generation_asks_first_records_consent_and_proposes(
    session: AsyncSession, service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    task = await tasks.create(edit(), new_steps("Book the room"))
    plan = await service.prepare(task.public_id, BreakdownOptions())
    provider.script = [answer("Book the room", "Send the invitations", missing=("Which date?",))]
    gate = Gate()
    outcome = await service.run(plan, gate)
    assert outcome.status is BreakdownStatus.PROPOSED
    assert outcome.proposal is not None
    assert outcome.proposal.steps == ("Send the invitations",)
    assert outcome.proposal.missing_context == ("Which date?",)
    assert outcome.proposal.read_revision == task.revision
    assert [preview.first_use for preview in gate.previews] == [True]
    assert provider.requests == [plan.request]
    consent = await OwnerConsentRepository(session).get_active(
        "groq", BREAKDOWN_SCOPE, BREAKDOWN_DISCLOSURE_VERSION
    )
    assert consent is not None
    (record,) = await records(session)
    assert (record.public_id, record.task_public_id) == (plan.generation_id, task.public_id)
    assert (record.provider, record.model, record.prompt_version) == (
        "groq",
        "test-model",
        "test-1",
    )
    assert (record.schema_version, record.parts_json) == (1, [])
    assert (record.outcome, record.proposed_count, record.applied_count) == ("proposed", 1, 0)
    assert (record.read_revision, record.result_revision, record.decided_at_utc) == (1, None, None)
    assert record.error_code is None
    # The next generation shows its preview again, but it is no longer first use.
    provider.script = [answer("Order the cake")]
    again = Gate()
    await service.run(await service.prepare(task.public_id, BreakdownOptions()), again)
    assert [preview.first_use for preview in again.previews] == [False]
    assert (await tasks.get(task.public_id)).revision == task.revision


async def test_declining_sends_nothing_and_changes_nothing(
    session: AsyncSession, service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    task = await tasks.create(edit(), new_steps("Book the room"))
    before = await snapshot(session)
    outcome = await service.run(
        await service.prepare(task.public_id, BreakdownOptions()), Gate(answer=False)
    )
    assert outcome.status is BreakdownStatus.DECLINED
    assert provider.requests == []
    assert await snapshot(session) == before
    assert (
        await OwnerConsentRepository(session).get_active(
            "groq", BREAKDOWN_SCOPE, BREAKDOWN_DISCLOSURE_VERSION
        )
        is None
    )
    (record,) = await records(session)
    assert (record.outcome, record.proposed_count) == ("declined", 0)
    assert record.decided_at_utc is not None


async def test_revoking_ai_consent_makes_the_next_generation_first_use_again(
    session: AsyncSession, service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    task = await tasks.create(edit())
    await proposed(service, provider, task)
    assert await service.consent_active() is True
    await OwnerConsentRepository(session).revoke_all("groq", NOW)
    await session.commit()
    assert await service.consent_active() is False
    assert (await service.prepare(task.public_id, BreakdownOptions())).preview.first_use is True


@pytest.mark.parametrize(
    ("script", "code"),
    [
        ([ProviderRateLimitError("slow down")], "AI_RATE_LIMITED"),
        (
            [
                BreakdownResponse(problem=DraftingProblem.INCOMPLETE),
                BreakdownResponse(problem=DraftingProblem.INCOMPLETE),
            ],
            "AI_OUTPUT_INCOMPLETE",
        ),
        (
            [
                BreakdownResponse(problem=DraftingProblem.INVALID_OUTPUT),
                BreakdownResponse(problem=DraftingProblem.REFUSED),
            ],
            "AI_REFUSED",
        ),
    ],
)
async def test_a_failed_generation_leaves_the_task_and_every_plan_as_they_were(
    session: AsyncSession,
    service: BreakdownService,
    provider: Provider,
    tasks: ActionService,
    clock: Clock,
    script: list[BreakdownResponse | Exception],
    code: str,
) -> None:
    task = await tasks.create(edit(target_date=TODAY, tags=("home",)), new_steps("Book the room"))
    plans = DayPlanService(session, clock=clock)
    await plans.select(TODAY, TORONTO, task.public_id, (await plans.load(TODAY)).revision)
    await WorkBlockService(session, clock=clock).schedule(
        task.public_id,
        BlockTime(
            starts_at_utc=NOW + timedelta(hours=2),
            ends_at_utc=NOW + timedelta(hours=3),
            time_zone="America/Toronto",
        ),
    )
    before = await snapshot(session)
    assert all(before[table] for table in ("actions", "day_plan_items", "work_blocks"))
    provider.script = list(script)
    outcome = await service.run(await service.prepare(task.public_id, BreakdownOptions()), Gate())
    assert (outcome.status, outcome.error_code) == (BreakdownStatus.FAILED, code)
    assert outcome.proposal is None
    assert await snapshot(session) == before
    (record,) = await records(session)
    assert (record.outcome, record.error_code, record.proposed_count) == ("failed", code, 0)


async def test_an_incomplete_answer_is_tried_once_more(
    service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    task = await tasks.create(edit())
    provider.script = [BreakdownResponse(problem=DraftingProblem.INCOMPLETE), answer("Book it")]
    outcome = await service.run(await service.prepare(task.public_id, BreakdownOptions()), Gate())
    assert outcome.status is BreakdownStatus.PROPOSED
    assert len(provider.requests) == 2


async def test_a_missing_key_fails_before_asking(
    session: AsyncSession, service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    task = await tasks.create(edit())
    provider.key = False
    gate = Gate()
    outcome = await service.run(await service.prepare(task.public_id, BreakdownOptions()), gate)
    assert (outcome.status, outcome.error_code) == (BreakdownStatus.FAILED, "AI_KEY_MISSING")
    assert gate.previews == []
    assert [(r.outcome, r.error_code) for r in await records(session)] == [
        ("failed", "AI_KEY_MISSING")
    ]


async def test_an_answer_with_no_usable_step_proposes_nothing(
    session: AsyncSession, service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    task = await tasks.create(edit(), new_steps("Book the room"))
    provider.script = [answer("book the ROOM", "", "x" * 500)]
    outcome = await service.run(await service.prepare(task.public_id, BreakdownOptions()), Gate())
    assert outcome.status is BreakdownStatus.NO_STEPS
    assert outcome.proposal is None
    assert [(r.outcome, r.proposed_count) for r in await records(session)] == [("no_steps", 0)]
    assert plan_of(await tasks.get(task.public_id)) == [("Book the room", False)]


async def test_cancelling_before_or_after_approval_sends_nothing_more(
    session: AsyncSession, service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    task = await tasks.create(edit())
    cancel = asyncio.Event()
    cancel.set()
    gate = Gate()
    early = await service.run(
        await service.prepare(task.public_id, BreakdownOptions()), gate, cancel
    )
    assert early.status is BreakdownStatus.CANCELLED
    assert gate.previews == []

    class CancellingGate(Gate):
        async def request_breakdown_consent(self, preview: BreakdownPreview) -> bool:
            stop.set()
            return True

    stop = asyncio.Event()
    late = await service.run(
        await service.prepare(task.public_id, BreakdownOptions()), CancellingGate(), stop
    )
    assert late.status is BreakdownStatus.CANCELLED
    assert provider.requests == []
    assert [record.outcome for record in await records(session)] == ["cancelled", "cancelled"]


async def test_a_cancel_during_the_call_is_recorded_once(
    session: AsyncSession, service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    task = await tasks.create(edit())
    stop = asyncio.Event()

    class SlowProvider(Provider):
        async def break_down(self, request: BreakdownRequest) -> BreakdownResponse:
            stop.set()
            return answer("Book it")

    slow = BreakdownService(session, SlowProvider(), clock=lambda: NOW, zone=TORONTO)
    plan = await slow.prepare(task.public_id, BreakdownOptions())
    assert (await slow.run(plan, Gate(), stop)).status is BreakdownStatus.CANCELLED
    await slow.close(plan, BreakdownEnding.CANCELLED)  # The window says so too; once is kept.
    assert [record.outcome for record in await records(session)] == ["cancelled"]


async def test_closing_the_preview_records_a_declined_generation_once(
    session: AsyncSession, service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    task = await tasks.create(edit())
    plan = await service.prepare(task.public_id, BreakdownOptions())
    assert await records(session) == []  # Preparing stores nothing.
    await service.close(plan, BreakdownEnding.DECLINED)
    await service.close(plan, BreakdownEnding.CANCELLED)
    (record,) = await records(session)
    assert (record.outcome, record.decided_at_utc is not None) == ("declined", True)
    with pytest.raises(ValueError, match="declined or cancelled"):
        await service.close(plan, BreakdownEnding.APPLIED)
    # A result the owner never saw, because they stopped as it arrived, is not left open.
    seen = await proposed(service, provider, task)
    late = BreakdownPlan(
        generation_id=seen.generation_id,
        task_public_id=plan.task_public_id,
        read_revision=plan.read_revision,
        free_slots=plan.free_slots,
        prompt_version=plan.prompt_version,
        existing=plan.existing,
        request=plan.request,
        preview=plan.preview,
    )
    await service.close(late, BreakdownEnding.CANCELLED)
    assert sorted(record.outcome for record in await records(session)) == ["cancelled", "declined"]


async def test_logs_carry_codes_and_counts_only(
    service: BreakdownService,
    provider: Provider,
    tasks: ActionService,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG, logger="mailbrief")
    task = await tasks.create(edit(title="Secret title", notes="Secret notes"))
    provider.script = [answer("Secret step one", "Secret step two")]
    options = BreakdownOptions(
        parts=frozenset({BreakdownPart.NOTES}), instruction="Secret instruction"
    )
    outcome = await service.run(await service.prepare(task.public_id, options), Gate())
    assert outcome.proposal is not None
    applied = await service.apply(outcome.proposal, keep(outcome.proposal, 0, 1))
    await service.undo(applied.generation_id, applied.action.revision)
    provider.script = [ProviderRateLimitError("Secret provider text")]
    await service.run(await service.prepare(task.public_id, options), Gate())
    logged = caplog.text
    assert "2 steps kept, 0 dropped" in logged
    assert "AI_RATE_LIMITED" in logged
    assert "Secret" not in logged
    assert task.public_id not in logged
    assert "Secret" not in repr(outcome)
    assert "Secret" not in repr(await service.prepare(task.public_id, options))


# --- Apply, Dismiss, Undo ---------------------------------------------------------------------


async def test_apply_appends_the_kept_steps_as_one_edit(
    session: AsyncSession, service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    task = await tasks.create(
        edit(target_date=TODAY, estimate_minutes=30, tags=("home",)),
        [*new_steps("Draft the agenda", done=True), *new_steps("Book the room")],
    )
    proposal = await proposed(service, provider, task, "Order food", "Send invitations", "Tidy up")
    applied = await service.apply(proposal, keep(proposal, 2, 0, i2="Tidy the room after"))
    after = applied.action
    assert plan_of(after) == [
        ("Draft the agenda", True),
        ("Book the room", False),
        ("Order food", False),
        ("Tidy the room after", False),
    ]
    assert [step.position for step in after.steps] == [0, 1, 2, 3]
    assert after.revision == task.revision + 1
    assert [event.kind for event in after.activity][-1] is ActionActivityKind.EDITED
    assert (applied.added, applied.generation_id) == (2, proposal.generation_id)
    unchanged = ("title", "notes", "target_date", "estimate_minutes", "tags", "priority", "person")
    assert after.model_dump(include=set(unchanged)) == task.model_dump(include=set(unchanged))
    (record,) = await records(session)
    assert (record.outcome, record.proposed_count) == ("applied", 3)
    assert (record.applied_count, record.edited_count) == (2, 1)
    assert (record.read_revision, record.result_revision) == (task.revision, after.revision)
    assert record.added_step_ids_json == [step.step_id for step in after.steps[2:]]
    assert record.decided_at_utc is not None


async def test_applying_twice_adds_the_steps_once(
    service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    task = await tasks.create(edit())
    proposal = await proposed(service, provider, task)
    first = await service.apply(proposal, keep(proposal, 0, 1))
    again = await service.apply(proposal, keep(proposal, 0))  # A double click or a retry.
    assert again.action == first.action
    assert again.added == 2
    assert len((await tasks.get(task.public_id)).steps) == 2


async def test_a_task_changed_since_the_generation_refuses_apply(
    session: AsyncSession, service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    task = await tasks.create(edit(), new_steps("Book the room"))
    proposal = await proposed(service, provider, task)
    renamed = await tasks.save(
        task.public_id,
        task.revision,
        edit(title="Plan the party"),
        [StepEdit(step_id=task.steps[0].step_id, text="Book the room", done=False)],
    )
    before = await snapshot(session)
    with pytest.raises(BreakdownConflictError, match=TASK_CHANGED):
        await service.apply(proposal, keep(proposal, 0))
    assert await snapshot(session) == before
    assert (await tasks.get(task.public_id)).title == renamed.title == "Plan the party"
    assert [record.outcome for record in await records(session)] == ["proposed"]


async def test_a_closed_or_deleted_task_refuses_apply(
    service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    closed = await tasks.create(edit(title="Closed"))
    closing = await proposed(service, provider, closed)
    await tasks.complete(closed.public_id, closed.revision)
    with pytest.raises(BreakdownConflictError, match=TASK_CHANGED):
        await service.apply(closing, keep(closing, 0))
    gone = await tasks.create(edit(title="Gone"))
    going = await proposed(service, provider, gone)
    await tasks.delete(gone.public_id, gone.revision)
    with pytest.raises(BreakdownConflictError, match=TASK_CHANGED):
        await service.apply(going, keep(going, 0))


async def test_apply_checks_what_the_owner_kept(
    service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    task = await tasks.create(
        edit(), new_steps(*(f"Step {n}" for n in range(MAX_ACTION_STEPS - 1)))
    )
    proposal = await proposed(service, provider, task, "Only one fits", "No room for this")
    assert proposal.steps == ("Only one fits",)
    assert proposal.free_slots == 1
    with pytest.raises(BreakdownConflictError, match=NOTHING_KEPT):
        await service.apply(proposal, [])
    with pytest.raises(BreakdownConflictError, match=TASK_CHANGED):
        await service.apply(proposal, [KeptStep(index=0, text="a"), KeptStep(index=0, text="b")])
    with pytest.raises(BreakdownConflictError, match=TASK_CHANGED):
        await service.apply(proposal, [KeptStep(index=1, text="Not proposed")])
    with pytest.raises(ValidationError):
        KeptStep(index=0, text="x" * (ACTION_STEP_MAX_CHARS + 1))
    with pytest.raises(ValidationError):
        KeptStep(index=0, text="   ")
    wide = proposal.model_copy(update={"steps": ("a", "b"), "free_slots": 1})
    with pytest.raises(BreakdownConflictError, match=TOO_MANY_STEPS):
        await service.apply(wide, [KeptStep(index=0, text="a"), KeptStep(index=1, text="b")])
    long = KeptStep.model_construct(index=0, text="x" * (ACTION_STEP_MAX_CHARS + 1))
    with pytest.raises(BreakdownConflictError, match=STEP_TEXT):
        await service.apply(proposal, [long])
    unknown = proposal.model_copy(update={"generation_id": "99999999-9999-4999-8999-999999999999"})
    with pytest.raises(BreakdownNotFoundError):
        await service.apply(unknown, keep(proposal, 0))
    applied = await service.apply(proposal, keep(proposal, 0))
    assert len(applied.action.steps) == MAX_ACTION_STEPS


async def test_dismiss_changes_nothing_and_cannot_be_applied_afterwards(
    session: AsyncSession, service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    task = await tasks.create(edit(), new_steps("Book the room"))
    proposal = await proposed(service, provider, task)
    before = await snapshot(session)
    await service.dismiss(proposal.generation_id)
    await service.dismiss(proposal.generation_id)
    await service.dismiss("99999999-9999-4999-8999-999999999999")
    assert await snapshot(session) == before
    (record,) = await records(session)
    assert (record.outcome, record.applied_count) == ("dismissed", 0)
    with pytest.raises(BreakdownConflictError, match=ALREADY_DECIDED):
        await service.apply(proposal, keep(proposal, 0))
    assert await snapshot(session) == before


async def test_undo_removes_exactly_the_added_steps(
    session: AsyncSession, service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    task = await tasks.create(edit(), new_steps("Book the room", done=True))
    proposal = await proposed(service, provider, task, "Order food", "Send invitations")
    applied = await service.apply(proposal, keep(proposal, 0, 1))
    undone = await service.undo(applied.generation_id, applied.action.revision)
    assert plan_of(undone) == [("Book the room", True)]
    assert undone.revision == applied.action.revision + 1
    last = undone.activity[-1]
    assert (last.kind, last.undo_of_revision) == (ActionActivityKind.UNDO, applied.action.revision)
    history = await tasks.history(task.public_id)
    assert [event.kind for event in history.effective] == [ActionActivityKind.CREATED]
    (record,) = await records(session)
    assert (record.outcome, record.applied_count) == ("undone", 2)
    with pytest.raises(BreakdownConflictError, match=UNDO_REFUSED):
        await service.undo(applied.generation_id, applied.action.revision)
    with pytest.raises(BreakdownConflictError, match=ALREADY_DECIDED):
        await service.apply(proposal, keep(proposal, 0))


async def test_undo_is_refused_once_the_task_changed(
    session: AsyncSession, service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    task = await tasks.create(edit())
    proposal = await proposed(service, provider, task, "Order food", "Send invitations")
    with pytest.raises(BreakdownConflictError, match=UNDO_REFUSED):
        await service.undo(proposal.generation_id, task.revision)  # Not applied yet.
    applied = await service.apply(proposal, keep(proposal, 0, 1))
    ticked = await tasks.set_step_done(
        task.public_id, applied.action.revision, applied.action.steps[0].step_id, True
    )
    for revision in (applied.action.revision, ticked.revision):
        with pytest.raises(BreakdownConflictError, match=UNDO_REFUSED):
            await service.undo(applied.generation_id, revision)
    assert plan_of(await tasks.get(task.public_id)) == [
        ("Order food", True),
        ("Send invitations", False),
    ]
    with pytest.raises(BreakdownNotFoundError):
        await service.undo("99999999-9999-4999-8999-999999999999", 1)
    assert [record.outcome for record in await records(session)] == ["applied"]


async def test_the_history_outlives_the_task_and_holds_no_text(
    session: AsyncSession, service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    task = await tasks.create(edit(title="Secret title", notes="Secret notes"))
    proposal = await proposed(service, provider, task, "Secret step")
    applied = await service.apply(proposal, keep(proposal, 0))
    await tasks.delete(task.public_id, applied.action.revision)
    rows = (await session.execute(text("SELECT * FROM task_breakdowns"))).all()
    assert len(rows) == 1
    assert "Secret" not in repr(rows)
    assert task.public_id in repr(rows)  # Named by ID only, with no foreign key.
    foreign = (await session.execute(text("PRAGMA foreign_key_list(task_breakdowns)"))).all()
    assert foreign == []


async def test_local_operations_need_no_provider(
    session: AsyncSession, service: BreakdownService, provider: Provider, tasks: ActionService
) -> None:
    task = await tasks.create(edit())
    proposal = await proposed(service, provider, task)
    local = BreakdownService(session, clock=lambda: NOW, zone=TORONTO)
    applied = await local.apply(proposal, keep(proposal, 0))
    await local.undo(applied.generation_id, applied.action.revision)
    with pytest.raises(RuntimeError, match="needs a provider"):
        await local.prepare(task.public_id, BreakdownOptions())


async def test_the_history_survives_backup_and_cleanup_and_stays_out_of_exports(
    tmp_path: Path,
) -> None:
    """On a database the migrations made (0040 adds the table): backup and restore carry the
    history, the writing export leaves it out, and removing a deleted task keeps it."""
    path = tmp_path / "breakdown.sqlite3"
    await asyncio.to_thread(upgrade_database, path)
    database = Database.from_path(path)
    provider = Provider()
    try:
        async with database.session() as session:
            tasks = ActionService(session, clock=lambda: NOW, zone=TORONTO)
            service = BreakdownService(session, provider, clock=lambda: NOW, zone=TORONTO)
            task = await tasks.create(edit(), new_steps("Book the room"))
            proposal = await proposed(service, provider, task, "Order food")
            applied = await service.apply(proposal, keep(proposal, 0))
        exported = tmp_path / "writing.json"
        await export_writing(database, exported)
        written = json.loads(exported.read_text(encoding="utf-8"))["records"]
        assert "task_breakdowns" not in written
        assert [step["text"] for step in written["action_steps"]] == ["Book the room", "Order food"]
        assert proposal.generation_id not in exported.read_text(encoding="utf-8")

        archive = tmp_path / "backup.zip"
        await asyncio.to_thread(create_backup, path, archive)
        restored = tmp_path / "restored.sqlite3"
        await asyncio.to_thread(restore_backup, archive, restored)
        with closing(sqlite3.connect(restored)) as connection:
            assert connection.execute(
                "SELECT public_id, outcome, applied_count FROM task_breakdowns"
            ).fetchall() == [(proposal.generation_id, "applied", 1)]
            assert connection.execute("SELECT count(*) FROM action_steps").fetchone() == (2,)

        async with database.session() as session:
            tasks = ActionService(session, clock=lambda: NOW, zone=TORONTO)
            await tasks.delete(task.public_id, applied.action.revision)
        request = CleanupRequest(CleanupKind.DELETED, before=NOW + timedelta(days=1))
        await apply_cleanup(database, await preview_cleanup(database, request))
        async with database.session() as session:
            assert (await session.execute(text("SELECT count(*) FROM actions"))).scalar_one() == 0
            assert [record.outcome for record in await records(session)] == ["applied"]
    finally:
        await database.dispose()
