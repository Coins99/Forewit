"""B05's repeating tasks: a series' occurrences are ordinary tasks, one made as each closes."""

import asyncio
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from alembic import command
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from mailbrief.domain.actions import Action, ActionEdit, ActionFilter, ActionStatus, StepEdit
from mailbrief.domain.actions import ActionActivityKind as Kind
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.drafts import DraftKind
from mailbrief.domain.preferences import PreferencesEdit
from mailbrief.domain.projects import ProjectEdit
from mailbrief.domain.recurrence import RepeatEdit, RepeatFrequency, RepeatRule
from mailbrief.services.actions import ActionConflictError, ActionRepeatError, ActionService
from mailbrief.services.calendar import resolve_timezone
from mailbrief.services.data import export_writing
from mailbrief.services.drafts import DraftService
from mailbrief.services.preferences import PreferencesService
from mailbrief.services.projects import ProjectService
from mailbrief.storage.backup import create_backup
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import migration_config, upgrade_database
from mailbrief.storage.recovery import restore_backup
from mailbrief.storage.tables import ActionActivityTable, ActionSeriesTable, ActionTable
from tests.factories import Clock

START = datetime(2026, 10, 15, 13, 0, tzinfo=UTC)  # A Thursday; 09:00 in Toronto.
TORONTO = ZoneInfo("America/Toronto")
TODAY = date(2026, 10, 15)
DAILY = RepeatRule(frequency=RepeatFrequency.DAILY)
MONDAY_WEDNESDAY = RepeatRule(frequency=RepeatFrequency.WEEKLY, weekdays=(0, 2))
DAY_31 = RepeatRule(frequency=RepeatFrequency.MONTHLY_DAY, month_day=31)

TASK = ActionEdit(
    title="Water plants",
    ownership=ActionOwnership.MINE,
    effort=None,
    priority=None,
    tags=("Home",),
    estimate_minutes=15,
    target_date=None,
    notes="The fern too.",
)
STEPS = (StepEdit(step_id=None, text="Fill the can", done=True),)


def task(**changes: Any) -> ActionEdit:
    return ActionEdit.model_validate(TASK.model_dump() | changes)


def same(action: Action, **changes: Any) -> ActionEdit:
    """The edit that leaves ``action`` as it is, with ``changes``."""
    fields: dict[str, Any] = {
        "title": action.title,
        "notes": action.notes,
        "tags": action.tags,
        "target_date": action.target_date,
        "estimate_minutes": action.estimate_minutes,
    }
    return task(**(fields | changes))


def kept(action: Action) -> list[StepEdit]:
    return [StepEdit(step_id=s.step_id, text=s.text, done=s.done) for s in action.steps]


def service(session: AsyncSession, clock: Clock) -> ActionService:
    return ActionService(session, clock=clock, zone=TORONTO)


async def open_tasks(actions: ActionService) -> list[Action]:
    return list(await actions.list_actions(ActionFilter.OPEN))


async def only_open(actions: ActionService) -> Action:
    found = await open_tasks(actions)
    assert len(found) == 1, found
    return found[0]


async def test_a_new_repeating_task_is_dated_by_its_rule(session: AsyncSession) -> None:
    actions = service(session, Clock(START))
    created = await actions.create(task(), STEPS, MONDAY_WEDNESDAY)
    assert created.repeat is not None
    assert created.repeat.rule == MONDAY_WEDNESDAY
    # Undated, it takes the rule's first date from today: Monday 19 October.
    assert created.repeat.occurrence_date == created.target_date == date(2026, 10, 19)
    assert created.repeat.series_revision == 1
    assert created.repeat.previous_date is None
    assert [event.kind for event in created.activity] == [Kind.CREATED]

    dated = await actions.create(task(target_date=date(2026, 10, 16)), (), DAILY)
    assert dated.repeat is not None
    assert dated.repeat.occurrence_date == date(2026, 10, 16)  # Its own date comes first.


