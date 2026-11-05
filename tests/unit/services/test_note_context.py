"""Notes retain #101 link semantics while #102 capture review changes independently."""

from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.drafts import DraftEdit
from mailbrief.domain.projects import ProjectEdit
from mailbrief.services.actions import ActionService
from mailbrief.services.capture import CaptureService
from mailbrief.services.drafts import DraftService
from mailbrief.services.projects import ProjectService
from tests.unit.services.test_manual_tasks import EDIT


async def test_project_edit_and_review_keep_a_recoverable_task_link(session: AsyncSession) -> None:
    writing, actions = DraftService(session), ActionService(session)
    note = await writing.create_note(
        DraftEdit(body="Owner text"),
        command_id="11111111-1111-4111-8111-111111111111",
        capture_pending=True,
    )
    task = await actions.create(EDIT)
    project = await ProjectService(session).create_project(ProjectEdit(name="Work"))
    linked = await writing.set_task(note.public_id, note.revision, task.public_id)
    versions = await writing.versions(note.public_id)
    await actions.delete(task.public_id, task.revision)
    moved = await writing.set_project(linked.public_id, linked.revision, project.public_id)
    assert moved.action_public_id is None
    assert moved.action_title == task.title
    kept = await CaptureService(session).keep("note", moved.public_id, moved.revision)
    assert kept.capture_processed_at_utc is not None
    await actions.restore(task.public_id)
    restored = await writing.get(note.public_id)
    assert restored.action_public_id == task.public_id
    assert restored.project_public_id == project.public_id
    assert restored.body == note.body
    assert await writing.versions(note.public_id) == versions
