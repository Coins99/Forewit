"""Synthetic 12-widget dashboard/query/detail/input-latency benchmark; never owner data."""

import asyncio
import ctypes
import hashlib
import json
import os
import platform
import statistics
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QDate, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from qasync import QEventLoop  # noqa: E402

from mailbrief.domain.common import new_public_id  # noqa: E402
from mailbrief.domain.dashboards import ChartKind, DashboardLayout, WidgetSpec  # noqa: E402
from mailbrief.domain.metrics import (  # noqa: E402
    MetricDateRange,
    MetricDimension,
    MetricQuery,
    MetricReducer,
    builtin_metric,
)
from mailbrief.services.dashboards import DashboardService  # noqa: E402
from mailbrief.storage.database import Database  # noqa: E402
from mailbrief.storage.migrate import upgrade_database  # noqa: E402
from mailbrief.ui.insights_backend import InsightsBackend  # noqa: E402
from mailbrief.ui.insights_view import InsightsPage  # noqa: E402
from scripts.benchmark_metrics import NOW, seed  # noqa: E402


def peak_memory_bytes() -> int:
    if sys.platform == "win32":

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_uint32),
                ("faults", ctypes.c_uint32),
                *(
                    (name, ctypes.c_size_t)
                    for name in (
                        "peak",
                        "working",
                        "pool_peak",
                        "pool",
                        "nonpaged_peak",
                        "nonpaged",
                        "pagefile",
                        "peak_pagefile",
                    )
                ),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        process = ctypes.windll.kernel32.GetCurrentProcess
        process.restype = ctypes.c_void_p
        read = ctypes.windll.psapi.GetProcessMemoryInfo
        read.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_uint32]
        if not read(process(), ctypes.byref(counters), counters.cb):
            raise RuntimeError("Unable to measure benchmark memory.")
        return int(counters.peak)
    else:
        import resource

        return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * (
            1 if sys.platform == "darwin" else 1024
        )


