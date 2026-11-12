"""Regressions from the review and macOS UI check of Insights (2026-11-09). Each test is one
defect an owner could meet on the page or in its dialogs; all use synthetic records."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QComboBox, QLabel, QPushButton, QScrollArea, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.domain.common import new_public_id
from mailbrief.domain.dashboards import ChartKind, DashboardLayout, WidgetSpec
from mailbrief.domain.metric_entries import (
    MeasurementKind,
    MetricDefinition,
    MetricDefinitionSpec,
    MetricEntryInput,
)
from mailbrief.domain.metrics import MetricQuery, builtin_metric
from mailbrief.domain.projects import AreaEdit, LifeArea, Project, ProjectEdit
from mailbrief.services.projects import ProjectService
from mailbrief.storage.database import Database
from mailbrief.ui.insights_backend import InsightsBackend
from mailbrief.ui.insights_editors import (
    GENERAL_REFUSAL,
    MetricManager,
    WidgetEditor,
    custom_descriptor,
    filter_note,
)
from mailbrief.ui.insights_view import InsightsPage, MetricDetails
from mailbrief.ui.metric_chart import MetricChartCard
from tests.ui.test_insights import DATES, refresh

NOW = datetime(2026, 11, 8, 17, tzinfo=UTC)  # Noon on DATES.last in Toronto.


def spec(
    key: str = "tasks.completions",
    chart: ChartKind = ChartKind.NUMBER,
    *,
    definition: MetricDefinition | None = None,
) -> WidgetSpec:
    """A widget titled with its metric's own name, as one saved with a blank title is."""
    descriptor = custom_descriptor(definition) if definition else builtin_metric(key)
    return WidgetSpec(
        public_id=new_public_id(),
        title=descriptor.label,
        chart=chart,
        query=MetricQuery(metric_key=descriptor.key, reducer=descriptor.reducer, dates=DATES),
    )


async def metric(backend: InsightsBackend, name: str = "Steps") -> MetricDefinition:
    return await backend.define(
        MetricDefinitionSpec(name=name, unit="steps", kind=MeasurementKind.AMOUNT, scale=0),
        new_public_id(),
    )


async def log(
    backend: InsightsBackend,
    definition: MetricDefinition,
    value: int,
    day: date = DATES.last,
    project: str | None = None,
) -> None:
    await backend.create(
        definition.public_id,
        MetricEntryInput(
            value_scaled=value, local_date=day, zone=DATES.zone, project_public_id=project
        ),
        new_public_id(),
    )


async def save_layout(backend: InsightsBackend, *widgets: WidgetSpec) -> None:
    saved = await backend.load(DATES)
    await backend.save(DashboardLayout(widgets=widgets), saved.revision, new_public_id())


async def open_page(
    qtbot: QtBot,
    backend: InsightsBackend,
    projects: tuple[Project, ...] = (),
    areas: tuple[LifeArea, ...] = (),
) -> InsightsPage:
    """The page on DATES with the clock at NOW, whatever day the test runs on."""
    backend.metrics.clock = lambda: NOW
    page = InsightsPage()
    qtbot.addWidget(page)
    page.resize(1000, 760)
    page.show()
    page.attach(backend, DATES.zone, projects, areas)
    page.first.setDate(QDate(DATES.first.year, DATES.first.month, DATES.first.day))
    page.last.setDate(QDate(DATES.last.year, DATES.last.month, DATES.last.day))
    await refresh(page)
    return page


def scrolled_fields(dialog: QWidget) -> QWidget:
    """The widget a dialog scrolls: its fields."""
    scroll = dialog.findChild(QScrollArea)
    assert scroll is not None
    content = scroll.widget()
    assert content is not None
    return content


def chart_of(card: QWidget) -> MetricChartCard:
    chart = card.findChild(MetricChartCard)
    assert chart is not None
    return chart


@pytest.fixture
def parent(qtbot: QtBot) -> QWidget:
    """A window for a dialog under test, alive for the whole test: a dialog goes with its
    parent, and a parent only a helper referred to would be collected."""
    window = QWidget()
    qtbot.addWidget(window)
    return window