async def test_completing_makes_the_next_from_the_template(session: AsyncSession) -> None:
    clock = Clock(START)
    actions = service(session, clock)
    first = await actions.create(task(), STEPS, DAILY)
    assert first.target_date == TODAY

    done = await actions.complete(first.public_id, first.revision)

    assert done.status is ActionStatus.COMPLETED
    following = await only_open(actions)
    assert following.public_id != first.public_id
    assert following.repeat is not None
    assert following.repeat.occurrence_date == following.target_date == date(2026, 10, 16)
    assert following.repeat.previous_date == TODAY
    assert following.repeat.previous_status is ActionStatus.COMPLETED
    assert (following.title, following.notes, following.tags, following.estimate_minutes) == (
        "Water plants",
        "The fern too.",
        ("Home",),
        15,
    )
    assert [(step.text, step.done) for step in following.steps] == [("Fill the can", False)]
    assert following.revision == 1
    assert [event.kind for event in following.activity] == [Kind.CREATED]
    assert (await actions.get(first.public_id)).status is ActionStatus.COMPLETED


async def test_a_late_completion_skips_the_missed_dates(session: AsyncSession) -> None:
    """Left for a week, a daily task comes back once, dated tomorrow."""
    clock = Clock(START)
    actions = service(session, clock)
    first = await actions.create(task(), (), DAILY)
    clock.advance(days=7)

    await actions.complete(first.public_id, first.revision)

    following = await only_open(actions)
    assert following.target_date == date(2026, 10, 23)
    count = await session.scalar(select(func.count()).select_from(ActionTable))
    assert count == 2


async def test_the_owner_zone_dates_the_next_occurrence(session: AsyncSession) -> None:
    """At 22:00 in Toronto it is already tomorrow in UTC; the next date counts from Toronto."""
    clock = Clock(datetime(2026, 10, 16, 2, 0, tzinfo=UTC))
    actions = service(session, clock)
    first = await actions.create(task(target_date=date(2026, 10, 10)), (), DAILY)
    await actions.complete(first.public_id, first.revision)
    assert (await only_open(actions)).target_date == date(2026, 10, 16)


async def test_skipping_closes_without_completing(session: AsyncSession) -> None:
    actions = service(session, Clock(START))
    first = await actions.create(task(), (), DAILY)

    skipped = await actions.skip(first.public_id, first.revision)

    assert skipped.status is ActionStatus.SKIPPED
    assert skipped.completed_at_utc is None
    assert skipped.activity[-1].kind is Kind.SKIPPED
    following = await only_open(actions)
    assert following.target_date == date(2026, 10, 16)
    assert following.repeat is not None
    assert following.repeat.previous_status is ActionStatus.SKIPPED
    # A skipped occurrence is in neither list; its own history keeps it.
    assert await actions.list_actions(ActionFilter.COMPLETED) == ()
    with pytest.raises(ActionConflictError, match="open task"):
        await actions.skip(first.public_id, skipped.revision)

    plain = await actions.create(task(title="Once"))
    with pytest.raises(ActionConflictError, match="repeating task"):
        await actions.skip(plain.public_id, plain.revision)


async def test_undo_withdraws_the_untouched_next(session: AsyncSession) -> None:
    clock = Clock(START)
    actions = service(session, clock)
    first = await actions.create(task(), (), DAILY)
    done = await actions.complete(first.public_id, first.revision)
    clock.advance(minutes=1)

    undone = await actions.undo_status(first.public_id, done.revision)

    assert undone.status is ActionStatus.OPEN
    assert [action.public_id for action in await open_tasks(actions)] == [first.public_id]

    skipped = await actions.skip(first.public_id, undone.revision)
    restored = await actions.undo_status(first.public_id, skipped.revision)
    assert restored.status is ActionStatus.OPEN
    assert [action.public_id for action in await open_tasks(actions)] == [first.public_id]
    assert [event.kind for event in restored.activity][-4:] == [
        Kind.COMPLETED,
        Kind.UNDO,
        Kind.SKIPPED,
        Kind.UNDO,
    ]


