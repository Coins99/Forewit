"""Projects/areas preserve owner work, grouping history and recovery without Gmail."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from mailbrief.domain.actions import ActionEdit, ActionFilter
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.projects import AreaEdit, ProjectEdit
from mailbrief.services.actions import ActionConflictError, ActionService
from mailbrief.services.data import export_writing
from mailbrief.services.projects import ProjectConflictError, ProjectService
from mailbrief.storage.backup import create_backup
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import upgrade_database
from mailbrief.storage.projects import ProjectRepository
from mailbrief.storage.recovery import restore_backup
from mailbrief.storage.tables import LifeAreaTable, ProjectTable

TASK = ActionEdit(
    title="Finish proposal",
    ownership=ActionOwnership.MINE,
    effort=None,
    priority=None,
    tags=(),
    estimate_minutes=None,
    target_date=None,
    notes="Owner plan",
)


async def test_organization_lifecycle_history_and_recovery(tmp_path: Path) -> None:
    path = tmp_path / "owner.sqlite3"
    await asyncio.to_thread(upgrade_database, path)
    db = Database.from_path(path)
    async with db.session() as session:
        projects, tasks = ProjectService(session), ActionService(session)
        area = await projects.create_area(AreaEdit(name="Work", notes="Long-term commitments"))
        area = await projects.save_area(
            area.public_id, 1, AreaEdit(name="Career", notes="Work notes")
        )
        project = await projects.create_project(
            ProjectEdit(name="Launch", area_public_id=area.public_id)
        )
        project = await projects.save_project(
            project.public_id,
            1,
            ProjectEdit(name="Spring launch", notes="Outcome", area_public_id=area.public_id),
        )
        task = await tasks.create(TASK.model_copy(update={"project_public_id": project.public_id}))
        assert task.project is not None and task.project.area_name == "Career"
        done = await tasks.complete(task.public_id, 1)
        assert done.activity[-1].project_public_id == project.public_id
        assert done.activity[-1].area_public_id == area.public_id
        archived = await projects.archive_project(project.public_id, 2, True)
        assert (await projects.archive_project(project.public_id, 3, True)) == archived
        assert (
            await tasks.count_actions(
                ActionFilter.COMPLETED, group="project", group_id=project.public_id
            )
            == 1
        )
        # Existing tasks remain editable, while new tasks cannot join an archived project.
        await tasks.save(
            done.public_id, 2, TASK.model_copy(update={"project_public_id": project.public_id})
        )
        with pytest.raises(ActionConflictError):
            await tasks.create(TASK.model_copy(update={"project_public_id": project.public_id}))
        project = await projects.archive_project(project.public_id, 3, False)
        await projects.delete_area(area.public_id, 2)
        assert await projects.list_areas() == ()
        project = (await projects.list_projects())[0]
        assert project.area_public_id is None
        assert project.revision == 5
        kept = await tasks.get(task.public_id)
        assert kept.activity[1].area_public_id == area.public_id  # Historical grouping stays.
        assert kept.project is not None and kept.project.area_public_id is None
        assert (
            await tasks.count_actions(ActionFilter.COMPLETED, group="area", group_id=area.public_id)
            == 0
        )
    exported = tmp_path / "export.json"
    await export_writing(db, exported)
    records = json.loads(exported.read_text(encoding="utf-8"))["records"]
    assert records["projects"][0]["public_id"] == project.public_id
    assert records["life_areas"][0]["deleted_at_utc"] is not None
    assert records["action_activity"][1]["area_public_id"] == area.public_id
    await db.dispose()
    db = Database.from_path(path)
    async with db.session() as session:
        assert await ActionService(session).get(task.public_id) == kept
    await db.dispose()
    backup = tmp_path / "backup.zip"
    await asyncio.to_thread(create_backup, path, backup)
    restored = tmp_path / "restored.sqlite3"
    await asyncio.to_thread(restore_backup, backup, restored)
    recovered = Database.from_path(restored)
    async with recovered.session() as session:
        assert await ActionService(session).get(task.public_id) == kept
        assert (await ProjectService(session).list_projects())[0] == project
    await recovered.dispose()


async def test_stale_missing_references_and_atomic_failures(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = Database.from_path(tmp_path / "conflicts.sqlite3")
    await db.create_schema_for_tests()
    async with db.session() as session:
        service = ProjectService(session)
        area = await service.create_area(AreaEdit(name="Home"))
        project = await service.create_project(ProjectEdit(name="Renovation"))
        for operation in (
            service.save_area(area.public_id, 2, AreaEdit(name="Stale")),
            service.delete_area(area.public_id, 2),
            service.save_project(project.public_id, 2, ProjectEdit(name="Stale")),
            service.archive_project(project.public_id, 2, True),
            service.create_project(ProjectEdit(name="Bad", area_public_id="0" * 36)),
            service.save_project(
                project.public_id, 1, ProjectEdit(name="Bad", area_public_id="0" * 36)
            ),
        ):
            with pytest.raises(ProjectConflictError):
                await operation
        await service.delete_area(area.public_id, 1)
        with pytest.raises(ProjectConflictError):
            await service.save_area(area.public_id, 2, AreaEdit(name="Deleted"))
        monkeypatch.setattr(session, "commit", AsyncMock(side_effect=RuntimeError("disk full")))
        with pytest.raises(RuntimeError):
            await service.create_area(AreaEdit(name="Unsaved"))
        with pytest.raises(RuntimeError):
            await service.create_project(ProjectEdit(name="Unsaved"))
        assert await session.scalar(select(func.count()).select_from(ProjectTable)) == 1
        assert await session.scalar(select(func.count()).select_from(LifeAreaTable)) == 1
    await db.dispose()


async def test_group_filter_precedes_completed_limit_and_unassigned_stays_valid(
    tmp_path: Path,
) -> None:
    db = Database.from_path(tmp_path / "filters.sqlite3")
    await db.create_schema_for_tests()
    async with db.session() as session:
        projects = ProjectService(session)
        area = await projects.create_area(AreaEdit(name="Learning"))
        project = await projects.create_project(
            ProjectEdit(name="Study", area_public_id=area.public_id)
        )
        clock = [datetime(2026, 10, 11, tzinfo=UTC)]
        service = ActionService(session, clock=lambda: clock[0])
        assigned = await service.create(
            TASK.model_copy(update={"project_public_id": project.public_id})
        )
        assigned = await service.complete(assigned.public_id, 1)
        clock[0] += timedelta(hours=1)
        newest = await service.create(TASK)
        await service.complete(newest.public_id, 1)
        assert (
            await service.list_actions(
                ActionFilter.COMPLETED, limit=1, group="project", group_id=project.public_id
            )
        )[0].public_id == assigned.public_id
        assert (
            len(
                await service.list_actions(
                    ActionFilter.COMPLETED, group="area", group_id=area.public_id
                )
            )
            == 1
        )
        assert len(await service.list_actions(ActionFilter.COMPLETED, group="unassigned")) == 1
        assert len(await service.list_actions(ActionFilter.COMPLETED)) == 2
        with pytest.raises(ValueError):
            await service.list_actions(ActionFilter.OPEN, group="unknown")
        with pytest.raises(ActionConflictError):
            await service.save(
                assigned.public_id, 2, TASK.model_copy(update={"project_public_id": "0" * 36})
            )
        cleared = await service.save(assigned.public_id, 2, TASK)
        assert cleared.project is None
        assert cleared.activity[-1].project_public_id is None
    await db.dispose()


async def test_area_removal_rolls_back_all_detachments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = Database.from_path(tmp_path / "atomic-area.sqlite3")
    await db.create_schema_for_tests()
    async with db.session() as session:
        service = ProjectService(session)
        area = await service.create_area(AreaEdit(name="Work"))
        project = await service.create_project(
            ProjectEdit(name="Launch", area_public_id=area.public_id)
        )
        monkeypatch.setattr(session, "commit", AsyncMock(side_effect=RuntimeError("disk full")))
        with pytest.raises(RuntimeError):
            await service.delete_area(area.public_id, 1)
        assert await service.list_areas() == (area,)
        assert await service.list_projects() == (project,)
        with pytest.raises(ProjectConflictError):
            await service.archive_project("0" * 36, 1, True)
    await db.dispose()


@pytest.mark.parametrize("project_kind", [False, True])
async def test_reused_session_cannot_overwrite_newer_organization(
    tmp_path: Path, project_kind: bool
) -> None:
    db = Database.from_path(tmp_path / "stale-session.sqlite3")
    await db.create_schema_for_tests()
    async with db.session() as initial:
        service = ProjectService(initial)
        record = (
            await service.create_project(ProjectEdit(name="Original"))
            if project_kind
            else await service.create_area(AreaEdit(name="Original"))
        )
    async with db.session() as old, db.session() as fresh:
        repository = ProjectRepository(old)
        cached = (
            await repository.project(record.public_id)
            if project_kind
            else await repository.area(record.public_id)
        )
        assert cached is not None and cached.revision == 1
        await old.commit()
        if project_kind:
            await ProjectService(fresh).save_project(record.public_id, 1, ProjectEdit(name="Newer"))
            with pytest.raises(ProjectConflictError):
                await ProjectService(old).save_project(record.public_id, 1, ProjectEdit(name="Old"))
        else:
            await ProjectService(fresh).save_area(record.public_id, 1, AreaEdit(name="Newer"))
            with pytest.raises(ProjectConflictError):
                await ProjectService(old).save_area(record.public_id, 1, AreaEdit(name="Old"))
    async with db.session() as reader:
        service = ProjectService(reader)
        records = await service.list_projects() if project_kind else await service.list_areas()
        assert records[0].name == "Newer" and records[0].revision == 2
    await db.dispose()


@pytest.mark.parametrize("project_kind", [False, True])
async def test_concurrent_organization_writes_reject_the_losing_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, project_kind: bool
) -> None:
    db = Database.from_path(tmp_path / "race.sqlite3")
    await db.create_schema_for_tests()
    async with db.session() as initial:
        service = ProjectService(initial)
        record = (
            await service.create_project(ProjectEdit(name="Original"))
            if project_kind
            else await service.create_area(AreaEdit(name="Original"))
        )
    original = ProjectRepository.project if project_kind else ProjectRepository.area
    barrier = asyncio.Event()
    reads = 0

    async def both_read(
        repository: ProjectRepository, public_id: str
    ) -> ProjectTable | LifeAreaTable | None:
        nonlocal reads
        row = await original(repository, public_id)
        reads += 1
        if reads == 2:
            barrier.set()
        await barrier.wait()
        return row

    monkeypatch.setattr(ProjectRepository, "project" if project_kind else "area", both_read)
    async with db.session() as first, db.session() as second:
        operations = [
            ProjectService(session).save_project(record.public_id, 1, ProjectEdit(name=name))
            if project_kind
            else ProjectService(session).save_area(record.public_id, 1, AreaEdit(name=name))
            for session, name in ((first, "First"), (second, "Second"))
        ]
        results = await asyncio.gather(*operations, return_exceptions=True)
        assert sum(isinstance(result, ProjectConflictError) for result in results) == 1
        assert sum(not isinstance(result, BaseException) for result in results) == 1
    async with db.session() as reader:
        service = ProjectService(reader)
        records = await service.list_projects() if project_kind else await service.list_areas()
        assert records[0].name in {"First", "Second"} and records[0].revision == 2
    await db.dispose()


async def test_task_read_hydrates_only_referenced_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from collections.abc import Iterable

    from mailbrief.domain.projects import Project

    db = Database.from_path(tmp_path / "scoped.sqlite3")
    await db.create_schema_for_tests()
    async with db.session() as session:
        projects = ProjectService(session)
        linked = await projects.create_project(ProjectEdit(name="Linked"))
        await projects.create_project(ProjectEdit(name="Unrelated", notes="x" * 10000))
        task = await ActionService(session).create(
            TASK.model_copy(update={"project_public_id": linked.public_id})
        )
        original = ProjectRepository.projects_by_ids
        loaded: list[str] = []

        async def capture(
            repository: ProjectRepository, public_ids: Iterable[str]
        ) -> tuple[Project, ...]:
            records = await original(repository, public_ids)
            loaded.extend(record.name for record in records)
            return records

        monkeypatch.setattr(ProjectRepository, "projects_by_ids", capture)
        assert (await ActionService(session).get(task.public_id)).project == linked
        assert loaded == ["Linked"]
    await db.dispose()
