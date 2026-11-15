"""Propose checklist steps for one task with an AI provider (ADR 0033).

Only the task's title, today's date, the owner's instruction and the parts they tick are
sent, after they approve a preview; the first use also needs recorded consent. The model's
answer can only become step text: Python checks every step, and nothing changes until the
owner applies. Apply appends the kept steps as one revision, in the same transaction as the
generation's record, and Undo removes exactly those steps while the task is unchanged. A
failed, declined, cancelled or dismissed generation leaves the task as it was. The history
holds no owner or AI text; errors and logs carry codes and counts only.
"""

import asyncio
import logging
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Final, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from mailbrief.domain.actions import (
    ACTION_STEP_MAX_CHARS,
    MAX_ACTION_STEPS,
    Action,
    ActionActivityKind,
    ActionStatus,
)
from mailbrief.domain.breakdown import (
    BREAKDOWN_NOTES_SENT_CHARS,
    BREAKDOWN_SCHEMA_VERSION,
    BREAKDOWN_STEPS_SENT_CHARS,
    MAX_MISSING_CONTEXT,
    MAX_PROPOSED_STEPS,
    MISSING_CONTEXT_MAX_CHARS,
    PART_ORDER,
    PROPOSED_STEP_MAX_CHARS,
    BreakdownApplied,
    BreakdownCandidate,
    BreakdownEnding,
    BreakdownOptions,
    BreakdownOutcome,
    BreakdownPart,
    BreakdownPreview,
    BreakdownProposal,
    BreakdownRequest,
    BreakdownStatus,
    CheckedSteps,
    KeptStep,
    SentStep,
    available_parts,
    blocked_reason,
    part_sizes,
)
from mailbrief.domain.common import new_public_id, normalize_utc, utc_now
from mailbrief.domain.drafting import DraftingProblem, PreviewLine
from mailbrief.domain.preferences import AnalysisMode
from mailbrief.ports.breakdown import BreakdownProvider
from mailbrief.ports.errors import ProviderError
from mailbrief.services.actions import touch
from mailbrief.services.analysis import KEY_MISSING, provider_detail, provider_error_code
from mailbrief.services.brief import provider_display_name
from mailbrief.services.drafting import PROBLEM_CODES, bounded
from mailbrief.services.preferences import PreferencesService, owner_zone
from mailbrief.services.writes import commit_once
from mailbrief.storage.actions import ActionRepository
from mailbrief.storage.repositories import OwnerConsentRepository
from mailbrief.storage.tables import ActionTable, TaskBreakdownTable
from mailbrief.text.prepare import clean_generated_text

logger = logging.getLogger(__name__)

BREAKDOWN_SCOPE: Final = "breakdown"
BREAKDOWN_DISCLOSURE_VERSION: Final = "1"
TASK_NOT_FOUND: Final = "That task was not found."
PARTS_CHANGED: Final = "That context is no longer available; choose again."
TASK_CHANGED: Final = "The task changed; generate again."
UNDO_REFUSED: Final = "Only an unchanged breakdown can be undone."
ALREADY_DECIDED: Final = "That breakdown was already dismissed or undone; generate again."
NOTHING_KEPT: Final = "Tick at least one step to apply."
STEP_TEXT: Final = f"A step needs 1 to {ACTION_STEP_MAX_CHARS} characters."
TOO_MANY_STEPS: Final = f"A task has at most {MAX_ACTION_STEPS} steps."
_NO_PROVIDER: Final = "AI breakdown needs a provider to prepare or run a generation."
_ATTEMPTS: Final = 2  # An unusable answer is tried once more.
_ERROR_CODE_CHARS: Final = 64
_ALWAYS_LABEL: Final = "Always: the task's title, today's date and your instruction"
_ALWAYS_EMAIL_LABEL: Final = (
    "Always: the task's title (this task has an email source), today's date and your instruction"
)
_PART_LABELS: Final = {
    BreakdownPart.NOTES: "The task's notes",
    BreakdownPart.STEPS: "Its existing steps, with their done marks",
    BreakdownPart.PROJECT: "Its project's and area's names",
    BreakdownPart.DATES: "Its deadline and target date",
    BreakdownPart.ESTIMATE: "Your estimate",
}
_ENDINGS: Final = {
    BreakdownStatus.PROPOSED: BreakdownEnding.PROPOSED,
    BreakdownStatus.NO_STEPS: BreakdownEnding.NO_STEPS,
    BreakdownStatus.DECLINED: BreakdownEnding.DECLINED,
    BreakdownStatus.FAILED: BreakdownEnding.FAILED,
    BreakdownStatus.CANCELLED: BreakdownEnding.CANCELLED,
}


