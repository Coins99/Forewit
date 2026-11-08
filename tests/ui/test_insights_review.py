"""P4 author-review regressions for saves, list generations and preview identity."""

import asyncio
from decimal import Decimal

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.domain.common import new_public_id
from mailbrief.domain.dashboards import ChartKind, DashboardLayout
from mailbrief.domain.metric_entries import (
    MeasurementKind,
    MetricDefinition,
    MetricDefinitionSpec,
    MetricEntry,
    MetricEntryInput,
)
from mailbrief.domain.projects import ProjectEdit
from mailbrief.services.projects import ProjectService
from mailbrief.storage.database import Database
from mailbrief.ui.insights_backend import InsightsBackend
from mailbrief.ui.insights_editors import (
    InsightsDialog,
    MetricManager,
    WidgetEditor,
    custom_descriptor,
)
from mailbrief.ui.insights_view import InsightsPage
from mailbrief.ui.metric_chart import MetricChartCard
from tests.ui.test_insights import DATES, refresh
from tests.ui.test_palette import window_for
from tests.ui.test_workflow import FakeBackend


async def definition(backend: InsightsBackend, name: str, scale: int = 0) -> MetricDefinition:
    return await backend.define(
        MetricDefinitionSpec(
            name=name,
            unit="units",
            kind=MeasurementKind.AMOUNT,
            scale=scale,
        ),
        new_public_id(),
    )


def choose(manager: MetricManager, identity: str) -> None:
    manager.metric_choice.blockSignals(True)
    manager.metric_choice.setCurrentIndex(manager.metric_choice.findData(identity))
    manager.metric_choice.blockSignals(False)


async def test_finishing_a_read_cannot_unlock_an_inflight_save(qtbot: QtBot) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = InsightsDialog("Test save", parent)
    read_release, save_release, read_finished = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def read() -> None:
        await read_release.wait()
        read_finished.set()

    async def save() -> None:
        await save_release.wait()

    dialog.run(read())
    dialog.write(save())
    try:
        read_release.set()
        await read_finished.wait()
        await asyncio.sleep(0)
        assert dialog._saving and not dialog.isEnabled()
    finally:
        save_release.set()
        await dialog.drain()