async def benchmark(output: Path) -> None:
    await asyncio.to_thread(output.parent.mkdir, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="p4-dashboard-", dir=output.parent) as temporary:
        database_path = Path(temporary) / "synthetic.sqlite3"
        await asyncio.to_thread(upgrade_database, database_path)
        database = Database.from_path(database_path)
        page = InsightsPage()
        latency: list[float] = []
        last_tick = time.perf_counter()

        def tick() -> None:
            nonlocal last_tick
            now = time.perf_counter()
            latency.append(now - last_tick)
            last_tick = now

        timer = QTimer()
        timer.setInterval(10)
        timer.timeout.connect(tick)
        try:
            definition = await seed(database, 10_000, 50_000)
            dates = MetricDateRange(
                first=NOW.date() - timedelta(days=29), last=NOW.date(), zone="UTC"
            )
            widgets: list[WidgetSpec] = []
            for key, chart, group in (
                ("tasks.created", ChartKind.NUMBER, None),
                ("tasks.completions", ChartKind.LINE, None),
                ("tasks.distinct_completed", ChartKind.NUMBER, None),
                ("tasks.current_overdue", ChartKind.NUMBER, None),
                (f"custom.{definition}.amount", ChartKind.BAR, None),
                (f"custom.{definition}.amount", ChartKind.BAR, MetricDimension.PROJECT),
                ("plan.selected_estimates", ChartKind.NUMBER, None),
                ("blocks.scheduled_total", ChartKind.BAR, None),
                ("blocks.scheduled_unique", ChartKind.LINE, None),
                ("capacity.available", ChartKind.BAR, None),
                ("workload.planned", ChartKind.NUMBER, None),
                ("tasks.completions", ChartKind.BAR, MetricDimension.PROJECT),
            ):
                reducer = (
                    MetricReducer.SUM if key.startswith("custom.") else builtin_metric(key).reducer
                )
                chosen = (
                    MetricDateRange(first=NOW.date(), last=NOW.date(), zone="UTC")
                    if key == "workload.planned"
                    else dates
                )
                widgets.append(
                    WidgetSpec(
                        public_id=new_public_id(),
                        title="Synthetic " + key.split(".")[0],
                        chart=chart,
                        query=MetricQuery(
                            metric_key=key, reducer=reducer, dates=chosen, group_by=group
                        ),
                        use_global_range=False,
                    )
                )
            async with database.session() as session:
                service = DashboardService(session)
                saved = await service.load(dates)
                await service.save(
                    DashboardLayout(widgets=tuple(widgets)),
                    saved.revision,
                    command_id=new_public_id(),
                )
            backend = InsightsBackend(database, google_on=False)
            backend.metrics.clock = lambda: NOW
            page.backend = backend
            page.zone = "UTC"
            page.first.setDate(QDate(dates.first.year, dates.first.month, dates.first.day))
            page.last.setDate(QDate(dates.last.year, dates.last.month, dates.last.day))
            page.resize(1100, 720)
            page.show()
            last_tick = time.perf_counter()
            timer.start()
            dashboard: list[float] = []
            first_usable: list[float] = []
            run_started = time.perf_counter()

            def ready() -> None:
                if not first_usable:
                    first_usable.append(time.perf_counter() - run_started)

            page.data_ready.connect(ready)
            for _ in range(3):
                started = time.perf_counter()
                page.refresh()
                assert page._refresh_task is not None
                await page._refresh_task
                await asyncio.sleep(0.02)
                if len(page.cards) != 12:
                    raise RuntimeError("Dashboard did not render twelve widgets.")
                dashboard.append(time.perf_counter() - started)
            query = widgets[1].query
            result = await backend.metrics.query(query)
            if result.total != 40_000:
                raise RuntimeError("Dashboard fixture did not reconcile.")
            started = time.perf_counter()
            rows = await backend.metrics.contributors(result)
            detail = time.perf_counter() - started
            if len(rows) != 100:
                raise RuntimeError("Detail fixture did not return a complete page.")
            signature = await asyncio.to_thread(source_signature)
            report: dict[str, Any] = {
                "synthetic_only": True,
                "platform": platform.platform(),
                "python": platform.python_version(),
                "processor": platform.processor(),
                "source_sha256": signature,
                "tasks": 10_000,
                "activity_records": 100_000,
                "entries": 50_000,
                "widgets": 12,
                "dashboard_seconds": dashboard,
                "cold_seconds": dashboard[0],
                "first_usable_card_seconds": first_usable[0],
                "warm_p50_seconds": statistics.median(dashboard[1:]),
                "worst_seconds": max(dashboard),
                "detail_first_page_seconds": detail,
                "peak_process_bytes": peak_memory_bytes(),
                "gui_tick_p95_seconds": sorted(latency)[int((len(latency) - 1) * 0.95)],
                "gui_tick_worst_seconds": max(latency),
                "notes": "10 ms Qt timer measures event-loop latency, not real-display input. "
                "Warm runs reuse OS pages only; source snapshots are rebuilt. Three samples "
                "are insufficient for a stable dashboard p95. No acceptance claim.",
            }
            await asyncio.to_thread(
                output.write_text, json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
        finally:
            timer.stop()
            await page.drain()
            page.close()
            await database.dispose()


def source_signature() -> str:
    source_hash = hashlib.sha256()
    for folder in ("src/mailbrief", "migrations/versions"):
        for source in sorted(Path(folder).rglob("*.py")):
            source_hash.update(source.read_bytes())
    return source_hash.hexdigest()


def main() -> None:
    application = QApplication([])
    application.setQuitOnLastWindowClosed(False)
    loop = QEventLoop(application)
    asyncio.set_event_loop(loop)
    with loop:
        loop.run_until_complete(benchmark(Path("scratch/p4-dashboard-benchmark.json")))


if __name__ == "__main__":
    main()
