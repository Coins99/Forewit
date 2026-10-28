"""Accept, dismiss and manage the owner's actions; every change is one committed revision.

Nothing here reads or sends email: suggestions come from stored analyses. Errors carry
static messages, and nothing logs action text.
"""

from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from mailbrief.domain.actions import (
    ACTION_STEP_MAX_CHARS,
    MAX_ACTION_STEPS,
    PRIORITY_RANK,
    Action,
    ActionActivityKind,
    ActionEdit,
    ActionFilter,
    ActionPriority,
    ActionStatus,
    DeadlineChange,
    DeadlineChangeKind,
    DeadlineOrigin,
    StepEdit,
    SuggestionState,
    ThreadLink,
    person_key,
    tag_key,
    waiting_since_for,
)
from mailbrief.domain.analysis import (
    ActionOwnership,
    ActionSuggestion,
    DeadlinePrecision,
    deadline_due_at,
)
from mailbrief.domain.common import new_public_id, utc_now
from mailbrief.domain.messages import ProviderKind
from mailbrief.domain.recurrence import RepeatEdit, RepeatRule
from mailbrief.services.deadlines import (
    ResolvedDeadline,
    due_day,
    owner_deadline,
    resolved,
    target_for_day,
)
from mailbrief.services.preferences import OwnerZone
from mailbrief.services.recurrence import RepeatError, Repeats
from mailbrief.services.writes import commit_once
from mailbrief.storage.actions import (
    ActionRepository,
    DecisionKey,
    stated_deadline,
    suggestion_from_row,
)
from mailbrief.storage.projects import ProjectRepository
from mailbrief.storage.tables import (
    AccountTable,
    ActionStepTable,
    ActionTable,
    MessageTable,
    SuggestionDecisionTable,
    deadline_columns,
)

_NOT_FOUND: Final = "That action was not found."
_SUGGESTION_NOT_FOUND: Final = "That suggestion was not found."
_STALE: Final = "The action changed since it was loaded; reload it and try again."
_UNDO_ADD: Final = "Only an unchanged addition can be undone."
_UNDO_STATUS: Final = "Only an unchanged completion, skip or reopening can be undone."
_NO_STATED: Final = "This task has no deadline from an email to go back to."
_CLOSED_PLAN: Final = "Reopen the task to change its plan."
_NO_STEP: Final = "That step is no longer in this task's plan."
_TOO_MANY_STEPS: Final = f"An action has at most {MAX_ACTION_STEPS} steps."
_STEP_TEXT: Final = f"A step needs 1 to {ACTION_STEP_MAX_CHARS} characters."
# The changes undo_status() reverses: each records the status before it.
_UNDOABLE: Final = frozenset(
    kind.value
    for kind in (
        ActionActivityKind.COMPLETED,
        ActionActivityKind.REOPENED,
        ActionActivityKind.SKIPPED,
    )
)
_LATEST: Final = datetime.max.replace(tzinfo=UTC)
COMPLETED_LIST_LIMIT: Final = 200  # The newest completed actions a list shows by default.
MAX_THREAD_LINKS: Final = 3  # The actions named for one email, most urgent first.


class ActionNotFoundError(LookupError):
    """No live action has that public ID."""


class SuggestionNotFoundError(LookupError):
    """No stored suggestion has that ID, or its row no longer validates."""


class ActionConflictError(ValueError):
    """The action changed since it was read, or the change is not allowed now."""


class ActionRepeatError(ActionConflictError):
    """The repeat settings can't be applied; the static message says why, for the owner."""


@dataclass(frozen=True, slots=True)
class DecisionSnapshot:
    """A suggestion's decision as it was before accept_into, for its undo to put back:
    ``decision`` is None when there was none (the suggestion was pending)."""

    decision: SuggestionState | None = None
    action_id: int | None = None
    decided_at_utc: datetime | None = None


@dataclass(frozen=True, slots=True)
class AcceptedInto:
    """What accept_into did: the action, whether the email became a new source of it,
    whether anything changed at all (repeating an addition changes nothing, and so has
    nothing to undo), and the decision it replaced."""

    action: Action
    source_added: bool
    changed: bool
    previous: DecisionSnapshot


def urgency(
    target_date: date | None,
    due_at_utc: datetime | None,
    priority: str | None,
    created_at_utc: datetime,
) -> tuple[date, datetime, int, datetime]:
    """How urgent an action is: by target date, then due time, then the owner's priority
    (none last), then creation, with undated actions last; so a priority orders work that has
    no date."""
    rank = PRIORITY_RANK[None if priority is None else ActionPriority(priority)]
    return (target_date or date.max, due_at_utc or _LATEST, rank, created_at_utc)


def action_order(action: Action) -> tuple[date, datetime, int, datetime]:
    """How open actions are ordered: urgency() for the action."""
    return urgency(action.target_date, action.due_at_utc(), action.priority, action.created_at_utc)


