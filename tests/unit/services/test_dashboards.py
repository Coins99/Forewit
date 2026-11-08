"""Dashboard concurrency, atomic retry, repair, recovery and source reconciliation."""

import asyncio
import json
import sqlite3
import threading
from contextlib import closing
from datetime import date, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.common import new_public_id
from mailbrief.domain.dashboards import ChartKind, DashboardLayout, WidgetSpec
from mailbrief.domain.metric_entries import MeasurementKind, MetricDefinitionSpec
from mailbrief.domain.metric_queries import MetricQueryResult
from mailbrief.domain.metrics import MetricDateRange, MetricQuery, custom_metric
from mailbrief.services import metrics as metrics_module
from mailbrief.services.dashboards import DashboardRepairError, DashboardService, starter_layout
from mailbrief.services.data import export_writing
from mailbrief.services.metric_calculations import calculate_metric
from mailbrief.services.metric_entries import (
    MetricConflictError,
    MetricEntryService,
    MetricStorageError,
)
from mailbrief.services.metrics import MetricService
from mailbrief.storage.backup import create_backup
from mailbrief.storage.database import Database
from mailbrief.storage.metric_sources import MetricSources
from mailbrief.storage.migrate import upgrade_database
from mailbrief.storage.recovery import BackupValidationError, restore_backup, validate_snapshot
from mailbrief.storage.tables import DashboardCommandTable, DashboardTable

DATES = MetricDateRange(first=date(2026, 11, 1), last=date(2026, 11, 8), zone="America/Toronto")


async def test_starter_retry_revision_and_conflicting_editors(database: Database) -> None:
    async def load() -> object:
        async with database.session() as session:
            return await DashboardService(session).load(DATES)

    first, second = await asyncio.gather(load(), load())
    assert first == second
    async with database.session() as session:
        service = DashboardService(session)
        original = await service.load(DATES)
        assert len(original.layout.widgets) == 4
        layout = DashboardLayout(widgets=original.layout.widgets[::-1])
        identity = new_public_id()
        saved = await service.save(layout, original.revision, command_id=identity)
        assert saved.revision == 2
        assert await service.save(layout, 1, command_id=identity.upper()) == saved
        with pytest.raises(MetricConflictError):
            await service.save(original.layout, 1, command_id=new_public_id())
        with pytest.raises(MetricConflictError):
            await service.save(original.layout, 1, command_id=identity)
        next_saved = await service.save(original.layout, 2, command_id=new_public_id())
        assert next_saved.revision == 3
        with pytest.raises(MetricConflictError):
            await service.save(layout, 1, command_id=identity)
        assert await service.load(DATES) == next_saved


async def test_validated_custom_widget_and_limit(session: AsyncSession) -> None:
    logs = MetricEntryService(session)
    definition = await logs.define(
        MetricDefinitionSpec(name="Minutes", unit="min", kind=MeasurementKind.AMOUNT),
        command_id=new_public_id(),
    )
    service = DashboardService(session)
    original = await service.load(DATES)
    key = f"custom.{definition.public_id}.amount"
    descriptor = custom_metric(key, definition.name, definition.unit, definition.kind)
    widget = WidgetSpec(
        public_id=new_public_id(),
        title="My metric",
        chart=ChartKind.BAR,
        query=MetricQuery(metric_key=key, reducer=descriptor.reducer, dates=DATES),
    )
    twelve = DashboardLayout(
        widgets=tuple(widget.model_copy(update={"public_id": new_public_id()}) for _ in range(12))
    )
    assert len((await service.save(twelve, 1, command_id=new_public_id())).layout.widgets) == 12
    invalid = twelve.model_copy(update={"widgets": (*twelve.widgets, widget)})
    with pytest.raises(ValueError):
        await service.save(invalid, 2, command_id=new_public_id())
    malformed = widget.model_copy(
        update={
            "query": widget.query.model_copy(
                update={"metric_key": f"custom.{definition.public_id}.point"}
            )
        }
    )
    with pytest.raises(ValueError, match="reference"):
        await service.save(DashboardLayout(widgets=(malformed,)), 2, command_id=new_public_id())
    assert (await service.load(DATES)).layout == twelve
    assert original.revision == 1


async def test_repair_and_missing_singleton_conflict(session: AsyncSession) -> None:
    service = DashboardService(session)
    with pytest.raises(MetricConflictError):
        await service.save(DashboardLayout(), 1, command_id=new_public_id())
    saved = await service.load(DATES)
    row = await session.get(DashboardTable, 1)
    assert row is not None
    row.layout_json = {"schema_version": 500}
    await session.commit()
    with pytest.raises(DashboardRepairError) as error:
        await service.load(DATES)
    assert error.value.revision == saved.revision
    fixed = await service.save(starter_layout(DATES), saved.revision, command_id=new_public_id())
    assert fixed.revision == 2
    with pytest.raises(ValueError, match="UUID"):
        await service.save(fixed.layout, 2, command_id="bad")


