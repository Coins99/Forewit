"""Repeating tasks (B05): a series' rule and template, and the occurrences made from them.

A series has one open occurrence at a time in the ordinary course. Completing or skipping it
makes the next one, dated the rule's first date after both the occurrence's own date and today,
so dates missed in between are never created. The next occurrence starts from the series'
template, never from the occurrence just closed: editing one occurrence changes only it, unless
the owner chooses to change later repeats too. Reopening an occurrence, or undoing its
completion or skip, withdraws the occurrence it made while that one is untouched.

Everything here runs inside the caller's transaction, so a change and the occurrence it makes
commit together. Errors carry static messages, and nothing logs task text.
"""

from collections.abc import Callable
from datetime import date, datetime
from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import (
    ActionActivityKind,
    ActionStatus,
    person_key,
    tag_key,
    waiting_since_for,
)
from mailbrief.domain.analysis import ActionOwnership, DeadlinePrecision
from mailbrief.domain.recurrence import (
    ENDS_FIRST,
    RepeatEdit,
    RepeatRule,
    ends_before,
    first_occurrence,
    next_occurrence,
    weekday_mask,
)
from mailbrief.services.preferences import OwnerZone
from mailbrief.storage.actions import ActionRepository, series_rule
from mailbrief.storage.projects import ProjectRepository
from mailbrief.storage.tables import ActionSeriesTable, ActionTable

_REOPEN: Final = "Reopen the task to change how it repeats."
_STALE: Final = "The repeat changed since it was loaded; reload it and try again."
# What the owner's own changes leave on a task; a task with no other activity is untouched.
_UNTOUCHED: Final = frozenset(
    {ActionActivityKind.CREATED.value, ActionActivityKind.TAG_RESPELLED.value}
)
_TEMPLATE_FIELDS: Final = (
    "project_public_id",
    "title",
    "ownership",
    "person",
    "priority",
    "effort",
    "estimate_minutes",
    "notes",
)


class RepeatError(ValueError):
    """A repeat change that isn't allowed now; the message says why."""


def _set_rule(series: ActionSeriesTable, rule: RepeatRule) -> None:
    series.frequency = rule.frequency.value
    series.weekday_mask = weekday_mask(rule.weekdays)
    series.month_day = rule.month_day
    series.end_date = rule.end_date


