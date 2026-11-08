"""Local composition for Insights, independent of mail, AI and the window's job queue."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.dashboards import DashboardLayout, SavedDashboard
from mailbrief.domain.metric_entries import (
    MetricDefinition,
    MetricDefinitionSpec,
    MetricEntry,
    MetricEntryChange,
    MetricEntryInput,
)
from mailbrief.domain.metric_queries import MetricComparison, MetricContribution, MetricQueryResult
from mailbrief.domain.metrics import MetricDateRange, MetricQuery
from mailbrief.services.dashboards import DashboardService
from mailbrief.services.metric_entries import MetricEntryService
from mailbrief.services.metrics import MetricService
from mailbrief.storage.database import Database


class DesktopMetrics(MetricService):
    """Materialize SQLite rows and reduce them on a worker-owned event loop/engine.

    Only the database URL and domain values cross the boundary. The desktop's engine,
    ORM objects and sessions are never used on the other loop.
    """

    def __init__(self, database: Database, *, google_on: bool) -> None:
        super().__init__(database, google_on=google_on)
        self._worker_slot = asyncio.Semaphore(1)

    async def _work[T](self, operation: Callable[[MetricService], Awaitable[T]]) -> T:
        url, google_on, now = str(self.database.engine.url), self.google_on, self.clock()
        async with self._worker_slot:

            def worker() -> T:
                async def run() -> T:
                    database = Database(url)
                    try:
                        return await operation(
                            MetricService(database, google_on=google_on, clock=lambda: now)
                        )
                    finally:
                        await database.dispose()

                return asyncio.run(run())

            task = asyncio.create_task(asyncio.to_thread(worker))
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                task.result()
                raise

    async def query_many(
        self, queries: tuple[MetricQuery, ...], *, captured_at: datetime | None = None
    ) -> tuple[MetricQueryResult, ...]:
        async def read(service: MetricService) -> tuple[MetricQueryResult, ...]:
            results = await service.query_many(queries, captured_at=captured_at)
            # The UI keeps generation handles and prepared buckets. Release large
            # contributor sets on the worker; details revalidate and page there.
            return tuple(result.model_copy(update={"contributions": ()}) for result in results)

        return await self._work(read)

    async def query(
        self, query: MetricQuery, *, captured_at: datetime | None = None
    ) -> MetricQueryResult:
        async def read(service: MetricService) -> MetricQueryResult:
            result = await service.query(query, captured_at=captured_at)
            return result.model_copy(update={"contributions": ()})

        return await self._work(read)

    async def compare(
        self, query: MetricQuery, *, captured_at: datetime | None = None
    ) -> MetricComparison:
        async def read(service: MetricService) -> MetricComparison:
            result = await service.compare(query, captured_at=captured_at)
            return result.model_copy(
                update={
                    "current": result.current.model_copy(update={"contributions": ()}),
                    "previous": result.previous.model_copy(update={"contributions": ()})
                    if result.previous is not None
                    else None,
                }
            )

        return await self._work(read)

    async def contributors(
        self,
        result: MetricQueryResult,
        *,
        bucket: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> tuple[MetricContribution, ...]:
        return await self._work(
            lambda service: service.contributors(
                result,
                bucket=bucket,
                after=after,
                limit=limit,
            )
        )


class InsightsBackend:
    def __init__(self, database: Database, *, google_on: bool) -> None:
        self.database = database
        self.metrics = DesktopMetrics(database, google_on=google_on)

    async def _read[T](self, operation: Callable[[AsyncSession], Awaitable[T]]) -> T:
        async def read() -> T:
            async with self.database.session() as session:
                return await operation(session)

        task = asyncio.create_task(read())
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            # SQLite connection creation runs on its own thread. Join the read and
            # session cleanup before closing the profile's loop, even on repeat cancel.
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
            task.result()
            raise

    async def load(self, dates: MetricDateRange) -> SavedDashboard:
        return await self._read(lambda session: DashboardService(session).load(dates))

    async def save(self, layout: DashboardLayout, revision: int, command: str) -> SavedDashboard:
        async with self.database.session() as session:
            return await DashboardService(session).save(layout, revision, command_id=command)

    async def definitions(self) -> tuple[MetricDefinition, ...]:
        return await self._read(
            lambda session: MetricEntryService(session).definitions(
                include_archived=True,
            )
        )

    async def define(self, spec: MetricDefinitionSpec, command: str) -> MetricDefinition:
        async with self.database.session() as session:
            return await MetricEntryService(session).define(spec, command_id=command)

    async def configure(
        self, definition: MetricDefinition, archived: bool, command: str
    ) -> MetricDefinition:
        async with self.database.session() as session:
            return await MetricEntryService(session).configure(
                definition.public_id,
                definition.revision,
                definition,
                archived=archived,
                command_id=command,
            )

    async def entries(
        self, definition: str, after: tuple[str, str] | None = None
    ) -> tuple[MetricEntry, ...]:
        return await self._read(
            lambda session: MetricEntryService(session).entries(
                definition,
                include_deleted=True,
                after=after,
            )
        )

    async def history(self, identity: str) -> tuple[MetricEntryChange, ...]:
        return await self._read(lambda session: MetricEntryService(session).history(identity))

    async def create(self, definition: str, entry: MetricEntryInput, command: str) -> MetricEntry:
        async with self.database.session() as session:
            return await MetricEntryService(session).create(definition, entry, command_id=command)

    async def mutate(
        self,
        entry: MetricEntry,
        operation: str,
        command: str,
        value: MetricEntryInput | None = None,
    ) -> MetricEntry:
        async with self.database.session() as session:
            return await MetricEntryService(session).mutate(
                entry.public_id,
                entry.revision,
                operation,
                entry=value,
                command_id=command,
            )
