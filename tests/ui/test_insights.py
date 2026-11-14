"""Offline owner journey, stale details, independent writes and responsive Insights."""

import asyncio
import os
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.domain.common import new_public_id
from mailbrief.domain.dashboards import ChartKind, DashboardLayout
from mailbrief.domain.metric_queries import MetricQueryResult
from mailbrief.domain.metrics import MetricDateRange, MetricQuery
from mailbrief.domain.routes import PageRoute
from mailbrief.services import metrics as metrics_module
from mailbrief.services.dashboards import starter_layout
from mailbrief.storage.database import Database
from mailbrief.ui.insights_backend import InsightsBackend
from mailbrief.ui.insights_editors import MetricManager, WidgetEditor, custom_descriptor
from mailbrief.ui.insights_view import InsightsPage, MetricDetails
from mailbrief.ui.metric_chart import MetricChartCard
from mailbrief.ui.theme import ColorScheme, ThemeMode, apply_theme
from tests.ui.test_main_window_layout import save_shot
from tests.ui.test_palette import window_for
from tests.ui.test_workflow import FakeBackend

DATES = MetricDateRange(first=date(2026, 11, 1), last=date(2026, 11, 8), zone="America/Toronto")


async def refresh(page: InsightsPage) -> None:
    page.refresh()
    assert page._refresh_task is not None
    await page._refresh_task


async def test_offline_dashboard_reorder_resize_remove_undo_and_restart(
    qtbot: QtBot,
    database: Database,
) -> None:
    page = InsightsPage()
    qtbot.addWidget(page)
    page.resize(1000, 700)
    page.show()
    backend = InsightsBackend(database, google_on=False)
    page.attach(backend, "America/Toronto", (), ())
    await refresh(page)
    assert page.saved is not None
    original = page.saved
    assert len(page.cards) == 4
    assert sum(chart.isVisible() for chart in page.findChildren(MetricChartCard)) == 4
    page.edit_layout.setChecked(True)
    await refresh(page)
    page._change(0, "later")
    assert page._write_task is not None
    await page._write_task
    assert page.saved.layout.widgets[1].public_id == original.layout.widgets[0].public_id
    page._change(1, "size")
    await page._write_task
    page._change(0, "remove")
    await page._write_task
    assert len(list(page.saved.layout.widgets)) == 3
    page._undo()
    await page._write_task
    await refresh(page)
    assert len(list(page.saved.layout.widgets)) == 4
    page.resize(680, 520)
    assert page.grid.columnCount() <= 2
    assert page.minimumSizeHint().width() <= 680
    saved = await backend.load(page.dates())
    assert saved == page.saved
    await page.drain()


async def test_widget_preview_cancel_and_conflict_keep_input(
    qtbot: QtBot, database: Database
) -> None:
    backend = InsightsBackend(database, google_on=False)
    original = await backend.load(DATES)
    parent = QWidget()
    qtbot.addWidget(parent)
    editor = WidgetEditor(backend, DATES, (), (), parent)
    editor.show()
    editor.title.setText("Owner title <plain>")
    editor.chart.setCurrentIndex(editor.chart.findData(ChartKind.BAR.value))
    assert editor.has_unsaved_input()
    editor._preview()
    await editor.drain()
    assert editor.findChild(MetricChartCard) is not None
    assert (await backend.load(DATES)) == original
    editor.reject()
    assert (await backend.load(DATES)) == original
    page = InsightsPage()
    qtbot.addWidget(page)
    page.show()
    page.attach(backend, "America/Toronto", (), ())
    await refresh(page)
    page._edit_widget()
    dialog = page.dialogs[-1]
    assert isinstance(dialog, WidgetEditor)
    dialog.title.setText("Keep my input")
    saved = await backend.load(DATES)
    await backend.save(DashboardLayout(), saved.revision, new_public_id())
    dialog._save()
    await dialog.drain()
    assert dialog.title.text() == "Keep my input"
    assert "changed" in dialog.message.text()
    assert (await backend.load(DATES)).layout.widgets == ()
    await page.drain()


