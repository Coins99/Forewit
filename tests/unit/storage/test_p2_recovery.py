"""Owner relationships and explicit capture state survive separate-profile recovery."""

import asyncio
import json
from datetime import date
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

from mailbrief.domain.drafts import DraftEdit
from mailbrief.domain.projects import ProjectEdit
from mailbrief.services.actions import ActionService
from mailbrief.services.capture import CaptureService
from mailbrief.services.data import export_writing
from mailbrief.services.day_plans import DayPlanService
from mailbrief.services.drafts import DraftService
from mailbrief.services.projects import ProjectService
from mailbrief.storage.backup import create_backup
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import upgrade_database
from mailbrief.storage.recovery import restore_backup
from tests.unit.services.test_manual_tasks import EDIT


async def test_linked_note_capture_and_selection_restore_without_network(tmp_path: Path) -> None:
    original, restored = tmp_path / "source.sqlite3", tmp_path / "restored.sqlite3"
    await asyncio.to_thread(upgrade_database, original)
    database = Database.from_path(original)
    command = str(uuid4())
    day = date(2026, 11, 3)
    async with database.session() as session:
        task = await ActionService(session).create(EDIT, capture_pending=True)
        project = await ProjectService(session).create_project(
            ProjectEdit(name="Project", notes="Context")
        )
        note = await DraftService(session).create_note(
            DraftEdit(body=" Owner writing\n"), command_id=command, capture_pending=True
        )
        linked = await DraftService(session).link_note(
            note.public_id,
            note.revision,
            project_public_id=project.public_id,
            action_public_id=task.public_id,
        )
        await CaptureService(session).keep("note", linked.public_id, linked.revision)
        plan = await DayPlanService(session).select(day, ZoneInfo("UTC"), task.public_id, 0)
        versions = await DraftService(session).versions(note.public_id)
        saved_note = await DraftService(session).get(note.public_id)
    writing = tmp_path / "writing.json"
    await export_writing(database, writing)
    exported = json.loads(writing.read_text(encoding="utf-8"))
    assert project.public_id in json.dumps(exported)
    archive = tmp_path / "backup.zip"
    await asyncio.to_thread(create_backup, original, archive)
    await database.dispose()
    await asyncio.to_thread(restore_backup, archive, restored)
    recovered = Database.from_path(restored)
    try:
        async with recovered.session() as session:
            assert await DraftService(session).get(note.public_id) == saved_note
            assert await DraftService(session).versions(note.public_id) == versions
            assert await DayPlanService(session).load(day) == plan
            assert [record.public_id for record in await CaptureService(session).inbox()] == [
                task.public_id
            ]
            replay = await DraftService(session).create_note(
                DraftEdit(body=" Owner writing\n"), command_id=command, capture_pending=True
            )
            assert replay == saved_note
            await ActionService(session).delete(task.public_id, task.revision)
            after = await DraftService(session).get(note.public_id)
            assert after.body == saved_note.body and after.action_public_id is None
            assert after.project_public_id == project.public_id
    finally:
        await recovered.dispose()
