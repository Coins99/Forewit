"""A note's project and task links (ADR 0027): revisioned, owner-only and durable."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import ActionFilter
from mailbrief.domain.drafts import DraftEdit, DraftKind, DraftSummary, WritingView
from mailbrief.domain.projects import AreaEdit, Project, ProjectEdit
from mailbrief.services.actions import ActionService
from mailbrief.services.data import (
    CleanupKind,
    CleanupRequest,
    apply_cleanup,
    export_writing,
    preview_cleanup,
)
from mailbrief.services.drafts import (
    DraftConflictError,
    DraftNotFoundError,
    DraftService,
    NoteLinkError,
)
from mailbrief.services.projects import ProjectService
from mailbrief.storage.backup import create_backup
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import upgrade_database
from mailbrief.storage.recovery import restore_backup
from mailbrief.storage.tables import DraftTable, DraftVersionTable, ProjectTable
from tests.factories import Clock
from tests.unit.services.test_manual_tasks import EDIT

START = datetime(2026, 11, 4, 9, 0, tzinfo=UTC)


@pytest.fixture
def clock() -> Clock:
    return Clock(START)


@pytest.fixture
def drafts(session: AsyncSession, clock: Clock) -> DraftService:
    return DraftService(session, clock=clock)


async def project(session: AsyncSession, name: str = "Home", **edit: object) -> Project:
    return await ProjectService(session).create_project(ProjectEdit(name=name, **edit))


async def archived(session: AsyncSession, name: str = "Old") -> Project:
    created = await project(session, name)
    return await ProjectService(session).archive_project(
        created.public_id, created.revision, archived=True
    )


async def versions(session: AsyncSession) -> int:
    return (await session.scalar(select(func.count()).select_from(DraftVersionTable))) or 0


# Projects


async def test_a_note_starts_in_a_project_and_reads_its_current_name(
    session: AsyncSession, drafts: DraftService
) -> None:
    home = await project(session)
    note = await drafts.create(DraftKind.NOTE, project_public_id=home.public_id)
    assert (note.project_public_id, note.project_name, note.revision) == (
        home.public_id,
        "Home",
        1,
    )

    await ProjectService(session).save_project(
        home.public_id, home.revision, ProjectEdit(name="House")
    )

    again = await drafts.get(note.public_id)
    assert (again.project_name, again.revision) == ("House", 1)
    (summary,) = await drafts.list_summaries(WritingView.NOTES)
    assert (summary.project_public_id, summary.project_name) == (home.public_id, "House")


async def test_a_new_note_needs_a_project_that_takes_notes(
    session: AsyncSession, drafts: DraftService
) -> None:
    old = await archived(session)
    home = await project(session)
    with pytest.raises(NoteLinkError, match="archived or no longer available"):
        await drafts.create(DraftKind.NOTE, project_public_id=old.public_id)
    with pytest.raises(NoteLinkError, match="archived or no longer available"):
        await drafts.create(DraftKind.NOTE, project_public_id="9" * 36)
    with pytest.raises(NoteLinkError, match="Only notes"):
        await drafts.create(DraftKind.EMAIL, project_public_id=home.public_id)
    assert await drafts.list_summaries() == ()


async def test_set_project_is_one_revision_without_a_version(
    session: AsyncSession, drafts: DraftService, clock: Clock
) -> None:
    home = await project(session)
    note = await drafts.create(DraftKind.NOTE)
    saved = await drafts.autosave(note.public_id, 1, DraftEdit(title="Ideas", body="Paint"))
    clock.advance(minutes=5)

    linked = await drafts.set_project(note.public_id, saved.revision, home.public_id)

    assert linked.revision == saved.revision + 1
    assert linked.updated_at_utc == START + timedelta(minutes=5)
    assert (linked.title, linked.body, linked.project_name) == ("Ideas", "Paint", "Home")
    assert await versions(session) == 1
    clock.advance(minutes=5)
    same = await drafts.set_project(note.public_id, linked.revision, home.public_id)
    assert same == linked  # Nothing changed, so no revision and no new time.
    cleared = await drafts.set_project(note.public_id, linked.revision, None)
    assert (cleared.project_public_id, cleared.project_name) == (None, None)
    assert cleared.revision == linked.revision + 1
    with pytest.raises(DraftConflictError):
        await drafts.set_project(note.public_id, linked.revision, home.public_id)


async def test_a_note_keeps_its_archived_project_but_takes_no_new_one(
    session: AsyncSession, drafts: DraftService
) -> None:
    home = await project(session)
    note = await drafts.create(DraftKind.NOTE, project_public_id=home.public_id)
    await ProjectService(session).archive_project(home.public_id, home.revision, archived=True)
    old = await archived(session, "Older")

    kept = await drafts.set_project(note.public_id, note.revision, home.public_id)
    assert kept == await drafts.get(note.public_id)
    assert kept.project_public_id == home.public_id
    with pytest.raises(NoteLinkError):
        await drafts.set_project(note.public_id, note.revision, old.public_id)
    moved = await drafts.set_project(note.public_id, note.revision, None)
    with pytest.raises(NoteLinkError):  # Once it leaves, it can't go back.
        await drafts.set_project(note.public_id, moved.revision, home.public_id)


async def test_only_live_notes_take_links(session: AsyncSession, drafts: DraftService) -> None:
    home = await project(session)
    task = await ActionService(session).create(EDIT)
    email = await drafts.create(DraftKind.EMAIL)
    with pytest.raises(NoteLinkError, match="Only notes"):
        await drafts.set_project(email.public_id, email.revision, home.public_id)
    with pytest.raises(NoteLinkError, match="Only notes"):
        await drafts.set_task(email.public_id, email.revision, task.public_id)
    note = await drafts.create(DraftKind.NOTE)
    await drafts.delete(note.public_id, note.revision)
    with pytest.raises(DraftNotFoundError):
        await drafts.set_project(note.public_id, note.revision + 1, home.public_id)
    with pytest.raises(DraftNotFoundError):
        await drafts.set_task(note.public_id, note.revision + 1, task.public_id)


async def test_removing_an_area_or_renaming_never_changes_notes(
    session: AsyncSession, drafts: DraftService
) -> None:
    projects = ProjectService(session)
    area = await projects.create_area(AreaEdit(name="Life"))
    home = await project(session, area_public_id=area.public_id)
    note = await drafts.create(DraftKind.NOTE, project_public_id=home.public_id)
    await projects.delete_area(area.public_id, area.revision)
    assert await drafts.get(note.public_id) == note


# Tasks


async def test_set_task_links_with_a_title_snapshot_and_never_changes_the_task(
    session: AsyncSession, drafts: DraftService
) -> None:
    actions = ActionService(session)
    task = await actions.create(EDIT)
    note = await drafts.create(DraftKind.NOTE)

    linked = await drafts.set_task(note.public_id, note.revision, task.public_id)

    assert (linked.action_public_id, linked.action_title) == (task.public_id, "Plan the week")
    assert linked.revision == 2
    assert await actions.get(task.public_id) == task
    assert await drafts.set_task(note.public_id, linked.revision, task.public_id) == linked
    renamed = await actions.save(
        task.public_id, task.revision, EDIT.model_copy(update={"title": "Plan the month"})
    )
    refreshed = await drafts.set_task(note.public_id, linked.revision, renamed.public_id)
    assert (refreshed.action_title, refreshed.revision) == ("Plan the month", 3)
    unlinked = await drafts.set_task(note.public_id, refreshed.revision, None)
    assert (unlinked.action_public_id, unlinked.action_title, unlinked.revision) == (None, None, 4)
    assert await drafts.set_task(note.public_id, unlinked.revision, None) == unlinked
    assert await versions(session) == 1


async def test_a_deleted_task_is_refused_and_a_kept_link_survives_a_project_change(
    session: AsyncSession, drafts: DraftService
) -> None:
    actions = ActionService(session)
    task = await actions.create(EDIT)
    gone = await actions.create(EDIT.model_copy(update={"title": "Gone"}))
    await actions.delete(gone.public_id, gone.revision)
    note = await drafts.create_for_action(task.public_id, DraftKind.NOTE)
    with pytest.raises(NoteLinkError, match="deleted"):
        await drafts.set_task(note.public_id, note.revision, gone.public_id)

    await actions.delete(task.public_id, task.revision)
    home = await project(session)
    moved = await drafts.set_project(note.public_id, note.revision, home.public_id)
    assert (moved.action_public_id, moved.action_title) == (None, "Plan the week")
    await actions.restore(task.public_id)
    assert (await drafts.get(note.public_id)).action_public_id == task.public_id
    # Unlinking the snapshot of a deleted task is still a change.
    await actions.delete(task.public_id, (await actions.get(task.public_id)).revision)
    unlinked = await drafts.set_task(note.public_id, moved.revision, None)
    assert unlinked.action_title is None and unlinked.revision == moved.revision + 1


async def test_title_matches_lists_live_tasks_open_first(
    session: AsyncSession, clock: Clock
) -> None:
    actions = ActionService(session, clock=clock)
    done = await actions.create(EDIT.model_copy(update={"title": "Write report"}))
    await actions.complete(done.public_id, done.revision)
    clock.advance(minutes=1)
    open_task = await actions.create(EDIT.model_copy(update={"title": "Write letter"}))
    gone = await actions.create(EDIT.model_copy(update={"title": "Write poem"}))
    await actions.delete(gone.public_id, gone.revision)

    assert [task.public_id for task in await actions.title_matches("write")] == [
        open_task.public_id,
        done.public_id,
    ]
    assert [task.title for task in await actions.title_matches("Write report")] == ["Write report"]
    assert await actions.title_matches("w") == ()  # Too short to search.
    assert len(await actions.title_matches("")) == 2


async def test_title_matches_stops_at_fifty(session: AsyncSession) -> None:
    actions = ActionService(session)
    for number in range(51):
        await actions.create(EDIT.model_copy(update={"title": f"Task {number}"}))
    assert len(await actions.title_matches("Task")) == 50


# Lists, copies and durability


async def test_views_and_project_groups_filter_in_storage(
    session: AsyncSession, drafts: DraftService, clock: Clock
) -> None:
    home = await project(session)
    work = await project(session, "Work")
    in_home = await drafts.create(DraftKind.NOTE, project_public_id=home.public_id)
    clock.advance(minutes=1)
    loose = await drafts.create(DraftKind.NOTE)
    clock.advance(minutes=1)
    email = await drafts.create(DraftKind.EMAIL)
    message = await drafts.create(DraftKind.MESSAGE)

    def ids(found: tuple[DraftSummary, ...]) -> list[str]:
        return [summary.public_id for summary in found]

    assert ids(await drafts.list_summaries(WritingView.NOTES)) == [
        loose.public_id,
        in_home.public_id,
    ]
    assert set(ids(await drafts.list_summaries(WritingView.DRAFTS))) == {
        email.public_id,
        message.public_id,
    }
    assert len(await drafts.list_summaries()) == 4
    assert ids(await drafts.list_summaries(WritingView.NOTES, group="unassigned")) == [
        loose.public_id
    ]
    assert ids(
        await drafts.list_summaries(WritingView.NOTES, group="project", group_id=home.public_id)
    ) == [in_home.public_id]
    assert (
        await drafts.list_summaries(WritingView.NOTES, group="project", group_id=work.public_id)
        == ()
    )
    with pytest.raises(ValueError, match="needs a project"):
        await drafts.list_summaries(WritingView.NOTES, group="project")


def test_each_kind_has_one_view() -> None:
    assert WritingView.NOTES.kinds == {DraftKind.NOTE}
    assert WritingView.DRAFTS.kinds == {DraftKind.REPLY, DraftKind.EMAIL, DraftKind.MESSAGE}
    assert {kind: WritingView.of(kind) for kind in DraftKind} == {
        DraftKind.NOTE: WritingView.NOTES,
        DraftKind.REPLY: WritingView.DRAFTS,
        DraftKind.EMAIL: WritingView.DRAFTS,
        DraftKind.MESSAGE: WritingView.DRAFTS,
    }


async def test_save_as_new_keeps_the_project_unless_it_was_archived(
    session: AsyncSession, drafts: DraftService
) -> None:
    home = await project(session)
    note = await drafts.create(DraftKind.NOTE, project_public_id=home.public_id)
    copy = await drafts.save_as_new(note.public_id, DraftEdit(body="Copy"))
    assert copy.project_public_id == home.public_id
    await ProjectService(session).archive_project(home.public_id, home.revision, archived=True)
    later = await drafts.save_as_new(note.public_id, DraftEdit(body="Later"))
    assert later.project_public_id is None


async def test_links_survive_export_backup_restore_and_purge(tmp_path: Path) -> None:
    path = tmp_path / "owner.sqlite3"
    await asyncio.to_thread(upgrade_database, path)
    database = Database.from_path(path)
    async with database.session() as session:
        home = await project(session)
        task = await ActionService(session).create(EDIT)
        service = DraftService(session, clock=Clock(START))
        note = await service.create(DraftKind.NOTE, project_public_id=home.public_id)
        note = await service.set_task(note.public_id, note.revision, task.public_id)
        old = await service.create(DraftKind.NOTE, project_public_id=home.public_id)
        await service.delete(old.public_id, old.revision)
    exported = tmp_path / "writing.json"
    await export_writing(database, exported)
    records = json.loads(exported.read_text(encoding="utf-8"))["records"]
    assert {row["public_id"]: row["project_public_id"] for row in records["drafts"]} == {
        note.public_id: home.public_id,
        old.public_id: home.public_id,
    }

    preview = await preview_cleanup(
        database, CleanupRequest(CleanupKind.DELETED, before=START + timedelta(days=1))
    )
    assert (preview.drafts, preview.actions) == (1, 0)
    await apply_cleanup(database, preview)
    async with database.session() as session:
        assert await session.get(ProjectTable, 1) is not None
        rows = list(await session.scalars(select(DraftTable.public_id)))
        assert rows == [note.public_id]
    await database.dispose()

    backup = tmp_path / "backup.zip"
    await asyncio.to_thread(create_backup, path, backup)
    restored_path = tmp_path / "restored.sqlite3"
    await asyncio.to_thread(restore_backup, backup, restored_path)
    restored = Database.from_path(restored_path)
    try:
        async with restored.session() as session:
            assert await DraftService(session).get(note.public_id) == note
            assert await ActionService(session).count_actions(ActionFilter.OPEN) == 1
    finally:
        await restored.dispose()