async def test_custom_log_correct_delete_restore_and_stale_details(
    qtbot: QtBot,
    database: Database,
) -> None:
    backend = InsightsBackend(database, google_on=False)
    parent = QWidget()
    qtbot.addWidget(parent)
    manager = MetricManager(backend, DATES.zone, (), parent)
    manager.name.setText("Reading")
    manager.unit.setText("minutes")
    manager.scale.setValue(6)
    await manager._define()
    assert len(manager.definitions) == 1
    manager.value.setText("1.123456")
    manager.day.setDate(QDate(2026, 11, 8))
    manager._mutate("create")
    await manager.drain()
    assert len(list(manager.rows)) == 1
    # Repeating an acknowledged identical log is a new observation.
    manager.value.setText("1.123456")
    manager._mutate("create")
    await manager.drain()
    assert len(list(manager.rows)) == 2
    manager._select(0)
    await manager.drain()
    assert "create" in manager.history_label.text()
    selected = manager.selected
    manager.value.setText("9.999999")
    manager._select(1)
    assert manager.selected == selected and manager.value.text() == "9.999999"
    assert "Discard input" in manager.message.text()
    manager.value.setText("0")
    manager._mutate("edit")
    await manager.drain()
    assert manager.rows[0].value_scaled == 0
    definition = manager.definition()
    descriptor = custom_descriptor(definition)
    widget = (
        starter_layout(DATES)
        .widgets[0]
        .model_copy(
            update={
                "query": starter_layout(DATES)
                .widgets[0]
                .query.model_copy(
                    update={
                        "metric_key": descriptor.key,
                        "reducer": descriptor.reducer,
                    }
                )
            }
        )
    )
    result = await backend.metrics.query(widget.query)
    assert result.total == Decimal("1.123456")
    detail = MetricDetails(backend, result, None, parent)
    await detail.load()
    assert len(list(detail.rows)) == 2
    manager._select(0)
    manager._mutate("delete")
    await manager.drain()
    await detail.load(next_page=True)
    assert "Data changed; refreshed" in detail.message.text()
    assert len(list(detail.rows)) == 1
    manager._select(0)
    manager._mutate("restore")
    await manager.drain()
    assert [row.deleted for row in manager.rows][0] is False
    manager._select(0)
    manager._mutate("undo")
    await manager.drain()
    assert [row.deleted for row in manager.rows][0] is True
    await detail.drain()