async def test_reopening_withdraws_only_an_untouched_next(session: AsyncSession) -> None:
    clock = Clock(START)
    actions = service(session, clock)
    first = await actions.create(task(), (), DAILY)
    done = await actions.complete(first.public_id, first.revision)

    reopened = await actions.reopen(first.public_id, done.revision)
    assert [action.public_id for action in await open_tasks(actions)] == [first.public_id]
    undone = await actions.undo_status(first.public_id, reopened.revision)  # Closed again.
    assert undone.status is ActionStatus.COMPLETED
    following = next(a for a in await open_tasks(actions) if a.public_id != first.public_id)

    # The owner changes the next one; it is theirs, and outlives a reopening.
    changed = await actions.save(following.public_id, 1, same(following, title="Water ferns"))
    again = await actions.reopen(first.public_id, undone.revision)
    assert {a.public_id for a in await open_tasks(actions)} == {
        first.public_id,
        changed.public_id,
    }
    # Closing it again makes no second next occurrence.
    await actions.complete(first.public_id, again.revision)
    assert [a.public_id for a in await open_tasks(actions)] == [changed.public_id]


async def test_an_edit_changes_only_its_occurrence_unless_asked(session: AsyncSession) -> None:
    clock = Clock(START)
    actions = service(session, clock)
    first = await actions.create(task(), STEPS, DAILY)
    assert first.repeat is not None
    only_this = RepeatEdit(rule=DAILY, series_revision=1, later_too=False)
    edited = await actions.save(
        first.public_id,
        first.revision,
        same(first, title="Water plants and fern", target_date=date(2026, 10, 17)),
        [StepEdit(step_id=None, text="Check the soil", done=False)],
        only_this,
    )
    # Moving the target date reschedules this occurrence; it is still the 15th's.
    assert edited.repeat is not None
    assert edited.repeat.occurrence_date == TODAY
    assert edited.repeat.series_revision == 1
    done = await actions.complete(edited.public_id, edited.revision)
    following = await only_open(actions)
    assert following.title == "Water plants"  # The template, not the edit.
    assert [step.text for step in following.steps] == ["Fill the can"]
    assert following.target_date == date(2026, 10, 16)
    assert done.repeat is not None

    later_too = RepeatEdit(rule=DAILY, series_revision=1, later_too=True)
    renamed = await actions.save(
        following.public_id,
        following.revision,
        same(following, title="Water the fern", target_date=date(2026, 10, 20), tags=()),
        kept(following),
        later_too,
    )
    assert renamed.repeat is not None
    assert renamed.repeat.series_revision == 2
    await actions.complete(renamed.public_id, renamed.revision)
    third = await only_open(actions)
    assert (third.title, third.tags) == ("Water the fern", ())
    assert third.target_date == date(2026, 10, 17)  # The target date never goes to the series.


async def test_a_stale_series_or_a_closed_task_is_refused(session: AsyncSession) -> None:
    actions = service(session, Clock(START))
    first = await actions.create(task(), (), DAILY)
    weekly = RepeatEdit(rule=MONDAY_WEDNESDAY, series_revision=1)
    changed = await actions.save(first.public_id, first.revision, same(first), None, weekly)
    assert changed.repeat is not None
    assert changed.repeat.rule == MONDAY_WEDNESDAY
    assert changed.repeat.occurrence_date == TODAY  # A new rule doesn't move this one.

    with pytest.raises(ActionConflictError, match="repeat changed"):
        await actions.save(first.public_id, changed.revision, same(first), None, weekly)
    # A repeating task's edit must carry the series revision it saw.
    with pytest.raises(ActionConflictError, match="repeat changed"):
        await actions.save(
            first.public_id, changed.revision, same(first), None, RepeatEdit(rule=DAILY)
        )
    done = await actions.complete(first.public_id, changed.revision)
    for edit in (
        RepeatEdit(rule=DAILY, series_revision=2),
        RepeatEdit(rule=None, series_revision=2),  # Not even to stop it.
    ):
        with pytest.raises(ActionConflictError, match="Reopen"):
            await actions.save(first.public_id, done.revision, same(done), None, edit)
    with pytest.raises(ActionConflictError, match="Reopen"):
        await actions.save(
            first.public_id,
            done.revision,
            same(done, title="Renamed"),
            None,
            RepeatEdit(rule=MONDAY_WEDNESDAY, series_revision=2, later_too=True),
        )
    plain = await actions.complete((await actions.create(task())).public_id, 1)
    with pytest.raises(ActionConflictError, match="Reopen"):
        await actions.save(plain.public_id, plain.revision, same(plain), None, weekly)