async def test_archive_and_reactivate_work_from_the_manager(
    qtbot: QtBot, database: Database, parent: QWidget
) -> None:
    backend = InsightsBackend(database, google_on=False)
    await metric(backend)
    manager = MetricManager(backend, DATES.zone, (), parent)
    await manager.reload()
    assert manager.archive_button.text() == "Archive metric"
    manager.archive_button.click()
    await manager.drain()
    assert (await backend.definitions())[0].archived
    assert manager.archive_button.text() == "Reactivate metric"
    assert not manager.entry_buttons["create"].isEnabled()
    assert "archived" in manager.message.text()
    manager.archive_button.click()
    await manager.drain()
    assert not (await backend.definitions())[0].archived
    assert manager.entry_buttons["create"].isEnabled()


async def test_a_refresh_updates_the_cards_where_they_are(qtbot: QtBot, database: Database) -> None:
    backend = InsightsBackend(database, google_on=False)
    steps = await metric(backend)
    for back in range(5):
        await log(backend, steps, 7000 + back, DATES.last - timedelta(days=back))
    widget = spec(chart=ChartKind.BAR, definition=steps)
    await save_layout(backend, widget, spec("tasks.completions"))
    page = await open_page(qtbot, backend)
    cards = list(page.cards)
    chart = chart_of(cards[0])
    chart.view_data.click()
    chart.plot.setFocus()
    chart.plot.select(3)
    await log(backend, steps, 500)
    await refresh(page)  # What the minute timer and every data change do.
    assert page.cards == cards and chart_of(page.cards[0]) is chart
    assert chart.display.total == Decimal(35510)  # The fresh value, in the same card.
    assert chart.table.isVisible() and chart.plot.selection == 3
    assert page.focusWidget() is chart.plot
    # Its details open on the refreshed result, not the one the card was built with.
    page._inspect(widget.public_id, None)
    details = page.dialogs[-1]
    assert isinstance(details, MetricDetails)
    await details.drain()
    assert len(details.rows) == 6 and "Data changed" not in details.message.text()
    details.reject()
    # A change of layout or mode still builds new cards.
    page.edit_layout.setChecked(True)
    await refresh(page)
    assert page.cards != cards
    await page.drain()


async def test_planned_workload_starts_today_on_the_dashboard_range(
    qtbot: QtBot, database: Database
) -> None:
    backend = InsightsBackend(database, google_on=False)
    planned = spec("workload.planned")
    await save_layout(backend, planned)
    page = await open_page(qtbot, backend)
    # The dashboard's dates begin a week back; the widget shows the part from today.
    assert "2026-11-08 to 2026-11-08" in chart_of(page.cards[0]).display.caption
    page._edit_widget(planned)
    editor = page.dialogs[-1]
    assert isinstance(editor, WidgetEditor)
    assert "Dates before today are left out." in editor.hint.text()
    editor._preview()
    await editor.drain()
    assert editor.findChild(MetricChartCard) is not None
    assert "Preview only" in editor.message.text()
    # A day later those dates are wholly past: the card and the preview say what to do.
    backend.metrics.clock = lambda: NOW + timedelta(days=1)
    editor._preview()
    await editor.drain()
    assert editor.message.text().startswith("Planned workload covers today and later.")
    editor.reject()
    await refresh(page)
    label = page.cards[0].findChild(QLabel)
    assert page.cards[0].findChild(MetricChartCard) is None and label is not None
    assert "Planned workload covers today and later." in label.text()
    await page.drain()


async def test_the_date_fields_always_hold_a_range_the_queries_accept(
    qtbot: QtBot, database: Database
) -> None:
    page = await open_page(qtbot, InsightsBackend(database, google_on=False))
    page.first.setDate(QDate(2026, 11, 20))  # After Through: Through follows.
    assert page.last.date() == QDate(2026, 11, 20)
    page.last.setDate(QDate(2026, 11, 3))  # Before From: From follows.
    assert page.first.date() == QDate(2026, 11, 3)
    page.first.setDate(QDate(2024, 1, 1))  # Over 366 dates: Through comes in.
    assert (page.dates().first, page.dates().days) == (date(2024, 1, 1), 366)
    page.last.setDate(QDate(2026, 11, 8))  # And From, when Through moves out.
    assert (page.dates().last, page.dates().days) == (date(2026, 11, 8), 366)
    await refresh(page)
    assert page.status.text().startswith("Local records only.")
    page.add.click()
    editor = page.dialogs[-1]
    assert isinstance(editor, WidgetEditor)
    # A widget's own range keeps itself in order the same way.
    editor.global_range.setChecked(False)
    editor.first.setDate(editor.last.date().addDays(5))
    assert editor.last.date() == editor.first.date()
    assert editor._checked() is not None
    editor.reject()
    await page.drain()