def touch(row: ActionTable, now: datetime) -> None:
    """Record a change to an action: one more revision, at ``now``."""
    row.revision += 1
    row.updated_at_utc = now


def _mark(step: ActionStepTable, done: bool, now: datetime) -> None:
    """A step that becomes done is timed now; it keeps its time while it stays done."""
    if not done:
        step.done, step.done_at_utc = False, None
    elif not step.done:
        step.done, step.done_at_utc = True, now


def _check_step_list(steps: Sequence[StepEdit]) -> list[int]:
    """The listed step IDs; raises ActionConflictError for too many steps or a repeated ID."""
    if len(steps) > MAX_ACTION_STEPS:
        raise ActionConflictError(_TOO_MANY_STEPS)
    listed = [edit.step_id for edit in steps if edit.step_id is not None]
    if len(listed) != len(set(listed)):
        raise ActionConflictError("Each step may appear only once.")
    return listed


def _set_ownership(row: ActionTable, ownership: ActionOwnership, now: datetime) -> None:
    """Give the action ``ownership`` (B08): one that becomes someone else's to do waits from
    ``now``, one that stays theirs keeps its wait, and one that becomes the owner's waits no
    longer."""
    if row.ownership != ownership.value or row.waiting_since_utc is None:
        row.waiting_since_utc = waiting_since_for(ownership, now)
    row.ownership = ownership.value


def _apply_edit(row: ActionTable, edit: ActionEdit, now: datetime) -> None:
    row.project_public_id = edit.project_public_id
    row.title = edit.title
    _set_ownership(row, edit.ownership, now)
    row.person = edit.person
    row.person_key = None if edit.person is None else person_key(edit.person)
    row.priority = None if edit.priority is None else edit.priority.value
    row.effort = None if edit.effort is None else edit.effort.value
    row.estimate_minutes = edit.estimate_minutes
    row.target_date = edit.target_date
    row.notes = edit.notes


def _set_deadline(row: ActionTable, deadline: ResolvedDeadline, origin: DeadlineOrigin) -> None:
    """Make ``deadline`` the action's current one, set by ``origin``."""
    row.deadline_text, row.deadline_precision = deadline.text, deadline.precision.value
    row.deadline_date, row.deadline_at_utc = deadline.date, deadline.at_utc
    row.deadline_timezone = deadline.timezone
    row.deadline_origin = origin.value


def _clear_deadline(row: ActionTable) -> None:
    row.deadline_text, row.deadline_precision = None, DeadlinePrecision.NONE.value
    row.deadline_date = row.deadline_at_utc = row.deadline_timezone = None
    row.deadline_origin = None


def _keep_stated(row: ActionTable) -> None:
    """Keep the email's deadline the owner is replacing or clearing, so they can go back."""
    row.stated_deadline_text = row.deadline_text
    row.stated_deadline_precision = row.deadline_precision
    row.stated_deadline_date = row.deadline_date
    row.stated_deadline_at_utc = row.deadline_at_utc
    row.stated_deadline_timezone = row.deadline_timezone


def _is_current(row: ActionTable, deadline: ResolvedDeadline) -> bool:
    """Whether ``deadline`` falls when the action's current one does."""
    return (
        row.deadline_precision,
        row.deadline_date,
        row.deadline_at_utc,
        row.deadline_timezone,
    ) == (deadline.precision.value, deadline.date, deadline.at_utc, deadline.timezone)


def _accepted_action_id(decision: SuggestionDecisionTable | None) -> int | None:
    if decision is None or decision.decision != SuggestionState.ACCEPTED.value:
        return None
    return decision.action_id