async def test_switching_metrics_invalidates_the_previous_entry_context(
    qtbot: QtBot,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    backend = InsightsBackend(database, google_on=False)
    first = await definition(backend, "Count")
    second = await definition(backend, "Amount", scale=2)
    await backend.create(
        first.public_id,
        MetricEntryInput(
            value_scaled=25,
            local_date=DATES.last,
            zone=DATES.zone,
        ),
        new_public_id(),
    )
    manager = MetricManager(backend, DATES.zone, (), parent)
    await manager.reload()
    choose(manager, first.public_id)
    await manager._entries()
    manager._select(0)
    await manager.drain()
    initial_selection = manager.selected
    assert initial_selection is not None
    real = backend.entries
    started, release = asyncio.Event(), asyncio.Event()

    async def blocked(
        identity: str, after: tuple[str, str] | None = None
    ) -> tuple[MetricEntry, ...]:
        if identity == second.public_id:
            started.set()
            await release.wait()
        return await real(identity, after)

    monkeypatch.setattr(backend, "entries", blocked)
    manager.metric_choice.setCurrentIndex(manager.metric_choice.findData(second.public_id))
    try:
        await started.wait()
        assert manager.selected is None
        assert not manager.rows and not manager.entries_list.isEnabled()
        assert manager.value.text() == ""
    finally:
        release.set()
        await manager.drain()


async def test_late_list_page_cannot_replace_a_newer_read_of_the_same_metric(
    qtbot: QtBot,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    backend = InsightsBackend(database, google_on=False)
    metric = await definition(backend, "Counter")
    value = MetricEntryInput(value_scaled=1, local_date=DATES.last, zone=DATES.zone)
    await backend.create(metric.public_id, value, new_public_id())
    manager = MetricManager(backend, DATES.zone, (), parent)
    await manager.reload()
    original = backend.entries
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def delayed(
        identity: str, after: tuple[str, str] | None = None
    ) -> tuple[MetricEntry, ...]:
        nonlocal calls
        rows = await original(identity, after)
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
        return rows

    monkeypatch.setattr(backend, "entries", delayed)
    manager.run(manager._entries())
    await started.wait()
    await backend.create(metric.public_id, value, new_public_id())
    try:
        await manager._entries()
    finally:
        release.set()
        await manager.drain()
    assert len(manager.rows) == 2


async def test_widget_preview_and_saved_chart_use_the_same_inherited_filters(
    qtbot: QtBot,
    database: Database,
) -> None:
    backend = InsightsBackend(database, google_on=False)
    async with database.session() as session:
        projects = ProjectService(session)
        first = await projects.create_project(ProjectEdit(name="First"))
        second = await projects.create_project(ProjectEdit(name="Second"))
    metric = await definition(backend, "Measured")
    for project, amount in ((first, 1), (second, 2)):
        await backend.create(
            metric.public_id,
            MetricEntryInput(
                value_scaled=amount,
                local_date=DATES.last,
                zone=DATES.zone,
                project_public_id=project.public_id,
            ),
            new_public_id(),
        )
    page = InsightsPage()
    qtbot.addWidget(page)
    page.show()
    page.attach(backend, DATES.zone, (first, second), ())
    page.first.setDate(QDate(DATES.first.year, DATES.first.month, DATES.first.day))
    page.last.setDate(QDate(DATES.last.year, DATES.last.month, DATES.last.day))
    page.project.setCurrentIndex(page.project.findData(first.public_id))
    await refresh(page)
    page._edit_widget()
    editor = page.dialogs[-1]
    assert isinstance(editor, WidgetEditor)
    editor.metric_choice.setCurrentIndex(
        editor.metric_choice.findData(custom_descriptor(metric).key)
    )
    editor.chart.setCurrentIndex(editor.chart.findData(ChartKind.NUMBER.value))
    editor._preview()
    await editor.drain()
    chart = editor.findChild(MetricChartCard)
    assert chart is not None and chart.display.total == Decimal(1)
    preview_total = chart.display.total
    assert editor.spec().query.project_public_id is None  # Inheritance stays dynamic.
    editor._save()
    await editor.drain()
    await refresh(page)
    actual = [
        c
        for c in page.findChildren(MetricChartCard)
        if c.isVisible() and c.display.title == metric.name
    ]
    assert len(actual) == 1 and actual[0].display.total == preview_total
    await page.drain()


async def test_preview_includes_comparison_and_worker_keeps_the_captured_clock(
    qtbot: QtBot,
    database: Database,
) -> None:
    from datetime import UTC, datetime

    from mailbrief.services.dashboards import starter_layout

    parent = QWidget()
    qtbot.addWidget(parent)
    backend = InsightsBackend(database, google_on=False)
    editor = WidgetEditor(backend, DATES, (), (), parent)
    editor.previous.setChecked(True)
    editor._preview()
    await editor.drain()
    chart = editor.findChild(MetricChartCard)
    assert chart is not None and "Previous:" in chart.display.caption
    captured = datetime(2026, 11, 8, 12, tzinfo=UTC)
    result = await backend.metrics.compare(
        starter_layout(DATES).widgets[0].query, captured_at=captured
    )
    assert result.current.captured_at_utc == captured
    assert result.previous is not None and result.previous.captured_at_utc == captured


async def test_a_new_definition_becomes_the_selected_logging_metric(
    qtbot: QtBot,
    database: Database,
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    backend = InsightsBackend(database, google_on=False)
    await definition(backend, "First")
    manager = MetricManager(backend, DATES.zone, (), parent)
    await manager.reload()
    manager.name.setText("Second")
    manager.unit.setText("amount")
    manager.scale.setValue(2)
    await manager._define()
    assert manager.definition().name == "Second" and manager.definition().scale == 2
    await manager.drain()


async def test_cancelling_a_profile_read_joins_its_session_before_returning(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from mailbrief.domain.dashboards import SavedDashboard
    from mailbrief.domain.metrics import MetricDateRange
    from mailbrief.services.dashboards import DashboardService

    original = DashboardService.load
    started, release = asyncio.Event(), asyncio.Event()

    async def held(service: DashboardService, dates: MetricDateRange) -> SavedDashboard:
        saved = await original(service, dates)
        started.set()
        await release.wait()
        return saved

    monkeypatch.setattr(DashboardService, "load", held)
    backend = InsightsBackend(database, google_on=False)
    read = asyncio.create_task(backend.load(DATES))
    await started.wait()
    read.cancel()
    await asyncio.sleep(0)
    read.cancel()
    try:
        await asyncio.sleep(0)
        assert not read.done()
    finally:
        release.set()
        outcome = await asyncio.gather(read, return_exceptions=True)
    assert isinstance(outcome[0], asyncio.CancelledError)


async def test_controls_from_an_old_render_cannot_mutate_a_new_layout(
    qtbot: QtBot,
    database: Database,
) -> None:
    backend = InsightsBackend(database, google_on=False)
    page = InsightsPage()
    qtbot.addWidget(page)
    page.show()
    page.attach(backend, DATES.zone, (), ())
    await refresh(page)
    assert page.saved is not None
    updated = await backend.save(
        DashboardLayout(widgets=page.saved.layout.widgets[::-1]),
        page.saved.revision,
        new_public_id(),
    )
    page.saved = updated  # Commit acknowledged; the replacement render is still loading.
    page._change(0, "remove")
    if page._write_task is not None:
        await page._write_task
    assert await backend.load(DATES) == updated
    await page.drain()


async def test_keyboard_move_focus_follows_the_current_widget_not_a_deferred_delete(
    qtbot: QtBot,
    qapp: QApplication,
    database: Database,
) -> None:
    page = InsightsPage()
    qtbot.addWidget(page)
    page.show()
    page.attach(InsightsBackend(database, google_on=False), DATES.zone, (), ())
    page.edit_layout.setChecked(True)
    await refresh(page)
    assert page.saved is not None
    identity = page.saved.layout.widgets[0].public_id
    name = f"insights-later-{identity}"
    button = page.cards[0].findChild(QPushButton, name)
    assert button is not None
    qapp.processEvents()
    button.setFocus()
    QTest.keyClick(button, Qt.Key.Key_Space)
    assert page._write_task is not None
    await page._write_task
    current = page.cards[1].findChild(QPushButton, name)
    assert current is not None and current is not button
    qapp.processEvents()
    assert current.hasFocus()
    await page.drain()


async def test_calendar_access_and_disconnect_refresh_insights_external_scope(
    qtbot: QtBot,
    database: Database,
) -> None:
    from mailbrief.domain.routes import PageRoute

    class Backend(FakeBackend):
        async def insights(self) -> InsightsBackend:
            return InsightsBackend(database, google_on=False)

    window = await window_for(qtbot, Backend())
    try:
        window._set_account(None)
        window._calendar_allowed = None
        window._navigate(PageRoute(page="insights"))
        await asyncio.gather(*tuple(window.insights_page.jobs))
        await refresh(window.insights_page)
        insights = window.insights_page.backend
        assert insights is not None
        assert not bool(insights.metrics.google_on)
        window._set_account("owner@example.com")
        window._calendar_allowed = True
        await window._refresh_calendar()
        assert bool(insights.metrics.google_on)
        await window._disconnect()
        assert not bool(insights.metrics.google_on)
    finally:
        window._quit_confirmed = True
        window.close()
        await window.shutdown()


async def test_committed_log_still_refreshes_charts_when_list_reload_fails(
    qtbot: QtBot,
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    backend = InsightsBackend(database, google_on=False)
    metric = await definition(backend, "Counter")
    manager = MetricManager(backend, DATES.zone, (), parent)
    await manager.reload()
    original = backend.entries

    async def failed(
        identity: str, after: tuple[str, str] | None = None
    ) -> tuple[MetricEntry, ...]:
        raise RuntimeError("Synthetic read failure.")

    monkeypatch.setattr(backend, "entries", failed)
    changed = QSignalSpy(manager.changed)
    manager.value.setText("3")
    manager._mutate("create")
    await manager.drain()
    assert len(await original(metric.public_id)) == 1
    assert changed.count() == 1 and "Saved" in manager.message.text()
    assert manager.value.text() == ""


async def test_closed_dialogs_are_released_after_their_pending_jobs(
    qtbot: QtBot,
    database: Database,
) -> None:
    page = InsightsPage()
    qtbot.addWidget(page)
    page.show()
    page.attach(InsightsBackend(database, google_on=False), DATES.zone, (), ())
    await refresh(page)
    page._edit_widget()
    editor = page.dialogs[-1]
    editor.show()
    editor.reject()
    await page.drain()
    assert all(dialog is not editor for dialog in page.dialogs)