async def test_a_cached_series_cannot_overwrite_another_sessions_edit(database: Database) -> None:
    async with database.session() as setup:
        actions = service(setup, Clock(START))
        first = await actions.create(task(), (), DAILY)
        done = await actions.complete(first.public_id, first.revision)
        following = await only_open(actions)
        # Editing the successor keeps it when the earlier occurrence is reopened.
        following = await actions.save(following.public_id, following.revision, same(following))
        first = await actions.reopen(first.public_id, done.revision)

    async with database.session() as stale, database.session() as fresh:
        cached = await stale.scalar(select(ActionSeriesTable))
        assert cached is not None
        await stale.commit()  # expire_on_commit=False deliberately retains this object.
        assert cached.revision == 1
        await service(fresh, Clock(START)).save(
            first.public_id,
            first.revision,
            same(first, title="Fresh template"),
            None,
            RepeatEdit(rule=DAILY, series_revision=1),
        )
        with pytest.raises(ActionRepeatError, match="repeat changed"):
            await service(stale, Clock(START)).save(
                following.public_id,
                following.revision,
                same(following, title="Stale template"),
                None,
                RepeatEdit(rule=DAILY, series_revision=1),
            )

    async with database.session() as check:
        series = await check.scalar(select(ActionSeriesTable))
        assert series is not None
        assert (series.title, series.revision) == ("Fresh template", 2)
        unchanged = await service(check, Clock(START)).get(following.public_id)
        assert (unchanged.title, unchanged.revision) == (following.title, following.revision)


async def test_series_update_checks_the_revision_at_flush(database: Database) -> None:
    async with database.session() as setup:
        await service(setup, Clock(START)).create(task(), (), DAILY)
    async with database.session() as stale, database.session() as fresh:
        old = await stale.scalar(select(ActionSeriesTable))
        current = await fresh.scalar(select(ActionSeriesTable))
        assert old is not None and current is not None
        await stale.commit()
        current.title, current.revision = "Fresh template", 2
        await fresh.commit()
        old.title, old.revision = "Stale template", 2
        with pytest.raises(StaleDataError):
            await stale.commit()
        await stale.rollback()
    async with database.session() as check:
        saved = await check.scalar(select(ActionSeriesTable))
        assert saved is not None
        assert (saved.title, saved.revision) == ("Fresh template", 2)


async def test_a_flush_conflict_rolls_back_and_reports_a_repeat_conflict(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session, "commit", AsyncMock(side_effect=StaleDataError()))
    with pytest.raises(ActionRepeatError, match="repeat changed"):
        await service(session, Clock(START)).create(task(), (), DAILY)
    assert await session.scalar(select(func.count()).select_from(ActionTable)) == 0
    assert await session.scalar(select(func.count()).select_from(ActionSeriesTable)) == 0