class Repeats:
    """The repeat side of the owner's task changes, in the caller's session.

    ``zone`` dates occurrences (OwnerZone: by default the owner's saved zone)."""

    def __init__(
        self,
        session: AsyncSession,
        repository: ActionRepository,
        *,
        zone: OwnerZone,
        id_factory: Callable[[], str],
    ) -> None:
        self._session = session
        self._repository = repository
        self._zone = zone
        self._id_factory = id_factory

    async def _today(self, now: datetime) -> date:
        """The owner's local date at ``now``."""
        return now.astimezone(await self._zone.get()).date()

    async def _series(self, row: ActionTable) -> ActionSeriesTable | None:
        if row.series_id is None or row.occurrence_date is None:
            return None
        return await self._repository.get_series(row.series_id)

    async def repeating(self, row: ActionTable) -> bool:
        """Whether ``row`` is an occurrence of a series that still repeats."""
        series = await self._series(row)
        return series is not None and series.ended_at_utc is None

    async def _template(self, row: ActionTable) -> dict[str, object]:
        """What a later occurrence would start with, from the task as it now is."""
        fields: dict[str, object] = {name: getattr(row, name) for name in _TEMPLATE_FIELDS}
        fields["steps_json"] = [step.text for step in await self._repository.step_rows(row.id)]
        fields["tags_json"] = [tag.name for tag in await self._repository.tag_rows(row.id)]
        return fields

    async def apply(self, row: ActionTable, edit: RepeatEdit, now: datetime) -> bool:
        """Apply the owner's repeat settings to a task whose edit is already applied; whether
        its series changed. A task that didn't repeat starts a series, as its first occurrence:
        dated its target date, or else the rule's first date from today, which becomes its
        target. A rule of None stops a series. Raises RepeatError for a stale series (an edit
        of a repeating task must carry the series revision it saw), a change to a closed
        task's repeat, or a rule that ends before the task's date."""
        series = await self._series(row)
        if series is None:
            if edit.rule is None:
                return False
            return await self._start(row, edit.rule, now)
        if edit.series_revision != series.revision:
            raise RepeatError(_STALE)
        is_open = row.status == ActionStatus.OPEN.value
        changed = False
        if edit.rule is None:
            if series.ended_at_utc is None:
                if not is_open:
                    raise RepeatError(_REOPEN)
                series.ended_at_utc = now
                changed = True
        else:
            if series.ended_at_utc is not None or series_rule(series) != edit.rule:
                if not is_open:
                    raise RepeatError(_REOPEN)
                assert row.occurrence_date is not None  # _series() found the series by it.
                if ends_before(edit.rule, row.occurrence_date):
                    raise RepeatError(ENDS_FIRST)
                _set_rule(series, edit.rule)
                series.ended_at_utc = None
                changed = True
            if edit.later_too:
                template = await self._template(row)
                if any(getattr(series, name) != value for name, value in template.items()):
                    if not is_open:
                        raise RepeatError(_REOPEN)
                    for name, value in template.items():
                        setattr(series, name, value)
                    changed = True
        if changed:
            series.revision += 1
            series.updated_at_utc = now
        return changed

    async def _start(self, row: ActionTable, rule: RepeatRule, now: datetime) -> bool:
        if row.status != ActionStatus.OPEN.value:
            raise RepeatError(_REOPEN)
        day = first_occurrence(rule, row.target_date, await self._today(now))
        if day is None:
            raise RepeatError(ENDS_FIRST)
        series = ActionSeriesTable(
            public_id=self._id_factory(),
            created_at_utc=now,
            updated_at_utc=now,
            revision=1,
            **await self._template(row),
        )
        _set_rule(series, rule)
        await self._repository.add_series(series)
        row.series_id = series.id
        row.occurrence_date = day
        row.target_date = day
        return True

    async def follow(self, row: ActionTable, now: datetime) -> ActionTable | None:
        """Make the occurrence after ``row``, which the owner just completed or skipped; None
        when its series stopped or ended, when ``row`` already has a live next occurrence (one
        the owner changed outlives a reopening), or when the next date already has one (even
        a deleted one, which the owner can restore). So repeating this makes one at most."""
        series = await self._series(row)
        if series is None or series.ended_at_utc is not None or row.occurrence_date is None:
            return None
        if any(item.deleted_at_utc is None for item in await self._repository.followers(row.id)):
            return None
        day = next_occurrence(series_rule(series), row.occurrence_date, await self._today(now))
        if day is None or await self._repository.occurrence(series.id, day) is not None:
            return None
        project = series.project_public_id
        if project is not None:
            found = await ProjectRepository(self._session).project(project)
            if found is None or found.archived_at_utc is not None:
                project = None  # An archived project takes no new tasks.
        follower = await self._repository.add_action(
            ActionTable(
                public_id=self._id_factory(),
                project_public_id=project,
                title=series.title,
                ownership=series.ownership,
                # Each occurrence waits from when it is made, like a task created then.
                waiting_since_utc=waiting_since_for(ActionOwnership(series.ownership), now),
                person=series.person,
                person_key=None if series.person is None else person_key(series.person),
                status=ActionStatus.OPEN.value,
                priority=series.priority,
                effort=series.effort,
                estimate_minutes=series.estimate_minutes,
                target_date=day,
                notes=series.notes,
                deadline_precision=DeadlinePrecision.NONE.value,
                created_at_utc=now,
                updated_at_utc=now,
                revision=1,
                series_id=series.id,
                occurrence_date=day,
                follows_action_id=row.id,
            )
        )
        names = [str(name) for name in series.tags_json]
        spellings = await self._repository.tag_spellings(map(tag_key, names), besides=None)
        await self._repository.set_tags(
            follower.id, [spellings.get(tag_key(name), name) for name in names]
        )
        for position, text in enumerate(series.steps_json):
            self._repository.add_step(follower.id, position, str(text), None)
        await self._repository.record_activity(follower, ActionActivityKind.CREATED, now)
        return follower

    async def withdraw(self, row: ActionTable) -> int:
        """Remove the occurrences ``row`` made that the owner never touched: live, open, with
        no change of their own since they were made (another task respelling a tag they share
        doesn't count) and no draft linked to them. One the owner touched stays; it is theirs
        now."""
        removed = 0
        for follower in await self._repository.followers(row.id):
            if (
                follower.deleted_at_utc is None
                and follower.status == ActionStatus.OPEN.value
                and await self._untouched(follower)
            ):
                await self._repository.delete_action(follower)
                removed += 1
        return removed

    async def _untouched(self, row: ActionTable) -> bool:
        activity = await self._repository.activity_rows(row.id)
        if any(event.kind not in _UNTOUCHED for event in activity):
            return False
        return not await self._repository.drafted(row.id)
