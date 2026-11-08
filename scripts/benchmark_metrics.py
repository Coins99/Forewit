"""Reproducible synthetic P4 query benchmark; never reads an owner profile or provider."""

import argparse
import asyncio
import hashlib
import json
import platform
import statistics
import tempfile
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import insert

from mailbrief.domain.metrics import MetricDateRange, MetricQuery, MetricReducer
from mailbrief.services.metrics import MetricService
from mailbrief.storage.database import Database
from mailbrief.storage.migrate import upgrade_database
from mailbrief.storage.tables import (
    ActionActivityTable,
    ActionTable,
    MetricDefinitionTable,
    MetricEntryChangeTable,
    MetricEntryTable,
)

NOW = datetime(2026, 11, 8, 15, tzinfo=UTC)


def identity(number: int) -> str:
    return str(UUID(int=number))


async def seed(database: Database, tasks: int, entries: int) -> str:
    """Seed explicit fixture transitions, including reopen episodes and complete histories."""
    definition = identity(10_000_000)
    async with database.transaction() as session:
        await session.execute(
            insert(MetricDefinitionTable),
            [
                {
                    "public_id": definition,
                    "name": "Synthetic reading",
                    "unit": "minutes",
                    "kind": "amount",
                    "scale": 2,
                    "allow_negative": False,
                    "version": 1,
                    "revision": 1,
                    "archived": False,
                    "created_at_utc": NOW,
                    "updated_at_utc": NOW,
                }
            ],
        )
        for first in range(0, tasks, 500):
            task_rows: list[dict[str, Any]] = []
            events: list[dict[str, Any]] = []
            for index in range(first, min(tasks, first + 500)):
                task_rows.append(
                    {
                        "id": index + 1,
                        "public_id": identity(index + 1),
                        "title": "Synthetic task",
                        "capture_origin": "direct",
                        "status": "open",
                        "ownership": "mine",
                        "revision": 10,
                        "created_at_utc": NOW - timedelta(days=9),
                        "updated_at_utc": NOW,
                    }
                )
                for revision in range(1, 11):
                    kind = (
                        "created"
                        if revision == 1
                        else (
                            "edited"
                            if revision == 10
                            else "completed"
                            if revision % 2 == 0
                            else "reopened"
                        )
                    )
                    events.append(
                        {
                            "action_id": index + 1,
                            "revision": revision,
                            "kind": kind,
                            "occurred_at_utc": NOW - timedelta(days=10 - revision),
                            "legacy": False,
                        }
                    )
            await session.execute(insert(ActionTable), task_rows)
            await session.execute(insert(ActionActivityTable), events)
        for first in range(0, entries, 500):
            logs: list[dict[str, Any]] = []
            changes: list[dict[str, Any]] = []
            for index in range(first, min(entries, first + 500)):
                day = NOW.date() - timedelta(days=index % 30)
                key = identity(100_000 + index)
                state = {
                    "value_scaled": 1250,
                    "local_date": day.isoformat(),
                    "zone": "UTC",
                    "project_public_id": None,
                    "deleted": False,
                }
                logs.append(
                    {
                        "public_id": key,
                        "definition_id": definition,
                        "definition_version": 1,
                        "value_scaled": 1250,
                        "local_date": day,
                        "zone": "UTC",
                        "revision": 1,
                        "created_at_utc": NOW,
                        "updated_at_utc": NOW,
                    }
                )
                changes.append(
                    {
                        "entry_id": key,
                        "revision": 1,
                        "operation": "create",
                        "state_json": state,
                        "recorded_at_utc": NOW,
                    }
                )
            if logs:
                await session.execute(insert(MetricEntryTable), logs)
                await session.execute(insert(MetricEntryChangeTable), changes)
    return definition


async def benchmark(tasks: int, entries: int, repeats: int, output: Path) -> dict[str, Any]:
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p4-benchmark-", dir=output.parent) as temporary:
        path = Path(temporary) / "synthetic.sqlite3"
        await asyncio.to_thread(upgrade_database, path)
        database = Database.from_path(path)
        try:
            definition = await seed(database, tasks, entries)
            service = MetricService(database, google_on=False, clock=lambda: NOW)
            dates = MetricDateRange(
                first=NOW.date() - timedelta(days=29), last=NOW.date(), zone="UTC"
            )
            timings: dict[str, object] = {}
            for key, reducer, expected in (
                ("tasks.created", MetricReducer.COUNT, tasks),
                ("tasks.completions", MetricReducer.COUNT, tasks * 4),
                ("tasks.distinct_completed", MetricReducer.DISTINCT, tasks),
                (
                    f"custom.{definition}.amount",
                    MetricReducer.SUM,
                    Decimal(entries) * Decimal("12.5"),
                ),
            ):
                elapsed: list[float] = []
                for _ in range(repeats):
                    started = time.perf_counter()
                    result = await service.query(
                        MetricQuery(metric_key=key, reducer=reducer, dates=dates)
                    )
                    elapsed.append(time.perf_counter() - started)
                    if result.total != expected:
                        raise RuntimeError(
                            "Synthetic aggregate did not match the benchmark fixture."
                        )
                started = time.perf_counter()
                await service.contributors(result)
                timings[key] = {
                    "first_seconds": elapsed[0],
                    "median_seconds": statistics.median(elapsed),
                    "worst_seconds": max(elapsed),
                    "detail_seconds": time.perf_counter() - started,
                }
        finally:
            await database.dispose()
    report: dict[str, Any] = {
        "synthetic_only": True,
        "platform": platform.platform(),
        "python": platform.python_version(),
        "processor": platform.processor(),
        "source_sha256": await asyncio.to_thread(source_signature),
        "tasks": tasks,
        "activity_records": tasks * 10,
        "custom_entries": entries,
        "repeats": repeats,
        "queries": timings,
        "scope": "Engine queries and revalidated detail; 12-widget dashboard is a later milestone.",
    }
    await asyncio.to_thread(
        output.write_text, json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    return report


def source_signature() -> str:
    digest = hashlib.sha256()
    for folder in ("src/mailbrief/domain", "src/mailbrief/storage", "src/mailbrief/services"):
        for source in sorted(Path(folder).glob("metric*.py")):
            digest.update(source.read_bytes())
    for file_name in (
        "src/mailbrief/storage/tables.py",
        "migrations/versions/20261108_0037_metric_logs.py",
    ):
        digest.update(Path(file_name).read_bytes())
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=10_000)
    parser.add_argument("--entries", type=int, default=50_000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--output", type=Path, default=Path("scratch/p4-metric-benchmark.json"))
    options = parser.parse_args()
    if (
        not 1 <= options.tasks <= 100_000
        or not 0 <= options.entries <= 100_000
        or not 1 <= options.repeats <= 5
    ):
        parser.error("Use 1–100000 tasks, 0–100000 entries and 1–5 repeats.")
    asyncio.run(benchmark(options.tasks, options.entries, options.repeats, options.output))


if __name__ == "__main__":
    main()