async def test_stopping_and_restarting_the_repeats(session: AsyncSession) -> None:
    actions = service(session, Clock(START))
    first = await actions.create(task(), (), DAILY)
    stopped = await actions.save(
        first.public_id, first.revision, same(first), None, RepeatEdit(rule=None, series_revision=1)
    )
    assert stopped.repeat is not None
    assert stopped.repeat.ended
    with pytest.raises(ActionConflictError, match="repeating task"):
        await actions.skip(stopped.public_id, stopped.revision)
    resumed = await actions.save(
        stopped.public_id,
        stopped.revision,
        same(stopped),
        None,
        RepeatEdit(rule=DAILY, series_revision=2),
    )
    assert resumed.repeat is not None
    assert not resumed.repeat.ended
    stopped_again = await actions.save(
        resumed.public_id,
        resumed.revision,
        same(resumed),
        None,
        RepeatEdit(rule=None, series_revision=3),
    )
    await actions.complete(stopped_again.public_id, stopped_again.revision)
    assert await open_tasks(actions) == []


async def test_the_end_date_is_the_last_occurrence(session: AsyncSession) -> None:
    actions = service(session, Clock(START))
    until = DAILY.model_copy(update={"end_date": date(2026, 10, 16)})
    first = await actions.create(task(), (), until)
    await actions.complete(first.public_id, first.revision)
    second = await only_open(actions)
    assert second.target_date == date(2026, 10, 16)
    await actions.complete(second.public_id, second.revision)
    assert await open_tasks(actions) == []

    early = DAILY.model_copy(update={"end_date": date(2026, 10, 14)})
    with pytest.raises(ActionRepeatError, match="ends before"):
        await actions.create(task(), (), early)
    # Undated, it would take November 1, after an end on October 20.
    first_of_month = RepeatRule(
        frequency=RepeatFrequency.MONTHLY_DAY, month_day=1, end_date=date(2026, 10, 20)
    )
    with pytest.raises(ActionRepeatError, match="ends before"):
        await actions.create(task(), (), first_of_month)
    late = await actions.create(task(target_date=date(2026, 10, 20)))
    with pytest.raises(ActionConflictError, match="ends before"):
        await actions.save(late.public_id, late.revision, same(late), None, RepeatEdit(rule=until))
    started = await actions.create(task(), (), DAILY)
    with pytest.raises(ActionConflictError, match="ends before"):
        await actions.save(
            started.public_id,
            started.revision,
            same(started),
            None,
            RepeatEdit(rule=early, series_revision=1),
        )


async def test_january_31_comes_back_in_march(session: AsyncSession) -> None:
    actions = service(session, Clock(datetime(2027, 1, 31, 15, 0, tzinfo=UTC)))
    first = await actions.create(task(), (), DAY_31)
    assert first.target_date == date(2027, 1, 31)
    await actions.complete(first.public_id, first.revision)
    assert (await only_open(actions)).target_date == date(2027, 3, 31)


async def test_an_existing_date_makes_no_second_occurrence(session: AsyncSession) -> None:
    """The owner deleted the next one: completing the earlier one again doesn't recreate it,
    and restoring brings it back."""
    clock = Clock(START)
    actions = service(session, clock)
    first = await actions.create(task(), (), DAILY)
    done = await actions.complete(first.public_id, first.revision)
    following = await only_open(actions)
    await actions.delete(following.public_id, following.revision)
    reopened = await actions.reopen(first.public_id, done.revision)  # The deleted one stays.
    await actions.complete(first.public_id, reopened.revision)
    assert await open_tasks(actions) == []
    restored = await actions.restore(following.public_id)
    assert [action.public_id for action in await open_tasks(actions)] == [restored.public_id]


async def test_the_closed_occurrence_names_the_next_it_made(session: AsyncSession) -> None:
    actions = service(session, Clock(START))
    first = await actions.create(task(), (), DAILY)
    assert first.repeat is not None and first.repeat.next_date is None
    done = await actions.complete(first.public_id, first.revision)
    assert done.repeat is not None and done.repeat.next_date == date(2026, 10, 16)

    # Once the next one is deleted, closing again makes none, and names none.
    following = await only_open(actions)
    await actions.delete(following.public_id, following.revision)
    reopened = await actions.reopen(first.public_id, done.revision)
    again = await actions.complete(first.public_id, reopened.revision)
    assert again.repeat is not None and again.repeat.next_date is None