async def test_what_a_button_answers_is_shown_outside_the_scrolled_fields(
    qtbot: QtBot, database: Database, parent: QWidget
) -> None:
    backend = InsightsBackend(database, google_on=False)
    manager = MetricManager(backend, DATES.zone, (), parent)
    await manager.reload()
    assert not scrolled_fields(manager).isAncestorOf(manager.message)
    manager.write(manager._define())  # Create metric, with nothing typed.
    await manager.drain()
    assert manager.message.text() == "Give the new metric a name and a unit."
    manager._mutate("create")  # Return in the value field, before any metric exists.
    await manager.drain()
    assert manager.message.text() == "Create a metric first, then log its values."
    await metric(backend)
    await manager.reload()
    manager.value.setText("12x")
    manager.entry_buttons["create"].click()
    await manager.drain()
    assert manager.message.text() == "Enter a finite decimal number without grouping separators."
    assert manager.value.text() == "12x"  # The input is kept.
    manager.value.setText("1.5")
    manager.entry_buttons["create"].click()
    await manager.drain()
    assert manager.message.text() == "The value has more decimal places than this metric allows."
    manager._mutate("delete")  # Not reachable by its button: nothing is selected.
    await manager.drain()
    assert manager.message.text() == "Select an observation in the list first."


async def test_preview_and_save_refuse_the_same_input_in_the_same_words(
    qtbot: QtBot, database: Database, parent: QWidget
) -> None:
    editor = WidgetEditor(InsightsBackend(database, google_on=False), DATES, (), (), parent)
    assert not scrolled_fields(editor).isAncestorOf(editor.message)
    for typed, reason in (
        ("1,200", "Enter the target as a plain number, such as 8000 or 2.5."),
        ("abc", "Enter the target as a plain number, such as 8000 or 2.5."),
        ("1" * 30, GENERAL_REFUSAL),
    ):
        editor.target.setText(typed)
        editor._preview()
        assert editor.message.text() == reason and not editor.jobs  # Nothing was started.
        editor.message.setText("")
        editor._save()
        assert editor.message.text() == reason
    editor.target.clear()
    editor.global_range.setChecked(False)
    editor.zone_field.setText("Toronto")
    editor._save()
    assert editor.message.text() == "The time zone is unknown."
    editor.zone_field.setText(DATES.zone)
    editor.group.setCurrentIndex(editor.group.findData("project"))
    editor._save()
    assert editor.message.text() == "A number card needs an ungrouped total."
    editor.metric_choice.setCurrentIndex(-1)
    editor._save()
    assert editor.message.text() == "Choose a metric for this widget."


async def test_entry_buttons_follow_the_chosen_metric_and_observation(
    qtbot: QtBot, database: Database, parent: QWidget
) -> None:
    backend = InsightsBackend(database, google_on=False)
    manager = MetricManager(backend, DATES.zone, (), parent)
    await manager.reload()

    def enabled() -> set[str]:
        return {name for name, button in manager.entry_buttons.items() if button.isEnabled()}

    assert enabled() == set() and not manager.archive_button.isEnabled()  # No metric yet.
    manager.name.setText("Steps")
    manager.unit.setText("steps")
    await manager._define()
    assert enabled() == {"create"} and manager.archive_button.isEnabled()
    manager.value.setText("8000")
    manager._mutate("create")
    await manager.drain()
    manager.entries_list.setCurrentRow(0)
    await manager.drain()
    assert enabled() == {"create", "edit", "delete", "undo"}  # A live observation.
    manager.entry_buttons["delete"].click()
    await manager.drain()
    assert enabled() == {"create"}  # The list reloaded with nothing selected.
    manager.entries_list.setCurrentRow(0)
    await manager.drain()
    assert enabled() == {"create", "restore", "undo"}  # A deleted one.
    manager._discard_input()
    assert enabled() == {"create"}


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
async def test_return_or_enter_opens_a_contributing_record_once(
    qtbot: QtBot, database: Database, key: Qt.Key
) -> None:
    backend = InsightsBackend(database, google_on=False)
    steps = await metric(backend)
    await log(backend, steps, 8000)
    parent = QWidget()
    qtbot.addWidget(parent)
    result = await backend.metrics.query(spec(definition=steps).query)
    details = MetricDetails(backend, result, None, parent)
    await details.load()
    details.show()
    activated = QSignalSpy(details.table.itemActivated)
    QTest.keyClick(details.table, key)
    assert activated.count() == 1
    # An observation has no page of its own; the dialog says where to correct it.
    assert "Manage metrics" in details.message.text()
    header = details.table.horizontalHeader()
    assert header.stretchLastSection() and not details.table.verticalHeader().isVisible()
    await details.drain()