class ActionService:
    """The owner's accepted actions, and decisions on the suggestions they come from.

    Every mutating method reads the clock once, commits once and rolls back on failure.
    A method taking ``expected_revision`` raises ActionConflictError when the action's
    revision differs, so a stale screen never overwrites a newer change. ``zone`` dates
    repeating tasks' occurrences and the deadlines the owner sets; by default the owner's
    saved zone is read when one is needed (OwnerZone).
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Callable[[], datetime] = utc_now,
        id_factory: Callable[[], str] = new_public_id,
        zone: ZoneInfo | None = None,
    ) -> None:
        self._session = session
        self._clock = clock
        self._id_factory = id_factory
        self._repository = ActionRepository(session)
        self._zone = OwnerZone(session, zone)
        self._repeats = Repeats(session, self._repository, zone=self._zone, id_factory=id_factory)

    async def _apply_repeat(self, row: ActionTable, repeat: RepeatEdit, now: datetime) -> None:
        try:
            await self._repeats.apply(row, repeat, now)
        except RepeatError as error:
            raise ActionRepeatError(str(error)) from None

    async def _apply_deadline(
        self, row: ActionTable, change: DeadlineChange, now: datetime
    ) -> None:
        """Apply the owner's change to the deadline (B06). Replacing or clearing an email's
        deadline keeps it as the stated one, and the suggested target follows the new
        deadline, counted from today; the target date stays the owner's. A deadline that
        falls when the current one does changes nothing, so its origin stays."""
        origin = row.deadline_origin
        if change.kind is DeadlineChangeKind.CLEAR:
            if row.deadline_precision == DeadlinePrecision.NONE.value:
                return
            if origin == DeadlineOrigin.EMAIL.value:
                _keep_stated(row)
            _clear_deadline(row)
            row.suggested_target_date = row.target_reason = None
            return
        zone = await self._zone.get()
        if change.kind is DeadlineChangeKind.STATED:
            if origin == DeadlineOrigin.EMAIL.value:
                return  # It is the email's already.
            stated = stated_deadline(row)
            if stated is None:
                raise ActionConflictError(_NO_STATED)
            deadline, new_origin = resolved(stated), DeadlineOrigin.EMAIL
        else:
            assert change.due_date is not None  # DeadlineChange checks it.
            deadline = owner_deadline(change.due_date, change.due_time, zone)
            if _is_current(row, deadline):
                return
            if origin == DeadlineOrigin.EMAIL.value:
                _keep_stated(row)
            new_origin = DeadlineOrigin.OWNER
        _set_deadline(row, deadline, new_origin)
        due = due_day(deadline, zone)
        if due is None:  # Only the email's words: nothing to count back from.
            row.suggested_target_date = row.target_reason = None
            return
        target, reason = target_for_day(due, now.astimezone(zone).date())
        row.suggested_target_date, row.target_reason = target, reason.value

    async def _write[T](self, operation: Callable[[datetime], Awaitable[T]]) -> T:
        try:
            return await commit_once(self._session, self._clock, operation)
        except StaleDataError:
            raise ActionRepeatError(
                "The repeat changed since it was loaded; reload it and try again."
            ) from None

    async def _suggestion(
        self, suggestion_id: int
    ) -> tuple[ActionSuggestion, MessageTable, AccountTable, DecisionKey]:
        """The suggestion, its message and account, and the key its decision is kept under."""
        found = await self._repository.get_suggestion(suggestion_id)
        if found is None:
            raise SuggestionNotFoundError(_SUGGESTION_NOT_FOUND)
        row, message, account = found
        try:
            suggestion = suggestion_from_row(row)
        except ValueError:
            raise SuggestionNotFoundError(_SUGGESTION_NOT_FOUND) from None
        key = DecisionKey(
            provider=account.provider,
            provider_account_id=account.provider_account_id,
            provider_message_id=message.provider_message_id,
            fingerprint=suggestion.fingerprint,
        )
        return suggestion, message, account, key

    async def _live(self, public_id: str, expected_revision: int | None) -> ActionTable:
        row = await self._repository.get_action(public_id)
        if row is None:
            raise ActionNotFoundError(_NOT_FOUND)
        if expected_revision is not None and row.revision != expected_revision:
            raise ActionConflictError(_STALE)
        return row

    async def _load(self, row: ActionTable) -> Action:
        return (await self._repository.load([row]))[0]

    async def _apply_steps(
        self, row: ActionTable, steps: Sequence[StepEdit], listed: list[int], now: datetime
    ) -> None:
        """Update listed steps, add new ones and delete the rest, in the given order."""
        existing = {step.id: step for step in await self._repository.step_rows(row.id)}
        if not set(listed) <= existing.keys():
            raise ActionConflictError("A step does not belong to this action.")
        for position, edit in enumerate(steps):
            if edit.step_id is None:
                self._repository.add_step(row.id, position, edit.text, now if edit.done else None)
                continue
            step = existing[edit.step_id]
            step.position = position
            step.text = edit.text
            _mark(step, edit.done, now)
        for step_id, step in existing.items():
            if step_id not in listed:
                await self._repository.delete_step(step)

    async def _check_project(self, edit: ActionEdit, previous: str | None) -> None:
        if edit.project_public_id is None:
            return
        project = await ProjectRepository(self._session).project(edit.project_public_id)
        if project is None or (
            project.archived_at_utc is not None and project.public_id != previous
        ):
            raise ActionConflictError("Choose an active project for this task.")

    async def _apply_tags(self, row: ActionTable, edit: ActionEdit, now: datetime) -> None:
        """Give the action the edit's tags. A tag it adds is spelt as other live tasks already
        spell it, so one tag reads the same on every task; respelling a tag it already has
        respells it on every live task, one revision of each."""
        had = {tag.name_key: tag.name for tag in await self._repository.tag_rows(row.id)}
        spellings = await self._repository.tag_spellings(
            (key for key in map(tag_key, edit.tags) if key not in had), besides=row.id
        )
        names: list[str] = []
        respelt: dict[int, ActionTable] = {}
        for name in edit.tags:
            key = tag_key(name)
            if key not in had:
                names.append(spellings.get(key, name))
                continue
            names.append(name)
            if had[key] != name:
                for other in await self._repository.respell_tag(key, name, besides=row.id):
                    respelt[other.id] = other
        await self._repository.set_tags(row.id, names)
        for other in respelt.values():
            touch(other, now)
            await self._repository.record_activity(other, ActionActivityKind.TAG_RESPELLED, now)

    async def create(
        self,
        edit: ActionEdit,
        steps: Sequence[StepEdit] = (),
        repeat: RepeatRule | None = None,
        deadline: DeadlineChange | None = None,
    ) -> Action:
        """Create a task with no account or email source, using the same plan and workflow.
        With ``repeat`` it is the first occurrence of a new series (see Repeats.apply), and
        with ``deadline`` the owner sets its deadline (only a new one: it has no email's)."""

        async def run(now: datetime) -> Action:
            listed = _check_step_list(steps)
            if listed:
                raise ActionConflictError("New tasks cannot reuse existing steps.")
            await self._check_project(edit, None)
            row = ActionTable(
                public_id=self._id_factory(),
                title=edit.title,
                ownership=edit.ownership.value,
                status=ActionStatus.OPEN.value,
                deadline_precision=DeadlinePrecision.NONE.value,
                stated_deadline_precision=DeadlinePrecision.NONE.value,
                created_at_utc=now,
                updated_at_utc=now,
                revision=1,
            )
            _apply_edit(row, edit, now)
            if deadline is not None:
                await self._apply_deadline(row, deadline, now)
            await self._repository.add_action(row)
            await self._apply_tags(row, edit, now)
            await self._apply_steps(row, steps, listed, now)
            if repeat is not None:
                await self._apply_repeat(row, RepeatEdit(rule=repeat), now)
            await self._repository.record_activity(row, ActionActivityKind.CREATED, now)
            return await self._load(row)

        return await self._write(run)

    async def undo_status(self, public_id: str, expected_revision: int) -> Action:
        """Undo the latest completion, skip or reopening, retaining the original completion
        time. Undoing a repeating task's completion or skip withdraws the occurrence it made
        while that one is untouched."""

        async def run(now: datetime) -> Action:
            row = await self._live(public_id, expected_revision)
            latest = await self._repository.latest_activity(row.id)
            if (
                latest is None
                or latest.revision != expected_revision
                or latest.kind not in _UNDOABLE
                or latest.previous_status is None
            ):
                raise ActionConflictError(_UNDO_STATUS)
            previous_status, previous_completed = row.status, row.completed_at_utc
            row.status = latest.previous_status
            row.completed_at_utc = latest.previous_completed_at_utc
            if row.status == ActionStatus.OPEN.value:
                await self._repeats.withdraw(row)
            else:
                await self._repeats.follow(row, now)  # Undoing a reopening closes it again.
            touch(row, now)
            await self._repository.record_activity(
                row,
                ActionActivityKind.UNDO,
                now,
                previous_status=previous_status,
                previous_completed_at_utc=previous_completed,
                undo_of_revision=expected_revision,
            )
            return await self._load(row)

        return await self._write(run)

    async def accept(self, suggestion_id: int) -> Action:
        """Turn a suggestion into an action; accepting it again returns the same action.

        An action that was accepted and then soft-deleted is restored rather than copied.
        """

        async def run(now: datetime) -> Action:
            suggestion, message, account, key = await self._suggestion(suggestion_id)
            decision = await self._repository.get_decision(key)
            accepted_id = _accepted_action_id(decision)
            if accepted_id is not None:
                existing = await self._repository.get_action_by_id(accepted_id)
                if existing is not None:
                    if existing.deleted_at_utc is not None:
                        existing.deleted_at_utc = None
                        touch(existing, now)
                        await self._repository.record_activity(
                            existing, ActionActivityKind.RESTORED, now
                        )
                    return await self._load(existing)
            row = await self._repository.add_action(
                ActionTable(
                    public_id=self._id_factory(),
                    title=suggestion.title,
                    ownership=suggestion.ownership.value,
                    # It waits from now; whom it waits on is the owner's to say.
                    waiting_since_utc=waiting_since_for(suggestion.ownership, now),
                    status=ActionStatus.OPEN.value,
                    effort=None if suggestion.effort is None else suggestion.effort.value,
                    **deadline_columns(suggestion),
                    deadline_origin=(
                        None
                        if suggestion.deadline_precision is DeadlinePrecision.NONE
                        else DeadlineOrigin.EMAIL.value
                    ),
                    suggested_target_date=suggestion.suggested_target_date,
                    target_reason=(
                        None if suggestion.target_reason is None else suggestion.target_reason.value
                    ),
                    target_date=suggestion.suggested_target_date,
                    notes="",
                    evidence=suggestion.evidence,
                    created_at_utc=now,
                    updated_at_utc=now,
                    revision=1,
                )
            )
            await self._repository.record_activity(row, ActionActivityKind.CREATED, now)
            for position, text in enumerate(suggestion.steps):
                self._repository.add_step(row.id, position, text, None)
            await self._repository.add_source(row.id, message, account)
            await self._repository.save_decision(
                key, decision=SuggestionState.ACCEPTED, action_id=row.id, decided_at_utc=now
            )
            return await self._load(row)

        return await self._write(run)

    async def accept_into(
        self, suggestion_id: int, public_id: str, expected_revision: int
    ) -> AcceptedInto:
        """Accept a suggestion into an existing action instead of creating another, adding
        its email as a source unless it already is one; one revision.

        Only a live, open action takes it: a completed or deleted one raises
        ActionConflictError, so it is reopened first. The brief offers this for the open
        actions that track the email's thread; naming the action (``actions accept N
        --into``) allows any thread or account. Repeating it for the same action returns
        the action unchanged, even with an old revision, with ``changed`` false: there is
        nothing to undo then. Otherwise ``previous`` is the decision it replaced (none, a
        dismissal, or an acceptance into a deleted action), which undo_accept_into() puts
        back while the action is unchanged.
        """

        async def run(now: datetime) -> AcceptedInto:
            _, message, account, key = await self._suggestion(suggestion_id)
            target = await self._repository.get_action(public_id, include_deleted=True)
            if target is None:
                raise ActionNotFoundError(_NOT_FOUND)
            if target.deleted_at_utc is not None or target.status != ActionStatus.OPEN.value:
                raise ActionConflictError("Reopen the action before adding to it.")
            decision = await self._repository.get_decision(key)
            accepted_id = _accepted_action_id(decision)
            previous = (
                DecisionSnapshot()
                if decision is None
                else DecisionSnapshot(
                    decision=SuggestionState(decision.decision),
                    action_id=decision.action_id,
                    decided_at_utc=decision.decided_at_utc,
                )
            )
            if accepted_id == target.id:
                return AcceptedInto(
                    await self._load(target), source_added=False, changed=False, previous=previous
                )
            if accepted_id is not None:
                other = await self._repository.get_action_by_id(accepted_id)
                if other is not None and other.deleted_at_utc is None:
                    raise ActionConflictError("That suggestion already belongs to another action.")
            if target.revision != expected_revision:
                raise ActionConflictError(_STALE)
            previous_source_count = await self._repository.source_count(target.id)
            added = await self._repository.add_source(target.id, message, account)
            await self._repository.save_decision(
                key, decision=SuggestionState.ACCEPTED, action_id=target.id, decided_at_utc=now
            )
            touch(target, now)
            await self._repository.record_activity(
                target,
                ActionActivityKind.SOURCE_ADDED
                if added
                else ActionActivityKind.SUGGESTION_ACCEPTED,
                now,
                previous_source_count=previous_source_count,
            )
            return AcceptedInto(
                await self._load(target), source_added=added, changed=True, previous=previous
            )

        return await self._write(run)

    async def undo_accept_into(
        self,
        suggestion_id: int,
        public_id: str,
        expected_revision: int,
        remove_source: bool,
        *,
        previous: DecisionSnapshot,
    ) -> Action:
        """Reverse accept_into while the action is exactly as it left it; one revision.

        The suggestion's decision becomes exactly ``previous`` (accept_into's): none, so it
        is pending again, or the earlier dismissal or acceptance with its original time.
        With ``remove_source`` (accept_into's ``source_added``), its email stops being a
        source; an email-derived action keeps its last source, while a manual task returns
        to zero sources when the addition began there. Raises ActionConflictError unless
        the action is live, still at ``expected_revision``, and the suggestion is still
        accepted into it.
        """

        async def run(now: datetime) -> Action:
            _, message, _, key = await self._suggestion(suggestion_id)
            row = await self._repository.get_action(public_id)
            decision = await self._repository.get_decision(key)
            if (
                row is None
                or row.revision != expected_revision
                or _accepted_action_id(decision) != row.id
            ):
                raise ActionConflictError(_UNDO_ADD)
            if previous.decision is None or previous.decided_at_utc is None:
                await self._repository.delete_decision(key)
            else:
                await self._repository.save_decision(
                    key,
                    decision=previous.decision,
                    action_id=previous.action_id,
                    decided_at_utc=previous.decided_at_utc,
                )
            latest = await self._repository.latest_activity(row.id)
            manual_add = bool(
                latest is not None
                and latest.revision == expected_revision
                and latest.previous_source_count == 0
            )
            if remove_source and (manual_add or await self._repository.source_count(row.id) > 1):
                await self._repository.remove_source(row.id, message.provider_message_id)
            touch(row, now)
            await self._repository.record_activity(
                row, ActionActivityKind.UNDO, now, undo_of_revision=expected_revision
            )
            return await self._load(row)

        return await self._write(run)

    async def dismiss(self, suggestion_id: int) -> None:
        """Hide a suggestion from later briefs; repeating it changes nothing."""

        async def run(now: datetime) -> None:
            _, _, _, key = await self._suggestion(suggestion_id)
            decision = await self._repository.get_decision(key)
            accepted_id = _accepted_action_id(decision)
            if accepted_id is not None:
                action = await self._repository.get_action_by_id(accepted_id)
                if action is not None and action.deleted_at_utc is None:
                    raise ActionConflictError(
                        "An accepted suggestion can't be dismissed; delete its action instead."
                    )
                if action is not None:
                    return  # Already shown as dismissed; the link lets restore bring it back.
            elif decision is not None and decision.decision == SuggestionState.DISMISSED.value:
                return  # Already dismissed; keep the original time.
            await self._repository.save_decision(
                key, decision=SuggestionState.DISMISSED, action_id=None, decided_at_utc=now
            )

        await self._write(run)

    async def restore_suggestion(self, suggestion_id: int) -> None:
        """Undo a dismissal: the suggestion is pending again, or its deleted action returns."""

        async def run(now: datetime) -> None:
            _, _, _, key = await self._suggestion(suggestion_id)
            decision = await self._repository.get_decision(key)
            if decision is None:
                return
            accepted_id = _accepted_action_id(decision)
            action = (
                None
                if accepted_id is None
                else await self._repository.get_action_by_id(accepted_id)
            )
            if action is None:
                await self._repository.delete_decision(key)
            elif action.deleted_at_utc is not None:
                await self._restore_deleted(action, now)

        await self._write(run)

    async def unaccept(self, public_id: str, expected_revision: int) -> None:
        """Undo an acceptance while the action is still exactly as it was accepted."""

        async def run(now: datetime) -> None:
            row = await self._live(public_id, expected_revision)
            if (
                row.revision != 1
                or row.status != ActionStatus.OPEN.value
                or await self._repository.source_count(row.id) != 1
            ):
                raise ActionConflictError(
                    "Only an unchanged action accepted from one suggestion can be undone."
                )
            await self._repository.delete_decisions_for(row.id)
            row.deleted_at_utc = now
            touch(row, now)
            await self._repository.record_activity(
                row, ActionActivityKind.UNDO, now, undo_of_revision=1
            )

        await self._write(run)

    async def save(
        self,
        public_id: str,
        expected_revision: int,
        edit: ActionEdit,
        steps: Sequence[StepEdit] | None = None,
        repeat: RepeatEdit | None = None,
        deadline: DeadlineChange | None = None,
    ) -> Action:
        """Apply an edit and, when given, a new plan, repeat settings and deadline together,
        as one revision. Without ``repeat`` a repeating task's series is left as it was, and
        without ``deadline`` the deadline is (see _apply_deadline)."""

        async def run(now: datetime) -> Action:
            listed = [] if steps is None else _check_step_list(steps)
            row = await self._live(public_id, expected_revision)
            await self._check_project(edit, row.project_public_id)
            _apply_edit(row, edit, now)
            if deadline is not None:
                await self._apply_deadline(row, deadline, now)
            await self._apply_tags(row, edit, now)
            if steps is not None:
                await self._apply_steps(row, steps, listed, now)
            if repeat is not None:
                await self._apply_repeat(row, repeat, now)
            touch(row, now)
            await self._repository.record_activity(row, ActionActivityKind.EDITED, now)
            return await self._load(row)

        return await self._write(run)

    async def _open_plan(self, public_id: str, expected_revision: int) -> ActionTable:
        """The live, open action whose plan the owner changes in place (B04)."""
        row = await self._live(public_id, expected_revision)
        if row.status != ActionStatus.OPEN.value:
            raise ActionConflictError(_CLOSED_PLAN)
        return row

    async def set_step_done(
        self, public_id: str, expected_revision: int, step_id: int, done: bool
    ) -> Action:
        """Tick or untick one step of an open task (B04): one revision, recorded as an edit,
        as the editor's would be. A step that becomes done is timed now and keeps that time
        while it stays done. A step already as asked leaves the action as it is, without a
        revision; one of another action raises ActionConflictError."""

        async def run(now: datetime) -> Action:
            row = await self._open_plan(public_id, expected_revision)
            steps = {step.id: step for step in await self._repository.step_rows(row.id)}
            step = steps.get(step_id)
            if step is None:
                raise ActionConflictError(_NO_STEP)
            if step.done == done:
                return await self._load(row)
            _mark(step, done, now)
            touch(row, now)
            await self._repository.record_activity(row, ActionActivityKind.EDITED, now)
            return await self._load(row)

        return await self._write(run)

    async def add_step(self, public_id: str, expected_revision: int, text: str) -> Action:
        """Add a step, not yet done, after an open task's last one (B04): one revision,
        recorded as an edit. ``text`` is the owner's, stored trimmed; one that is blank or
        longer than ACTION_STEP_MAX_CHARS, or a step past MAX_ACTION_STEPS, raises
        ActionConflictError. A repeating task's series keeps its template: the step is this
        occurrence's alone, as an edit in the editor would be."""
        step_text = text.strip()
        if not 1 <= len(step_text) <= ACTION_STEP_MAX_CHARS:
            raise ActionConflictError(_STEP_TEXT)

        async def run(now: datetime) -> Action:
            row = await self._open_plan(public_id, expected_revision)
            steps = await self._repository.step_rows(row.id)
            if len(steps) >= MAX_ACTION_STEPS:
                raise ActionConflictError(_TOO_MANY_STEPS)
            position = max((step.position for step in steps), default=-1) + 1
            self._repository.add_step(row.id, position, step_text, None)
            touch(row, now)
            await self._repository.record_activity(row, ActionActivityKind.EDITED, now)
            return await self._load(row)

        return await self._write(run)

    async def complete(self, public_id: str, expected_revision: int) -> Action:
        """Complete an open action; its steps are left as they are. A repeating task's next
        occurrence is made with it (Repeats.follow)."""

        async def run(now: datetime) -> Action:
            row = await self._live(public_id, expected_revision)
            if row.status != ActionStatus.OPEN.value:
                raise ActionConflictError("The action is already completed.")
            return await self._close(row, ActionStatus.COMPLETED, now)

        return await self._write(run)

    async def skip(self, public_id: str, expected_revision: int) -> Action:
        """Skip an open occurrence of a repeating task: it closes without being done, and
        its series makes the next one. Only a task whose series still repeats is skipped."""

        async def run(now: datetime) -> Action:
            row = await self._live(public_id, expected_revision)
            if row.status != ActionStatus.OPEN.value:
                raise ActionConflictError("Only an open task can be skipped.")
            if not await self._repeats.repeating(row):
                raise ActionConflictError("Only a repeating task can be skipped.")
            return await self._close(row, ActionStatus.SKIPPED, now)

        return await self._write(run)

    async def _close(self, row: ActionTable, status: ActionStatus, now: datetime) -> Action:
        previous_status, previous_completed = row.status, row.completed_at_utc
        row.status = status.value
        row.completed_at_utc = now if status is ActionStatus.COMPLETED else None
        touch(row, now)
        await self._repository.record_activity(
            row,
            ActionActivityKind.COMPLETED
            if status is ActionStatus.COMPLETED
            else ActionActivityKind.SKIPPED,
            now,
            previous_status=previous_status,
            previous_completed_at_utc=previous_completed,
        )
        await self._repeats.follow(row, now)
        return await self._load(row)

    async def reopen(self, public_id: str, expected_revision: int) -> Action:
        """Reopen a completed or skipped action. A repeating task's next occurrence, made
        when it closed, is withdrawn while it is untouched (Repeats.withdraw)."""

        async def run(now: datetime) -> Action:
            row = await self._live(public_id, expected_revision)
            if row.status == ActionStatus.OPEN.value:
                raise ActionConflictError("The action is already open.")
            previous_status, previous_completed = row.status, row.completed_at_utc
            row.status = ActionStatus.OPEN.value
            row.completed_at_utc = None
            await self._repeats.withdraw(row)
            touch(row, now)
            await self._repository.record_activity(
                row,
                ActionActivityKind.REOPENED,
                now,
                previous_status=previous_status,
                previous_completed_at_utc=previous_completed,
            )
            return await self._load(row)

        return await self._write(run)

    async def delete(self, public_id: str, expected_revision: int) -> None:
        """Hide an action; restore() brings it back."""

        async def run(now: datetime) -> None:
            row = await self._live(public_id, expected_revision)
            row.deleted_at_utc = now
            touch(row, now)
            await self._repository.record_activity(row, ActionActivityKind.DELETED, now)

        await self._write(run)

    async def _restore_deleted(self, row: ActionTable, now: datetime) -> None:
        latest = await self._repository.latest_activity(row.id)
        deletion = (
            latest is not None
            and latest.revision == row.revision
            and latest.kind == ActionActivityKind.DELETED.value
            and not latest.legacy
        )
        previous_revision = row.revision
        row.deleted_at_utc = None
        # Its tags read as live tasks spell them now, which may have changed while it was gone.
        tags = await self._repository.tag_rows(row.id)
        spellings = await self._repository.tag_spellings(
            (tag.name_key for tag in tags), besides=row.id
        )
        await self._repository.set_tags(
            row.id, [spellings.get(tag.name_key, tag.name) for tag in tags]
        )
        touch(row, now)
        await self._repository.record_activity(
            row,
            ActionActivityKind.UNDO if deletion else ActionActivityKind.RESTORED,
            now,
            undo_of_revision=previous_revision if deletion else None,
        )

    async def restore(self, public_id: str) -> Action:
        """Bring back a deleted action; an action that isn't deleted is returned as it is."""

        async def run(now: datetime) -> Action:
            row = await self._repository.get_action(public_id, include_deleted=True)
            if row is None:
                raise ActionNotFoundError(_NOT_FOUND)
            if row.deleted_at_utc is not None:
                await self._restore_deleted(row, now)
            return await self._load(row)

        return await self._write(run)

    async def mark_thread_seen(self, public_id: str, expected_revision: int) -> Action:
        """Move the owner's "seen" watermark past the latest message in the action's threads
        (ADR 0015), from others or the owner.

        With nothing new, the action is returned unchanged, without a new revision. This is
        the only thing that changes an action because of its threads.
        """

        async def run(now: datetime) -> Action:
            row = await self._live(public_id, expected_revision)
            action = await self._load(row)
            thread = action.thread
            if thread is None or not thread.unseen:
                return action
            seen = [
                at for at in (thread.latest_at_utc, thread.owner_replied_at_utc) if at is not None
            ]
            row.thread_seen_until_utc = max(seen)
            touch(row, now)
            await self._repository.record_activity(row, ActionActivityKind.THREAD_SEEN, now)
            return await self._load(row)

        return await self._write(run)

    async def thread_links(
        self, account_email: str, message_keys: Iterable[str]
    ) -> dict[str, tuple[ThreadLink, ...]]:
        """The live, open actions that continue each email's thread (ADR 0015).

        ``message_keys`` are Gmail message IDs of ``account_email``'s cached messages. An
        action continues a message's thread when one of its sources has the same provider,
        account and thread snapshot. Each message gets at most MAX_THREAD_LINKS, most urgent
        first; one that is not cached, has no thread or has no such action gets no entry.
        The number of queries does not grow with the number of messages or actions.
        """
        rows = await self._repository.thread_link_rows(
            ProviderKind.GMAIL.value, account_email, message_keys
        )
        found: dict[
            str, dict[int, tuple[tuple[date, datetime, int, datetime, int], ThreadLink]]
        ] = {}
        for row in rows:
            due = deadline_due_at(
                DeadlinePrecision(row.deadline_precision),
                row.deadline_date,
                row.deadline_at_utc,
                row.deadline_timezone,
            )
            links = found.setdefault(row.message_key, {})
            earlier = links.get(row.action_id)
            links[row.action_id] = (
                (*urgency(row.target_date, due, row.priority, row.created_at_utc), row.action_id),
                ThreadLink(
                    public_id=row.public_id,
                    title=row.title,
                    revision=row.revision,
                    ownership=ActionOwnership(row.ownership),
                    is_source=row.is_source or (earlier is not None and earlier[1].is_source),
                ),
            )
        ranked: dict[str, tuple[ThreadLink, ...]] = {}
        for key, links in found.items():
            ordered = sorted(links.values(), key=lambda pair: pair[0])
            ranked[key] = tuple(link for _, link in ordered[:MAX_THREAD_LINKS])
        return ranked

    async def get(self, public_id: str) -> Action:
        """A live action; deleted actions are not found."""
        return await self._load(await self._live(public_id, None))

    async def list_actions(
        self,
        view: ActionFilter,
        *,
        limit: int | None = None,
        group: str = "all",
        group_id: str | None = None,
    ) -> tuple[Action, ...]:
        """Live actions for a view.

        Open and waiting actions come in action_order() (target date, due time, priority,
        creation, with undated ones last), and are never capped unless ``limit`` is given.
        Completed actions come newest first, at most COMPLETED_LIST_LIMIT unless ``limit``
        says otherwise; count_actions() says how many there are.
        """
        if limit is not None and limit < 1:
            raise ValueError("limit must be at least 1")
        if limit is None and view is ActionFilter.COMPLETED:
            limit = COMPLETED_LIST_LIMIT
        actions = await self._repository.load(
            await self._repository.list_rows(view, limit=limit, group=group, group_id=group_id)
        )
        if view is ActionFilter.COMPLETED:
            return tuple(actions)
        # The rows arrive in ID order and sorted() is stable, so equal keys keep that order.
        ordered = sorted(actions, key=action_order)
        return tuple(ordered if limit is None else ordered[:limit])

    async def list_tags(self) -> tuple[str, ...]:
        """Every tag on a live task, once each, sorted; the Actions filter offers them."""
        return await self._repository.tag_names()

    async def list_people(self, view: ActionFilter | None = None) -> tuple[str, ...]:
        """The people live actions wait on or are for, a view's when given, once each and
        sorted (B08); the Actions filter offers them, and ``group="person"`` lists one's
        actions."""
        return await self._repository.people(view)

    async def count_actions(
        self, view: ActionFilter, *, group: str = "all", group_id: str | None = None
    ) -> int:
        """How many live actions a view holds, however many list_actions() shows."""
        return await self._repository.count_rows(view, group=group, group_id=group_id)