async def test_a_next_occurrence_with_a_draft_is_kept(session: AsyncSession) -> None:
    """Linking a draft doesn't change the task, but the draft makes it the owner's."""
    clock = Clock(START)
    actions = service(session, clock)
    first = await actions.create(task(), (), DAILY)
    done = await actions.complete(first.public_id, first.revision)
    following = await only_open(actions)
    draft = await DraftService(session, clock=clock).create_for_action(
        following.public_id, DraftKind.NOTE
    )

    await actions.undo_status(first.public_id, done.revision)

    assert {a.public_id for a in await open_tasks(actions)} == {
        first.public_id,
        following.public_id,
    }
    kept_draft = await DraftService(session, clock=clock).get(draft.public_id)
    assert kept_draft.action_public_id == following.public_id


async def test_a_tag_respelt_elsewhere_leaves_the_next_untouched(session: AsyncSession) -> None:
    actions = service(session, Clock(START))
    first = await actions.create(task(), (), DAILY)
    await actions.complete(first.public_id, first.revision)
    other = await actions.create(task(title="Other", tags=("Home",)))
    await actions.save(other.public_id, other.revision, same(other, tags=("HOME",)))
    following = next(a for a in await open_tasks(actions) if a.title == "Water plants")
    assert following.revision == 2
    assert [event.kind for event in following.activity] == [Kind.CREATED, Kind.TAG_RESPELLED]

    # The respelling changed the completed task too, so reopen it rather than undo.
    await actions.reopen(first.public_id, (await actions.get(first.public_id)).revision)

    assert {a.public_id for a in await open_tasks(actions)} == {
        first.public_id,
        other.public_id,
    }  # The next occurrence was withdrawn.


async def test_the_owner_zone_is_read_when_none_is_given(session: AsyncSession) -> None:
    """At 16:00 UTC it is already the 16th in Tokyo, the owner's saved zone."""
    clock = Clock(datetime(2026, 10, 15, 16, 0, tzinfo=UTC))
    await PreferencesService(session, clock=clock).save(PreferencesEdit(time_zone="Asia/Tokyo"), 0)
    actions = ActionService(session, clock=clock)
    created = await actions.create(task(), (), DAILY)
    assert created.target_date == date(2026, 10, 16)


async def test_unreadable_preferences_date_by_the_system_zone(session: AsyncSession) -> None:
    await PreferencesService(session).save(PreferencesEdit(), 0)
    await session.execute(text("UPDATE owner_preferences SET time_zone = 'Mars/Base'"))
    await session.commit()
    actions = ActionService(session, clock=Clock(START))
    created = await actions.create(task(), (), DAILY)
    assert created.target_date == START.astimezone(resolve_timezone(None)).date()


async def test_an_archived_project_takes_no_new_occurrence(session: AsyncSession) -> None:
    clock = Clock(START)
    projects = ProjectService(session, clock=clock)
    project = await projects.create_project(ProjectEdit(name="Garden", notes=""))
    actions = service(session, clock)
    first = await actions.create(task(project_public_id=project.public_id), (), DAILY)
    await projects.archive_project(project.public_id, project.revision, True)
    await actions.complete(first.public_id, first.revision)
    assert (await only_open(actions)).project is None


async def test_a_template_tag_takes_the_spelling_in_use(session: AsyncSession) -> None:
    actions = service(session, Clock(START))
    first = await actions.create(task(), (), DAILY)
    other = await actions.create(task(title="Other", tags=("Home",)))
    await actions.save(other.public_id, other.revision, same(other, tags=("HOME",)))
    await actions.complete(first.public_id, (await actions.get(first.public_id)).revision)
    following = next(a for a in await open_tasks(actions) if a.title == "Water plants")
    assert following.tags == ("HOME",)