async def test_every_combo_box_takes_tab_and_no_scroll_area_does(
    qtbot: QtBot, database: Database
) -> None:
    page = await open_page(qtbot, InsightsBackend(database, google_on=False))
    page._edit_widget()
    page._manage()
    await page.dialogs[-1].drain()
    boxes = {
        box.objectName(): box.focusPolicy()
        for box in page.findChildren(QComboBox)
        if box.objectName().startswith("insights-")
    }
    # The page's two filters, the editor's seven choices and the manager's three: macOS
    # leaves a combo box out of the Tab order unless it is asked for.
    assert len(boxes) == 12
    assert set(boxes.values()) == {Qt.FocusPolicy.StrongFocus}
    areas = [
        area for area in page.findChildren(QScrollArea) if area.objectName().startswith("insights-")
    ]
    assert len(areas) == 3
    assert all(area.focusPolicy() == Qt.FocusPolicy.NoFocus for area in areas)
    await page.drain()


async def test_a_blank_title_follows_the_metric(
    qtbot: QtBot, database: Database, parent: QWidget
) -> None:
    backend = InsightsBackend(database, google_on=False)
    saved = spec("tasks.current_overdue")  # Its title is the metric's own name.
    editor = WidgetEditor(backend, DATES, (), (), parent, saved)
    assert editor.title.text() == "" and editor.title.placeholderText() == "Currently overdue"
    assert not editor.has_unsaved_input()
    assert editor.spec().title == "Currently overdue"
    editor.metric_choice.setCurrentIndex(editor.metric_choice.findData("tasks.created"))
    assert editor.title.placeholderText() == "Tasks created"
    assert editor.spec().title == "Tasks created"
    # A title the owner wrote stays theirs when the metric changes.
    mine = saved.model_copy(update={"title": "Late"})
    editor = WidgetEditor(backend, DATES, (), (), parent, mine)
    editor.metric_choice.setCurrentIndex(editor.metric_choice.findData("tasks.created"))
    assert editor.title.text() == "Late" and editor.spec().title == "Late"


async def test_the_observation_list_opens_on_the_newest(
    qtbot: QtBot, database: Database, parent: QWidget
) -> None:
    backend = InsightsBackend(database, google_on=False)
    steps = await metric(backend)
    for back in range(3):
        await log(backend, steps, 100 + back, DATES.last - timedelta(days=back))
    manager = MetricManager(backend, DATES.zone, (), parent)
    await manager.reload()
    assert [row.local_date for row in manager.rows] == [
        DATES.last - timedelta(days=back) for back in range(3)
    ]
    manager.day.setDate(QDate(2026, 11, 9))
    manager.value.setText("999")
    manager._mutate("create")
    await manager.drain()
    assert manager.rows[0].value_scaled == 999  # The value just logged is the first row.
    assert manager.entries_list.item(0).text().startswith("2026-11-09")
    assert "Newest first" in manager.message.text()


async def test_undo_is_withdrawn_once_the_layout_moves_on(qtbot: QtBot, database: Database) -> None:
    page = await open_page(qtbot, InsightsBackend(database, google_on=False))
    page.edit_layout.setChecked(True)
    await refresh(page)

    async def change(index: int, operation: str) -> None:
        page._change(index, operation)
        assert page._write_task is not None
        await page._write_task

    assert page.saved is not None
    widgets = page.saved.layout.widgets
    await change(0, "remove")
    assert not page.undo.isHidden()
    await change(0, "later")  # The layout moved on: that undo can only fail now.
    assert page.undo.isHidden() and page._removed is None
    await change(0, "remove")
    page.undo.click()  # Straight after a removal, it still puts the widget back.
    assert page._write_task is not None
    await page._write_task
    await refresh(page)
    assert page.saved is not None and len(page.saved.layout.widgets) == len(widgets) - 1
    assert page.undo.isHidden()
    page.reset.click()  # A reset offers its own.
    await page._write_task
    assert not page.undo.isHidden()
    await page.drain()