class BreakdownNotFoundError(LookupError):
    """The task or the generation is gone; the message is static."""


class BreakdownUnavailableError(ValueError):
    """The task can't be broken down now, or a chosen part is gone; the message is static."""


class BreakdownConflictError(ValueError):
    """The task changed, or the generation was already decided; the message is static."""


class BreakdownGate(Protocol):
    """Asks the owner to approve one generation; the preview says whether it is first use."""

    async def request_breakdown_consent(self, preview: BreakdownPreview) -> bool: ...


@dataclass(frozen=True, slots=True)
class BreakdownPlan:
    """One prepared generation: exactly what would be sent, and the task as it was read.

    ``generation_id`` names the generation in the history and is never sent; the request's
    own key is random, sent and never stored. ``existing`` holds the keys of the task's
    steps, so a proposed step that repeats one is dropped.
    """

    generation_id: str
    task_public_id: str
    read_revision: int
    free_slots: int
    prompt_version: str
    existing: frozenset[str] = field(repr=False)
    request: BreakdownRequest = field(repr=False)
    preview: BreakdownPreview


def disclosure_lines(preview: BreakdownPreview) -> tuple[str, ...]:
    """Plain sentences describing a generation, shared by the CLI and the desktop."""
    provider = provider_display_name(preview.provider)
    lines = [
        f"Forewit will send these parts to {provider} ({preview.model}) to propose steps "
        "for this task:",
        *(f"- {line.label}: {line.characters:,} characters" for line in preview.lines),
        "It never sends the task's person, its tags, its source emails, your other tasks, "
        "notes or drafts, your calendar, Gmail or Forewit IDs, or credentials. The parts "
        "listed above are sent as written, so any addresses, links or numbers inside them "
        "are sent too.",
        "The answer can only become steps you review, edit and apply. It can't change the "
        "task's dates, priority, tags, estimate or anything else.",
        preview.privacy_notice,
    ]
    if preview.email_source:
        lines.insert(1, "This task has an email source, so its title may have come from an email.")
    if preview.first_use:
        lines.append(
            "Your consent to AI breakdown is remembered until you revoke it (Settings, or "
            "forewit-gmail-diagnostic ai-consent revoke)."
        )
    return tuple(lines)


def step_key(text: str) -> str:
    """What makes two steps the same step: case and spacing don't."""
    return " ".join(text.split()).casefold()


def _deadline_local(action: Action, zone: ZoneInfo) -> str | None:
    """The deadline as a local value ("2026-10-02" or "2026-10-02 17:00"): never the words
    an email stated it in, which are the email's."""
    if action.deadline_at_utc is not None:
        local = action.deadline_at_utc.astimezone(zone)
        return f"{local.date().isoformat()} {local:%H:%M}"
    return None if action.deadline_date is None else action.deadline_date.isoformat()


def _sent_steps(action: Action) -> tuple[tuple[SentStep, ...], bool]:
    """The task's steps in order, whole, while they fit in the limit; and whether some
    were left out."""
    ordered = sorted(action.steps, key=lambda step: step.position)
    sent: list[SentStep] = []
    used = 0
    for step in ordered:
        if used + len(step.text) > BREAKDOWN_STEPS_SENT_CHARS:
            break
        used += len(step.text)
        sent.append(SentStep(text=step.text, done=step.done))
    return tuple(sent), len(sent) < len(ordered)


