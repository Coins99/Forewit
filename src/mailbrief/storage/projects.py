"""Project/area ORM access; services own transactions and revision checks."""

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.projects import LifeArea, Project
from mailbrief.storage.database import sorted_batches
from mailbrief.storage.tables import LifeAreaTable, ProjectTable


def area_from_row(row: LifeAreaTable) -> LifeArea:
    return LifeArea(
        public_id=row.public_id,
        name=row.name,
        notes=row.notes,
        revision=row.revision,
        created_at_utc=row.created_at_utc,
        updated_at_utc=row.updated_at_utc,
    )


def project_from_row(row: ProjectTable, area_name: str | None = None) -> Project:
    return Project(
        public_id=row.public_id,
        name=row.name,
        notes=row.notes,
        area_public_id=row.area_public_id,
        area_name=area_name,
        revision=row.revision,
        archived_at_utc=row.archived_at_utc,
        created_at_utc=row.created_at_utc,
        updated_at_utc=row.updated_at_utc,
    )


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def area(self, public_id: str) -> LifeAreaTable | None:
        return (
            await self.session.scalars(
                select(LifeAreaTable)
                .where(LifeAreaTable.public_id == public_id, LifeAreaTable.deleted_at_utc.is_(None))
                .execution_options(populate_existing=True)
            )
        ).one_or_none()

    async def project(self, public_id: str) -> ProjectTable | None:
        return (
            await self.session.scalars(
                select(ProjectTable)
                .where(ProjectTable.public_id == public_id)
                .execution_options(populate_existing=True)
            )
        ).one_or_none()

    async def areas(self) -> tuple[LifeArea, ...]:
        rows = await self.session.scalars(
            select(LifeAreaTable)
            .where(LifeAreaTable.deleted_at_utc.is_(None))
            .order_by(LifeAreaTable.name, LifeAreaTable.id)
            .execution_options(populate_existing=True)
        )
        return tuple(area_from_row(row) for row in rows)

    async def projects(self) -> tuple[Project, ...]:
        result = await self.session.execute(
            select(ProjectTable, LifeAreaTable.name)
            .outerjoin(LifeAreaTable, ProjectTable.area_public_id == LifeAreaTable.public_id)
            .order_by(ProjectTable.name, ProjectTable.id)
            .execution_options(populate_existing=True)
        )
        return tuple(project_from_row(row, name) for row, name in result.tuples())

    async def projects_by_ids(self, public_ids: Iterable[str]) -> tuple[Project, ...]:
        """Hydrate only the projects referenced by this batch of tasks."""
        projects: list[Project] = []
        for chunk in sorted_batches(public_ids):
            result = await self.session.execute(
                select(ProjectTable, LifeAreaTable.name)
                .outerjoin(LifeAreaTable, ProjectTable.area_public_id == LifeAreaTable.public_id)
                .where(ProjectTable.public_id.in_(chunk))
                .execution_options(populate_existing=True)
            )
            projects.extend(project_from_row(row, name) for row, name in result.tuples())
        return tuple(projects)
