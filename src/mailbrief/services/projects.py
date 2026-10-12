"""Create, organize and archive owner projects without changing or deleting their tasks."""

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from mailbrief.domain.common import normalize_utc
from mailbrief.domain.projects import AreaEdit, LifeArea, Project, ProjectEdit
from mailbrief.storage.projects import ProjectRepository, area_from_row, project_from_row
from mailbrief.storage.tables import LifeAreaTable, ProjectTable


class ProjectConflictError(ValueError):
    """Missing, archived or stale organization; errors never contain owner writing."""


class ProjectService:
    def __init__(
        self, session: AsyncSession, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self.session = session
        self.repository = ProjectRepository(session)
        self.clock = clock

    async def _write[T](self, operation: Callable[[datetime], Awaitable[T]]) -> T:
        try:
            result = await operation(normalize_utc(self.clock()))
            await self.session.commit()
            return result
        except StaleDataError:
            await self.session.rollback()
            raise ProjectConflictError("Organization changed; reload it and try again.") from None
        except BaseException:
            await self.session.rollback()
            raise

    async def list_areas(self) -> tuple[LifeArea, ...]:
        return await self.repository.areas()

    async def list_projects(self) -> tuple[Project, ...]:
        return await self.repository.projects()

    async def _area(self, public_id: str, revision: int | None = None) -> LifeAreaTable:
        row = await self.repository.area(public_id)
        if row is None or (revision is not None and row.revision != revision):
            raise ProjectConflictError("That life area changed or is no longer available.")
        return row

    async def _project(self, public_id: str, revision: int) -> ProjectTable:
        row = await self.repository.project(public_id)
        if row is None or row.revision != revision:
            raise ProjectConflictError("That project changed or is no longer available.")
        return row

    async def _load(self, row: ProjectTable) -> Project:
        area = None if row.area_public_id is None else await self._area(row.area_public_id)
        return project_from_row(row, None if area is None else area.name)

    async def create_area(self, edit: AreaEdit) -> LifeArea:
        async def run(now: datetime) -> LifeArea:
            row = LifeAreaTable(
                public_id=str(uuid.uuid4()),
                name=edit.name,
                notes=edit.notes,
                revision=1,
                created_at_utc=now,
                updated_at_utc=now,
            )
            self.session.add(row)
            return area_from_row(row)

        return await self._write(run)

    async def save_area(self, public_id: str, revision: int, edit: AreaEdit) -> LifeArea:
        async def run(now: datetime) -> LifeArea:
            row = await self._area(public_id, revision)
            row.name, row.notes = edit.name, edit.notes
            row.revision += 1
            row.updated_at_utc = now
            return area_from_row(row)

        return await self._write(run)

    async def delete_area(self, public_id: str, revision: int) -> None:
        async def run(now: datetime) -> None:
            row = await self._area(public_id, revision)
            row.deleted_at_utc = now
            row.updated_at_utc = now
            row.revision += 1
            projects = await self.session.scalars(
                select(ProjectTable)
                .where(ProjectTable.area_public_id == public_id)
                .execution_options(populate_existing=True)
            )
            for project in projects:
                project.area_public_id = None
                project.updated_at_utc = now
                project.revision += 1

        await self._write(run)

    async def create_project(self, edit: ProjectEdit) -> Project:
        async def run(now: datetime) -> Project:
            if edit.area_public_id is not None:
                await self._area(edit.area_public_id)
            row = ProjectTable(
                public_id=str(uuid.uuid4()),
                name=edit.name,
                notes=edit.notes,
                area_public_id=edit.area_public_id,
                revision=1,
                created_at_utc=now,
                updated_at_utc=now,
            )
            self.session.add(row)
            return await self._load(row)

        return await self._write(run)

    async def save_project(self, public_id: str, revision: int, edit: ProjectEdit) -> Project:
        async def run(now: datetime) -> Project:
            row = await self._project(public_id, revision)
            if edit.area_public_id is not None:
                await self._area(edit.area_public_id)
            row.name, row.notes, row.area_public_id = edit.name, edit.notes, edit.area_public_id
            row.revision += 1
            row.updated_at_utc = now
            return await self._load(row)

        return await self._write(run)

    async def archive_project(self, public_id: str, revision: int, archived: bool) -> Project:
        async def run(now: datetime) -> Project:
            row = await self._project(public_id, revision)
            if (row.archived_at_utc is not None) != archived:
                row.archived_at_utc = now if archived else None
                row.revision += 1
                row.updated_at_utc = now
            return await self._load(row)

        return await self._write(run)