async def test_late_refresh_never_replaces_new_range(
    qtbot: QtBot,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = InsightsBackend(database, google_on=False)
    page = InsightsPage()
    qtbot.addWidget(page)
    page.show()
    page.attach(backend, "America/Toronto", (), ())
    await refresh(page)
    real = backend.metrics.query_many
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed(
        queries: tuple[MetricQuery, ...], *, captured_at: datetime | None = None
    ) -> tuple[MetricQueryResult, ...]:
        started.set()
        await release.wait()
        return await real(queries, captured_at=captured_at)

    monkeypatch.setattr(backend.metrics, "query_many", delayed)
    page.refresh()
    await started.wait()
    old = page._refresh_task
    page.first.setDate(page.last.date())
    release.set()
    await refresh(page)
    assert old is not None and old.done()
    charts = page.findChildren(MetricChartCard)
    assert all(len(chart.display.samples) <= 1 for chart in charts if chart.isVisible())
    await page.drain()


async def test_canonical_route_narrow_window_and_unsaved_quit_guard(
    qtbot: QtBot,
    database: Database,
    qapp: QApplication,
    themed: None,
) -> None:
    apply_theme(qapp, ThemeMode.LIGHT, ColorScheme.STUDIO)

    class Backend(FakeBackend):
        async def insights(self) -> InsightsBackend:
            return InsightsBackend(database, google_on=False)

    window = await window_for(qtbot, Backend())
    window.resize(680, 520)
    window._navigate(PageRoute(page="insights"))
    await asyncio.gather(*tuple(window.insights_page.jobs))
    await refresh(window.insights_page)
    assert window.workspace.current_page() == "insights"
    # Other existing pages can impose a larger native minimum. Insights must not
    # increase it; its own 680 px single-column geometry is tested separately.
    other_minimum = max(
        page.minimumSizeHint().width()
        for key, page in window.workspace._pages.items()
        if key != "insights"
    )
    assert window.insights_page.minimumSizeHint().width() <= other_minimum
    assert window.insights_page.saved is not None
    window.insights_page._edit_widget()
    dialog = window.insights_page.dialogs[-1]
    assert isinstance(dialog, WidgetEditor)
    dialog.title.setText("Unsaved title")
    assert window._unsaved_input() == "your Insights changes"
    dialog.reject()
    assert window._unsaved_input() is None
    await window.insights_page.drain()
    window._quit_confirmed = True
    window.close()
    await window.shutdown()


async def test_owner_zone_rollover_and_saved_override_are_preserved(
    qtbot: QtBot,
    database: Database,
) -> None:
    page = InsightsPage()
    qtbot.addWidget(page)
    page.show()
    backend = InsightsBackend(database, google_on=False)
    page.attach(backend, DATES.zone, (), ())
    await refresh(page)
    old = page.dates()
    page.follow_day(old.last + timedelta(days=1), "Asia/Tokyo")
    assert page.dates().last > old.last
    assert page.dates().zone == "Asia/Tokyo"
    custom = starter_layout(DATES).widgets[0].model_copy(update={"use_global_range": False})
    editor = WidgetEditor(backend, page.dates(), (), (), page, custom)
    assert editor.spec().query.dates.zone == DATES.zone
    assert editor.spec().query.dates.first == DATES.first
    await page.drain()


async def test_source_materialization_stays_off_gui_thread_and_cancellation_keeps_one_worker(
    qtbot: QtBot,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import AsyncSession

    from mailbrief.storage.metric_sources import MetricSources, read_metric_sources

    backend = InsightsBackend(database, google_on=False)
    original = read_metric_sources
    started, release = threading.Event(), threading.Event()
    gui_thread = threading.get_ident()
    calls: list[int] = []

    async def read(
        session: AsyncSession, query: MetricQuery, now: datetime, *, google_on: bool
    ) -> MetricSources:
        calls.append(threading.get_ident())
        if len(calls) == 1:
            started.set()
            await asyncio.to_thread(release.wait)
        return await original(session, query, now, google_on=google_on)

    monkeypatch.setattr(metrics_module, "read_metric_sources", read)
    query = starter_layout(DATES).widgets[0].query
    first = asyncio.create_task(backend.metrics.query(query))
    await asyncio.to_thread(started.wait)
    first.cancel()
    await asyncio.sleep(0)
    first.cancel()
    second = asyncio.create_task(backend.metrics.query(query))
    await asyncio.sleep(0.03)
    assert len(calls) == 1 and calls[0] != gui_thread
    release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert isinstance(results[0], asyncio.CancelledError)
    assert isinstance(results[1], MetricQueryResult)
    assert len(calls) == 2
    comparison = await backend.metrics.compare(query)
    assert comparison.current.contributions == ()
    assert comparison.previous is not None


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
async def test_gallery(
    qtbot: QtBot, database: Database, themed: None, mode: ThemeMode, qapp: QApplication
) -> None:
    apply_theme(qapp, mode, ColorScheme.STUDIO)
    page = InsightsPage()
    qtbot.addWidget(page)
    page.resize(1000, 720)
    page.show()
    page.attach(InsightsBackend(database, google_on=False), "America/Toronto", (), ())
    await refresh(page)
    if os.environ.get("MAILBRIEF_UI_SHOTS"):
        qapp.processEvents()
        save_shot(page.grab(), f"insights-{mode.value}")
        page._edit_widget()
        dialog = page.dialogs[-1]
        dialog.show()
        qapp.processEvents()
        save_shot(dialog.grab(), f"widget-editor-{mode.value}")
        dialog.hide()
        page._manage()
        manager = page.dialogs[-1]
        await manager.drain()
        manager.show()
        qapp.processEvents()
        save_shot(manager.grab(), f"metric-manager-{mode.value}")
        manager.hide()
    await page.drain()