def validate_steps(
    candidate: BreakdownCandidate, existing: frozenset[str], free_slots: int
) -> CheckedSteps:
    """Clean and check an untrusted answer.

    Every step loses control and format characters and extra spaces. A step that is then
    empty, longer than 120 characters, a repeat of another or of one the task already has
    is dropped, not cut. At most eight steps are kept, and never more than the task has
    room for. At most three missing-context items of 200 characters are kept.
    """
    room = min(MAX_PROPOSED_STEPS, free_slots)
    seen = set(existing)
    steps: list[str] = []
    dropped = 0
    for raw in candidate.steps:
        cleaned = clean_generated_text(raw)
        key = step_key(cleaned)
        if (
            not cleaned
            or len(cleaned) > PROPOSED_STEP_MAX_CHARS
            or key in seen
            or len(steps) >= room
        ):
            dropped += 1
            continue
        seen.add(key)
        steps.append(cleaned)
    missing = [
        bounded(cleaned, MISSING_CONTEXT_MAX_CHARS)
        for item in candidate.missing_context
        if (cleaned := clean_generated_text(item))
    ]
    return CheckedSteps(
        steps=tuple(steps), missing_context=tuple(missing[:MAX_MISSING_CONTEXT]), dropped=dropped
    )


class BreakdownService:
    """Prepare, run, apply, dismiss and undo AI breakdowns of one task.

    ``provider`` is needed only to prepare and run; applying, dismissing and undoing are
    local. ``local_mail`` says email analysis runs on this computer: a task with an email
    source can't be broken down then. Left out, it and ``zone`` are read from the owner's
    saved preferences when a generation is prepared, so the rule holds for every caller;
    unreadable preferences stop the generation (PreferencesUnavailableError).
    """

    def __init__(
        self,
        session: AsyncSession,
        provider: BreakdownProvider | None = None,
        *,
        clock: Callable[[], datetime] = utc_now,
        zone: ZoneInfo | None = None,
        local_mail: bool | None = None,
        id_factory: Callable[[], str] = new_public_id,
        key_factory: Callable[[], str] = lambda: secrets.token_hex(8),
    ) -> None:
        self._session = session
        self._provider = provider
        self._clock = clock
        self._zone = zone
        self._local_mail = local_mail
        self._id_factory = id_factory
        self._key_factory = key_factory
        self._actions = ActionRepository(session)
        self._consents = OwnerConsentRepository(session)

    def _ai(self) -> BreakdownProvider:
        if self._provider is None:
            raise RuntimeError(_NO_PROVIDER)
        return self._provider

    async def consent_active(self) -> bool:
        consent = await self._consents.get_active(
            self._ai().provider_name, BREAKDOWN_SCOPE, BREAKDOWN_DISCLOSURE_VERSION
        )
        return consent is not None

    async def _owner(self) -> tuple[ZoneInfo, bool]:
        """The owner's zone, and whether email analysis runs on this computer: as given, else
        from the saved preferences."""
        if self._zone is not None and self._local_mail is not None:
            return self._zone, self._local_mail
        preferences = await PreferencesService(self._session).get()
        return (
            self._zone or owner_zone(preferences),
            preferences.analysis_mode is AnalysisMode.LOCAL
            if self._local_mail is None
            else self._local_mail,
        )

    async def _action(self, row: ActionTable) -> Action:
        return (await self._actions.load([row]))[0]

    async def _record(self, generation_id: str) -> TaskBreakdownTable | None:
        found: TaskBreakdownTable | None = await self._session.scalar(
            select(TaskBreakdownTable).where(TaskBreakdownTable.public_id == generation_id)
        )
        return found

    async def prepare(self, public_id: str, options: BreakdownOptions) -> BreakdownPlan:
        """Build exactly what would be sent and its preview; nothing is sent or stored.

        Raises BreakdownNotFoundError for a missing or deleted task, and
        BreakdownUnavailableError when the task is closed, full or made from mail analysed
        on this computer, or when a ticked part is no longer there.
        """
        provider = self._ai()
        zone, local_mail = await self._owner()
        row = await self._actions.get_action(public_id)
        if row is None:
            raise BreakdownNotFoundError(TASK_NOT_FOUND)
        action = await self._action(row)
        reason = blocked_reason(action, local_mail=local_mail)
        if reason is not None:
            raise BreakdownUnavailableError(reason)
        if not options.parts <= available_parts(action):
            raise BreakdownUnavailableError(PARTS_CHANGED)
        notes = (
            bounded(action.notes.strip(), BREAKDOWN_NOTES_SENT_CHARS)
            if BreakdownPart.NOTES in options.parts
            else None
        )
        steps, steps_cut = (
            _sent_steps(action) if BreakdownPart.STEPS in options.parts else (None, False)
        )
        project = action.project if BreakdownPart.PROJECT in options.parts else None
        dated = BreakdownPart.DATES in options.parts
        request = BreakdownRequest(
            request_key=self._key_factory(),
            title=action.title,
            today=normalize_utc(self._clock()).astimezone(zone).date(),
            instruction=options.instruction,
            notes=notes,
            steps=steps,
            project=None if project is None else project.name,
            area=None if project is None else project.area_name,
            dates_sent=dated,
            deadline_local=_deadline_local(action, zone) if dated else None,
            target_date=action.target_date if dated else None,
            estimate_minutes=(
                action.estimate_minutes if BreakdownPart.ESTIMATE in options.parts else None
            ),
        )
        sizes = part_sizes(request)
        cut = {
            BreakdownPart.NOTES: notes is not None and len(notes) < len(action.notes.strip()),
            BreakdownPart.STEPS: steps_cut,
        }
        email_source = bool(action.sources)
        lines = [
            PreviewLine(
                label=_ALWAYS_EMAIL_LABEL if email_source else _ALWAYS_LABEL,
                characters=sizes[None],
            )
        ]
        lines.extend(
            PreviewLine(
                label=_PART_LABELS[part] + (" (cut to fit)" if cut.get(part) else ""),
                characters=sizes[part],
            )
            for part in PART_ORDER
            if part in sizes
        )
        preview = BreakdownPreview(
            provider=provider.provider_name,
            model=provider.model_name,
            privacy_notice=provider.privacy_notice,
            first_use=not await self.consent_active(),
            email_source=email_source,
            lines=tuple(lines),
        )
        return BreakdownPlan(
            generation_id=self._id_factory(),
            task_public_id=action.public_id,
            read_revision=action.revision,
            free_slots=MAX_ACTION_STEPS - len(action.steps),
            prompt_version=provider.breakdown_prompt_version,
            existing=frozenset(step_key(step.text) for step in action.steps),
            request=request,
            preview=preview,
        )

    async def _write_record(
        self,
        plan: BreakdownPlan,
        ending: BreakdownEnding,
        *,
        error_code: str | None = None,
        proposed: int = 0,
    ) -> None:
        """Record how ``plan``'s generation ended, once. A generation already recorded as
        proposed, whose result the owner never saw, takes a later ending."""

        async def write(now: datetime) -> None:
            record = await self._record(plan.generation_id)
            if record is not None:
                if (
                    record.outcome == BreakdownEnding.PROPOSED.value
                    and ending is not BreakdownEnding.PROPOSED
                    and error_code is None
                ):
                    record.outcome = ending.value
                    record.decided_at_utc = now
                return
            self._session.add(
                TaskBreakdownTable(
                    public_id=plan.generation_id,
                    task_public_id=plan.task_public_id,
                    provider=plan.preview.provider,
                    model=plan.preview.model,
                    prompt_version=plan.prompt_version,
                    schema_version=BREAKDOWN_SCHEMA_VERSION,
                    parts_json=[part.value for part in PART_ORDER if part in plan.request.parts],
                    proposed_count=proposed,
                    applied_count=0,
                    edited_count=0,
                    outcome=ending.value,
                    error_code=None if error_code is None else error_code[:_ERROR_CODE_CHARS],
                    read_revision=plan.read_revision,
                    created_at_utc=now,
                    decided_at_utc=None if ending is BreakdownEnding.PROPOSED else now,
                )
            )

        await commit_once(self._session, self._clock, write)

    async def _ended(
        self,
        plan: BreakdownPlan,
        status: BreakdownStatus,
        *,
        error_code: str | None = None,
        detail: str | None = None,
    ) -> BreakdownOutcome:
        await self._write_record(plan, _ENDINGS[status], error_code=error_code)
        return BreakdownOutcome(status=status, error_code=error_code, provider_detail=detail)

    async def run(
        self, plan: BreakdownPlan, gate: BreakdownGate, cancel: asyncio.Event | None = None
    ) -> BreakdownOutcome:
        """Ask approval, record first-use consent, call the provider and check the answer.

        Nothing is sent before approval. An unusable answer is tried once more. The task
        doesn't change here, whatever happens; the generation's record is written before
        this returns.
        """
        provider = self._ai()

        def cancelled() -> bool:
            return cancel is not None and cancel.is_set()

        if cancelled():
            return await self._ended(plan, BreakdownStatus.CANCELLED)
        if not await provider.credentials_available():
            return await self._ended(plan, BreakdownStatus.FAILED, error_code=KEY_MISSING)
        first_use = not await self.consent_active()
        preview = plan.preview.model_copy(update={"first_use": first_use})
        if not await gate.request_breakdown_consent(preview):
            logger.info("AI breakdown declined")
            return await self._ended(plan, BreakdownStatus.DECLINED)
        if cancelled():
            return await self._ended(plan, BreakdownStatus.CANCELLED)
        if first_use:
            await self._consents.grant(
                provider.provider_name,
                BREAKDOWN_SCOPE,
                BREAKDOWN_DISCLOSURE_VERSION,
                normalize_utc(self._clock()),
            )
            await self._session.commit()
        checked: CheckedSteps | None = None
        problem: DraftingProblem | None = None
        for _ in range(_ATTEMPTS):
            if cancelled():
                return await self._ended(plan, BreakdownStatus.CANCELLED)
            try:
                response = await provider.break_down(plan.request)
            except ProviderError as exc:
                code = provider_error_code(exc)
                logger.info("AI breakdown call failed: %s", code)
                return await self._ended(
                    plan, BreakdownStatus.FAILED, error_code=code, detail=provider_detail(exc)
                )
            if response.candidate is None:
                problem = response.problem
                continue
            checked = validate_steps(response.candidate, plan.existing, plan.free_slots)
            break
        if cancelled():
            return await self._ended(plan, BreakdownStatus.CANCELLED)
        if checked is None:
            code = PROBLEM_CODES[problem or DraftingProblem.INVALID_OUTPUT]
            logger.info("AI breakdown answer unusable: %s", code)
            return await self._ended(plan, BreakdownStatus.FAILED, error_code=code)
        logger.info("AI breakdown: %d steps kept, %d dropped", len(checked.steps), checked.dropped)
        if not checked.steps:
            return await self._ended(plan, BreakdownStatus.NO_STEPS)
        proposal = BreakdownProposal(
            generation_id=plan.generation_id,
            task_public_id=plan.task_public_id,
            read_revision=plan.read_revision,
            steps=checked.steps,
            missing_context=checked.missing_context,
            free_slots=plan.free_slots,
        )
        await self._write_record(plan, BreakdownEnding.PROPOSED, proposed=len(checked.steps))
        return BreakdownOutcome(status=BreakdownStatus.PROPOSED, proposal=proposal)

    async def close(
        self, plan: BreakdownPlan, ending: BreakdownEnding, error_code: str | None = None
    ) -> None:
        """Record a generation that ended without ``run`` saying so: the owner closed the
        preview (declined) or stopped the call (cancelled), or the run broke before it could
        answer (failed, with ``error_code``, a code and never text). Recording twice changes
        nothing."""
        allowed = {BreakdownEnding.DECLINED, BreakdownEnding.CANCELLED, BreakdownEnding.FAILED}
        if ending not in allowed or (ending is BreakdownEnding.FAILED) != (error_code is not None):
            raise ValueError("a generation is closed as declined, cancelled or failed with a code")
        await self._write_record(plan, ending, error_code=error_code)

    @staticmethod
    def _kept_texts(proposal: BreakdownProposal, kept: Sequence[KeptStep]) -> list[str]:
        """The kept steps' words, in proposed order; a selection that can't be applied is
        refused as a conflict, with the reason."""
        if not kept:
            raise BreakdownConflictError(NOTHING_KEPT)
        indexes = [step.index for step in kept]
        if len(set(indexes)) != len(indexes) or max(indexes) >= len(proposal.steps):
            raise BreakdownConflictError(TASK_CHANGED)
        if len(kept) > proposal.free_slots:
            raise BreakdownConflictError(TOO_MANY_STEPS)
        texts = [step.text.strip() for step in sorted(kept, key=lambda step: step.index)]
        if any(not 1 <= len(text) <= ACTION_STEP_MAX_CHARS for text in texts):
            raise BreakdownConflictError(STEP_TEXT)
        return texts

    async def apply(
        self, proposal: BreakdownProposal, kept: Sequence[KeptStep]
    ) -> BreakdownApplied:
        """Append the kept steps after the task's own, as one revision recorded as an edit,
        in the same transaction as the generation's record.

        The task must be live, open and at the revision the generation read; otherwise
        BreakdownConflictError says to generate again. Existing steps are never changed,
        reordered, ticked or removed, and nothing else about the task changes. Applying a
        generation that is already applied returns the task as it is, adding nothing.
        """
        texts = self._kept_texts(proposal, kept)
        edited = sum(1 for step in kept if step.text.strip() != proposal.steps[step.index])

        async def run(now: datetime) -> BreakdownApplied:
            record = await self._record(proposal.generation_id)
            if record is None or record.task_public_id != proposal.task_public_id:
                raise BreakdownNotFoundError(TASK_NOT_FOUND)
            row = await self._actions.get_action(proposal.task_public_id)
            if record.outcome == BreakdownEnding.APPLIED.value:
                if row is None:
                    raise BreakdownNotFoundError(TASK_NOT_FOUND)
                return BreakdownApplied(
                    action=await self._action(row),
                    generation_id=record.public_id,
                    added=record.applied_count,
                )
            if record.outcome != BreakdownEnding.PROPOSED.value:
                raise BreakdownConflictError(ALREADY_DECIDED)
            if (
                row is None
                or row.status != ActionStatus.OPEN.value
                or row.revision != record.read_revision
            ):
                raise BreakdownConflictError(TASK_CHANGED)
            steps = await self._actions.step_rows(row.id)
            if len(steps) + len(texts) > MAX_ACTION_STEPS:
                raise BreakdownConflictError(TOO_MANY_STEPS)
            first = max((step.position for step in steps), default=-1) + 1
            added = [
                self._actions.add_step(row.id, first + offset, text, None)
                for offset, text in enumerate(texts)
            ]
            touch(row, now)
            await self._session.flush()  # The new steps get their IDs.
            await self._actions.record_activity(row, ActionActivityKind.EDITED, now)
            record.outcome = BreakdownEnding.APPLIED.value
            record.applied_count = len(added)
            record.edited_count = edited
            record.result_revision = row.revision
            record.added_step_ids_json = [step.id for step in added]
            record.decided_at_utc = now
            logger.info("AI breakdown applied: %d steps, %d edited", len(added), edited)
            return BreakdownApplied(
                action=await self._action(row), generation_id=record.public_id, added=len(added)
            )

        try:
            return await commit_once(self._session, self._clock, run)
        except StaleDataError:
            raise BreakdownConflictError(TASK_CHANGED) from None

    async def dismiss(self, generation_id: str) -> None:
        """Turn a proposed generation down. The task is not read or changed; a generation
        already decided stays as it is."""

        async def run(now: datetime) -> None:
            record = await self._record(generation_id)
            if record is not None and record.outcome == BreakdownEnding.PROPOSED.value:
                record.outcome = BreakdownEnding.DISMISSED.value
                record.decided_at_utc = now

        await commit_once(self._session, self._clock, run)

    async def undo(self, generation_id: str, expected_revision: int) -> Action:
        """Remove exactly the steps an Apply added, as one revision recorded as an undo.

        Only while the task is still at the revision that Apply produced, which the caller
        saw; anything else raises BreakdownConflictError and the owner edits the steps by
        hand.
        """

        async def run(now: datetime) -> Action:
            record = await self._record(generation_id)
            if record is None:
                raise BreakdownNotFoundError(TASK_NOT_FOUND)
            if (
                record.outcome != BreakdownEnding.APPLIED.value
                or record.result_revision != expected_revision
            ):
                raise BreakdownConflictError(UNDO_REFUSED)
            row = await self._actions.get_action(record.task_public_id)
            if row is None or row.revision != expected_revision:
                raise BreakdownConflictError(UNDO_REFUSED)
            wanted = set(record.added_step_ids_json or [])
            mine = [step for step in await self._actions.step_rows(row.id) if step.id in wanted]
            if len(mine) != len(wanted):
                raise BreakdownConflictError(UNDO_REFUSED)
            for step in mine:
                await self._actions.delete_step(step)
            touch(row, now)
            await self._actions.record_activity(
                row, ActionActivityKind.UNDO, now, undo_of_revision=expected_revision
            )
            record.outcome = BreakdownEnding.UNDONE.value
            record.decided_at_utc = now
            logger.info("AI breakdown undone: %d steps removed", len(mine))
            return await self._action(row)

        try:
            return await commit_once(self._session, self._clock, run)
        except StaleDataError:
            raise BreakdownConflictError(UNDO_REFUSED) from None
