"""Canonical note links preserve writing and participate in revision conflict handling."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.drafts import DraftEdit, DraftKind
from mailbrief.domain.projects import ProjectEdit
from mailbrief.services.actions import ActionService
from mailbrief.services.drafts import DraftConflictError, DraftService
from mailbrief.services.projects import ProjectService
from tests.unit.services.test_manual_tasks import EDIT


async def test_link_unlink_preserves_text_versions_and_task(session: AsyncSession) -> None:
    writing = DraftService(session)
    note = await writing.create_note(
        DraftEdit(title="Notes", body="  Owner text\n"),
        command_id="11111111-1111-4111-8111-111111111111",
    )
    task = await ActionService(session).create(EDIT)
    project = await ProjectService(session).create_project(ProjectEdit(name="Work", notes=""))
    before = await writing.versions(note.public_id)
    linked = await writing.link_note(
        note.public_id,
        note.revision,
        project_public_id=project.public_id,
        action_public_id=task.public_id,
    )
    assert linked.content() == note.content()
    assert linked.revision == note.revision + 1
    assert linked.project_public_id == project.public_id
    assert linked.action_public_id == task.public_id
    assert await writing.versions(note.public_id) == before
    assert await ActionService(session).get(task.public_id) == task
    assert (
        await writing.link_note(
            linked.public_id,
            linked.revision,
            project_public_id=project.public_id,
            action_public_id=task.public_id,
        )
        == linked
    )
    with pytest.raises(DraftConflictError):
        await writing.autosave(note.public_id, note.revision, DraftEdit(body="stale text"))
    unlinked = await writing.link_note(
        linked.public_id, linked.revision, project_public_id=None, action_public_id=None
    )
    assert unlinked.project_public_id is unlinked.action_public_id is None
    assert unlinked.content() == note.content()


async def test_archived_missing_projects_and_non_notes_are_refused(session: AsyncSession) -> None:
    writing, projects = DraftService(session), ProjectService(session)
    note = await writing.create(DraftKind.NOTE)
    email = await writing.create(DraftKind.EMAIL)
    project = await projects.create_project(ProjectEdit(name="Work", notes=""))
    linked = await writing.link_note(
        note.public_id, note.revision, project_public_id=project.public_id, action_public_id=None
    )
    await projects.archive_project(project.public_id, project.revision, True)
    assert (
        await writing.link_note(
            linked.public_id,
            linked.revision,
            project_public_id=project.public_id,
            action_public_id=None,
        )
        == linked
    )
    for record, project_id in ((email, None), (note, "00000000-0000-4000-8000-000000000001")):
        with pytest.raises(DraftConflictError):
            await writing.link_note(
                record.public_id,
                record.revision,
                project_public_id=project_id,
                action_public_id=None,
            )
    assert await writing.get(linked.public_id) == linked