async def test_repeating_tasks_survive_backup_restore_and_export(tmp_path: Path) -> None:
    path = tmp_path / "owner.sqlite3"
    await asyncio.to_thread(upgrade_database, path)
    database = Database.from_path(path)
    async with database.session() as session:
        actions = service(session, Clock(START))
        first = await actions.create(task(), STEPS, MONDAY_WEDNESDAY)
        await actions.skip(first.public_id, first.revision)
    await asyncio.to_thread(create_backup, path, tmp_path / "backup.zip")
    await export_writing(database, tmp_path / "export.json")
    await database.dispose()

    payload = json.loads((tmp_path / "export.json").read_text(encoding="utf-8"))
    (series,) = payload["records"]["action_series"]
    assert (series["frequency"], series["weekday_mask"]) == ("weekly", 0b101)
    assert series["steps_json"] == ["Fill the can"]
    assert [row["status"] for row in payload["records"]["actions"]] == ["skipped", "open"]

    restored = tmp_path / "restored.sqlite3"
    await asyncio.to_thread(restore_backup, tmp_path / "backup.zip", restored)
    database = Database.from_path(restored)
    async with database.session() as session:
        following = await only_open(ActionService(session))
        assert following.repeat is not None
        assert following.repeat.occurrence_date == date(2026, 10, 21)
        assert following.repeat.previous_status is ActionStatus.SKIPPED
        rows = await session.scalars(select(ActionSeriesTable))
        assert [row.revision for row in rows] == [1]
    await database.dispose()


async def test_downgrade_keeps_repeat_history_readable_by_older_versions(tmp_path: Path) -> None:
    path = tmp_path / "downgrade.sqlite3"
    config = migration_config(path)
    # Simulate an owner database that has already used the immutable 0021 revision: made by
    # this version, then taken back to 0022, whose schema is 0021's.
    await asyncio.to_thread(command.upgrade, config, "head")
    database = Database.from_path(path)
    clock = Clock(START)
    async with database.session() as session:
        actions = service(session, clock)
        first = await actions.create(task(), (), DAILY)
        skipped = await actions.skip(first.public_id, first.revision)
        clock.advance(minutes=10)
        reopened = await actions.reopen(first.public_id, skipped.revision)
        await actions.undo_status(first.public_id, reopened.revision)
        other = await actions.create(task(title="Other"))
        await actions.save(other.public_id, other.revision, same(other, tags=("HOME",)))
        before = list((await session.execute(select(ActionActivityTable.__table__))).mappings())
        assert any(row["kind"] == "tag_respelled" for row in before)
        assert any(row["previous_status"] == "skipped" for row in before)
    await database.dispose()
    await asyncio.to_thread(command.downgrade, config, "20261016_0022")

    await asyncio.to_thread(command.upgrade, config, "head")
    database = Database.from_path(path)
    async with database.session() as session:
        after = list((await session.execute(select(ActionActivityTable.__table__))).mappings())
        assert after == before  # Upgrading must preserve the original audit trail.
    await database.dispose()
    await asyncio.to_thread(command.downgrade, config, "20261014_0020")

    # Read only 0020 columns, and enforce its enum vocabulary as its loader does.
    database = Database.from_path(path)
    async with database.session() as session:
        records = (await session.execute(text("SELECT * FROM action_activity"))).mappings().all()
        assert len(records) == len(before)
        older_kinds = {kind.value for kind in Kind} - {"skipped", "tag_respelled"}
        for record in records:
            assert record["kind"] in older_kinds
            assert record["previous_status"] in (None, "open", "completed")
            if record["previous_status"] == "completed":
                # The reopening points to the skip's time, not the later reopening time.
                assert str(record["previous_completed_at_utc"]).startswith("2026-10-15 13:00:00")
        tasks = (await session.execute(text("SELECT status, completed_at_utc FROM actions"))).all()
        for status, completed_at in tasks:
            assert status in ("open", "completed")
            assert (completed_at is not None) == (status == "completed")
    await database.dispose()
