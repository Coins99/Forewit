"""Synthetic offline metric lifecycle and recovery check, also run by frozen packages."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from mailbrief.domain.common import new_public_id
from mailbrief.domain.dashboards import ChartKind, DashboardLayout, WidgetSpec, WidgetTarget
from mailbrief.domain.metric_entries import MeasurementKind, MetricDefinitionSpec, MetricEntryInput
from mailbrief.domain.metrics import MetricDateRange, MetricQuery, MetricReducer
from mailbrief.services.dashboards import DashboardService
from mailbrief.services.data import export_writing
from mailbrief.services.metric_entries import MetricEntryService
from mailbrief.services.metrics import MetricService
from mailbrief.storage.backup import create_backup
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import upgrade_database
from mailbrief.storage.recovery import inspect_backup, restore_backup


async def check_metrics(directory: Path) -> None:
    """Use only the caller's disposable directory. Never access account/vault/provider state."""
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    path = directory / "metrics.sqlite3"
    await asyncio.to_thread(upgrade_database, path)
    now = datetime(2026, 11, 8, 15, tzinfo=UTC)
    database = Database.from_path(path)
    try:
        async with database.session() as session:
            service = MetricEntryService(session, clock=lambda: now)
            definition = await service.define(
                MetricDefinitionSpec(
                    name="Synthetic reading",
                    unit="minutes",
                    kind=MeasurementKind.AMOUNT,
                    scale=2,
                ),
                command_id=str(uuid4()),
            )
            entry = await service.create(
                definition.public_id,
                MetricEntryInput(
                    value_scaled=1250,
                    local_date=now.date(),
                    zone="UTC",
                ),
                command_id=str(uuid4()),
            )
            changed = await service.mutate(
                entry.public_id,
                1,
                "edit",
                entry=MetricEntryInput(
                    value_scaled=3500,
                    local_date=now.date(),
                    zone="UTC",
                ),
                command_id=str(uuid4()),
            )
            key = str(uuid4())
            saved = await service.mutate(entry.public_id, changed.revision, "undo", command_id=key)
            if (
                await service.mutate(entry.public_id, changed.revision, "undo", command_id=key)
                != saved
            ):
                raise RuntimeError("Packaged metric retry failed.")
        query = MetricQuery(
            metric_key=f"custom.{definition.public_id}.amount",
            reducer=MetricReducer.SUM,
            dates=MetricDateRange(first=now.date(), last=now.date(), zone="UTC"),
        )
        result = await MetricService(database, google_on=False, clock=lambda: now).query(query)
        if str(result.total) != "12.50":
            raise RuntimeError("Packaged metric aggregate failed.")
        async with database.session() as session:
            dashboard_service = DashboardService(session)
            initial = await dashboard_service.load(query.dates)
            layout = DashboardLayout(
                widgets=(
                    *initial.layout.widgets,
                    WidgetSpec(
                        public_id=new_public_id(),
                        title="Synthetic reading",
                        query=query,
                        chart=ChartKind.BAR,
                        target=WidgetTarget(value="20.00"),
                    ),
                )
            )
            dashboard_command = new_public_id()
            dashboard = await dashboard_service.save(
                layout, initial.revision, command_id=dashboard_command
            )
            if (
                await dashboard_service.save(layout, initial.revision, command_id=dashboard_command)
                != dashboard
            ):
                raise RuntimeError("Packaged dashboard retry failed.")
        if (
            len(
                await MetricService(database, google_on=False).query_many(
                    tuple(widget.query for widget in dashboard.layout.widgets)
                )
            )
            != 5
        ):
            raise RuntimeError("Packaged dashboard query failed.")
        output = directory / "metrics.json"
        await export_writing(database, output)
        records = json.loads(await asyncio.to_thread(output.read_text, encoding="utf-8"))["records"]
        if (
            len(records["metric_entry_changes"]) != 3
            or "metric_commands" in records
            or len(records["dashboards"]) != 1
            or "dashboard_commands" in records
        ):
            raise RuntimeError("Packaged metric export failed.")
        archive, recovered = directory / "metrics.zip", directory / "recovered.sqlite3"
        await asyncio.to_thread(create_backup, path, archive)
        await asyncio.to_thread(inspect_backup, archive)
        await asyncio.to_thread(restore_backup, archive, recovered)
    finally:
        await database.dispose()
    restored = Database.from_path(recovered)
    try:
        async with restored.session() as session:
            service = MetricEntryService(session)
            if (
                await service.get_entry(saved.public_id) != saved
                or len(await service.history(saved.public_id)) != 3
            ):
                raise RuntimeError("Packaged metric recovery failed.")
            if (
                await service.mutate(saved.public_id, changed.revision, "undo", command_id=key)
                != saved
            ):
                raise RuntimeError("Packaged recovery lost the metric retry receipt.")
            if await DashboardService(session).load(query.dates) != dashboard:
                raise RuntimeError("Packaged recovery lost the dashboard layout or targets.")
    finally:
        await restored.dispose()