@pytest.mark.parametrize("failure", ["commit", "cancel"])
async def test_failed_save_leaves_layout_and_receipt_atomic(
    session: AsyncSession, failure: str
) -> None:
    service = DashboardService(session)
    saved = await service.load(DATES)
    commit = session.commit
    exception = (
        asyncio.CancelledError()
        if failure == "cancel"
        else OperationalError("owner title", {"value": "private"}, Exception())
    )
    session.commit = AsyncMock(side_effect=exception)  # type: ignore[method-assign]
    with pytest.raises(asyncio.CancelledError if failure == "cancel" else MetricStorageError):
        await service.save(DashboardLayout(), 1, command_id=new_public_id())
    session.commit = commit  # type: ignore[method-assign]
    assert await service.load(DATES) == saved
    assert not list(await session.scalars(select(DashboardCommandTable)))


async def test_backup_restore_export_and_reject_corrupt_layout(tmp_path: Path) -> None:
    source = tmp_path / "original" / "mailbrief.sqlite3"
    await asyncio.to_thread(upgrade_database, source)
    database = Database.from_path(source)
    async with database.session() as session:
        service = DashboardService(session)
        saved = await service.load(DATES)
        saved = await service.save(saved.layout, 1, command_id=new_public_id())
    destination = tmp_path / "writing.json"
    await export_writing(database, destination)
    payload = json.loads(destination.read_text())
    assert "dashboards" in str(payload)
    assert "dashboard_commands" not in str(payload)
    await database.dispose()
    archive = tmp_path / "backup.zip"
    await asyncio.to_thread(create_backup, source, archive)
    restored = tmp_path / "restored" / "mailbrief.sqlite3"
    await asyncio.to_thread(restore_backup, archive, restored)
    copy = Database.from_path(restored)
    async with copy.session() as session:
        assert await DashboardService(session).load(DATES) == saved
    await copy.dispose()
    with closing(sqlite3.connect(restored)) as connection:
        connection.execute("UPDATE dashboards SET layout_json=?", ('{"schema_version":99}',))
        connection.commit()
    with pytest.raises(BackupValidationError):
        await asyncio.to_thread(validate_snapshot, restored, current_schema=True)


async def test_shared_dashboard_queries_match_individual_results(database: Database) -> None:
    service = MetricService(database, google_on=False)
    queries = tuple(w.query for w in starter_layout(DATES).widgets)
    results = await service.query_many(queries)
    for query, result in zip(queries, results, strict=True):
        individual = await service.query(query, captured_at=result.captured_at_utc)
        assert result == individual
    with pytest.raises(ValueError, match="twelve"):
        await service.query_many(queries * 7)


async def test_cancelled_dashboard_query_keeps_its_worker_slot_until_joined(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, release = threading.Event(), threading.Event()
    calls: list[str] = []

    def blocked(
        query: MetricQuery, sources: MetricSources, now: datetime, *, google_on: bool
    ) -> MetricQueryResult:
        calls.append(query.metric_key)
        if len(calls) == 1:
            started.set()
            assert release.wait(10)
        return calculate_metric(query, sources, now, google_on=google_on)

    monkeypatch.setattr(metrics_module, "calculate_metric", blocked)
    service = MetricService(database, google_on=False)
    queries = (starter_layout(DATES).widgets[0].query,)
    first = asyncio.create_task(service.query_many(queries))
    assert await asyncio.to_thread(started.wait, 5)
    first.cancel()
    await asyncio.sleep(0)
    first.cancel()
    second = asyncio.create_task(service.query_many(queries))
    try:
        await asyncio.sleep(0.03)
        assert len(calls) == 1
    finally:
        release.set()
        outcomes = await asyncio.gather(first, second, return_exceptions=True)
    assert isinstance(outcomes[0], asyncio.CancelledError)
    assert isinstance(outcomes[1], tuple) and len(outcomes[1]) == 1
    assert len(calls) == 2


async def test_invalid_batch_returns_a_safe_error_instead_of_partial_totals(
    session: AsyncSession,
    database: Database,
) -> None:
    from mailbrief.services.metrics import MetricDataError

    metric = await MetricEntryService(session).define(
        MetricDefinitionSpec(
            name="Private metric title",
            unit="units",
            kind=MeasurementKind.AMOUNT,
        ),
        command_id=new_public_id(),
    )
    bad = (
        starter_layout(DATES)
        .widgets[0]
        .query.model_copy(
            update={
                "metric_key": f"custom.{metric.public_id}.point",
            }
        )
    )
    with pytest.raises(MetricDataError) as error:
        await MetricService(database, google_on=False).query_many((bad,))
    assert "Private" not in str(error.value)