async def test_logging_keeps_the_keyboard_in_the_value_field(
    qtbot: QtBot, database: Database, parent: QWidget
) -> None:
    backend = InsightsBackend(database, google_on=False)
    await metric(backend)
    manager = MetricManager(backend, DATES.zone, (), parent)
    manager.show()
    await manager.reload()
    manager.value.setFocus()
    QTest.keyClicks(manager.value, "8000")
    QTest.keyClick(manager.value, Qt.Key.Key_Return)  # Return logs, as the button does.
    await manager.drain()
    assert [row.value_scaled for row in manager.rows] == [8000]
    assert manager.value.text() == "" and manager.focusWidget() is manager.value
    QTest.keyClicks(manager.value, "8100")
    manager.entry_buttons["create"].setFocus()
    manager.entry_buttons["create"].click()
    await manager.drain()
    assert len(manager.rows) == 2 and manager.focusWidget() is manager.value
    # A refused value leaves focus where the owner was, on the button.
    manager.value.setText("x")
    manager.entry_buttons["create"].setFocus()
    manager.entry_buttons["create"].click()
    await manager.drain()
    assert manager.focusWidget() is manager.entry_buttons["create"]
    assert manager.isEnabled()


async def test_cards_and_their_buttons_are_named_for_their_widget(
    qtbot: QtBot, database: Database
) -> None:
    backend = InsightsBackend(database, google_on=False)
    await save_layout(backend, spec("tasks.completions"), spec("tasks.created"))
    page = await open_page(qtbot, backend)
    page.edit_layout.setChecked(True)
    await refresh(page)
    first, second = page.cards
    assert first.accessibleName() == "Task completions widget"
    names = {button.accessibleName() for button in first.findChildren(QPushButton)}
    assert names == {
        "View data for Task completions",
        "Contributing records for Task completions",
        "Edit Task completions",
        "Move Task completions earlier",
        "Move Task completions later",
        "Change the size of Task completions",
        "Remove Task completions",
    }
    assert not names & {button.accessibleName() for button in second.findChildren(QPushButton)}
    await page.drain()


async def test_a_dashboard_filter_leaves_metrics_it_cannot_reach_whole(
    qtbot: QtBot, database: Database
) -> None:
    async with database.session() as session:
        service = ProjectService(session)
        health = await service.create_area(AreaEdit(name="Health"))
        fitness = await service.create_project(
            ProjectEdit(name="Fitness", area_public_id=health.public_id)
        )
    backend = InsightsBackend(database, google_on=False)
    steps = await metric(backend)
    await log(backend, steps, 8000, project=fitness.public_id)
    await log(backend, steps, 500)
    await save_layout(
        backend, spec(definition=steps), spec("capacity.available"), spec("tasks.completions")
    )
    page = await open_page(qtbot, backend, (fitness,), (health,))
    page.area.setCurrentIndex(page.area.findData(health.public_id))
    await refresh(page)
    custom, capacity, tasks = (chart_of(card) for card in page.cards)  # None "Unavailable".
    assert custom.display.total == Decimal(8500)  # Whole: observations have no life area.
    assert "life area filter does not apply" in custom.display.caption
    assert "life area filter does not apply" in capacity.display.caption
    assert "does not apply" not in tasks.display.caption  # Tasks take it.
    page.area.setCurrentIndex(0)
    page.project.setCurrentIndex(page.project.findData(fitness.public_id))
    await refresh(page)
    custom, capacity, tasks = (chart_of(card) for card in page.cards)
    assert custom.display.total == Decimal(8000)  # A custom metric takes a project.
    assert "does not apply" not in custom.display.caption
    assert "project filter does not apply" in capacity.display.caption
    await page.drain()


def test_the_filter_note_names_what_was_left_out() -> None:
    query = spec("capacity.available").query
    project, area = new_public_id(), new_public_id()
    assert filter_note(query, None, None) == ""
    assert filter_note(query, project, None) == (
        "The dashboard's project filter does not apply to this metric."
    )
    assert filter_note(query, project, area) == (
        "The dashboard's project and life area filters do not apply to this metric."
    )
    used = query.model_copy(update={"project_public_id": project})
    assert filter_note(used, project, None) == ""
