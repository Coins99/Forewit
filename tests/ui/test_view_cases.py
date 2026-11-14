"""Empty, one, many and long-text cases for the views added since October: the Insights
page and its metric manager, the planning drawer and the Calendar's Day view (QA slice D).
Each test is one case an owner could meet; all records are synthetic."""

from datetime import time

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import ACTION_TITLE_MAX_CHARS
from mailbrief.domain.common import new_public_id
from mailbrief.domain.dashboards import MAX_DASHBOARD_WIDGETS, ChartKind
from mailbrief.domain.events import EVENT_TITLE_MAX_CHARS, CalendarEvent, CalendarRange
from mailbrief.domain.metric_entries import MeasurementKind, MetricDefinitionSpec
from mailbrief.services.actions import ActionService
from mailbrief.storage.database import Database
from mailbrief.ui.calendar_layout import CalendarView, entry_key
from mailbrief.ui.calendar_view import EMPTY_DAY, CalendarPanel
from mailbrief.ui.insights_backend import InsightsBackend
from mailbrief.ui.insights_editors import NO_METRIC, MetricManager
from mailbrief.ui.insights_view import NO_WIDGETS
from mailbrief.ui.scheduling_view import SchedulingDialog
from mailbrief.ui.theme import ColorScheme, ThemeMode, apply_theme
from tests.ui.test_calendar_grid import TODAY, TORONTO, event
from tests.ui.test_insights import DATES
from tests.ui.test_insights_qa import chart_of, open_page, save_layout, spec
from tests.ui.test_main_window_layout import save_shot
from tests.ui.test_scheduling import Backend, ready
from tests.unit.services.test_manual_tasks import EDIT
from tests.unit.services.test_scheduling import DAY, setup

LONG = "Unbrokenlongword" * 20  # 320 characters with nowhere to wrap.


@pytest.fixture
def studio(themed: None, qapp: QApplication) -> None:
    """The default look in light mode, for the cases the gallery renders."""
    apply_theme(qapp, ThemeMode.LIGHT, ColorScheme.STUDIO)


# Insights: the dashboard.


async def test_an_empty_dashboard_says_how_to_fill_it(
    qtbot: QtBot, database: Database, studio: None
) -> None:
    backend = InsightsBackend(database, google_on=False)
    await save_layout(backend)
    page = await open_page(qtbot, backend)
    assert page.cards == [] and page.add.isEnabled()
    assert page.status.text() == NO_WIDGETS
    assert page.add.text() in NO_WIDGETS and page.reset.text() in NO_WIDGETS
    save_shot(page.grab(), "insights-empty-light")
    await page.drain()


async def test_one_widget_is_one_card_and_a_full_dashboard_takes_no_more(
    qtbot: QtBot, database: Database
) -> None:
    backend = InsightsBackend(database, google_on=False)
    await save_layout(backend, spec())
    page = await open_page(qtbot, backend)
    assert len(page.cards) == 1 and page.add.isEnabled()
    assert page.status.text() != NO_WIDGETS
    await page.drain()
    await save_layout(backend, *(spec() for _ in range(MAX_DASHBOARD_WIDGETS)))
    full = await open_page(qtbot, backend)
    assert len(full.cards) == MAX_DASHBOARD_WIDGETS and not full.add.isEnabled()
    qtbot.wait(30)
    assert full.scroll_area.verticalScrollBar().maximum() > 0  # They scroll, none is cut.
    await full.drain()


async def test_a_long_unbroken_widget_title_never_widens_the_page(
    qtbot: QtBot, database: Database
) -> None:
    backend = InsightsBackend(database, google_on=False)
    title = LONG[:120]
    await save_layout(
        backend,
        spec().model_copy(update={"title": title}),
        spec(chart=ChartKind.LINE).model_copy(
            update={"title": title, "public_id": new_public_id()}
        ),
    )
    page = await open_page(qtbot, backend)
    page.resize(680, 520)
    page.edit_layout.setChecked(True)  # The edit row's buttons are named for the title too.
    assert page._refresh_task is not None
    await page._refresh_task
    qtbot.wait(30)
    assert chart_of(page.cards[0]).title_label.text() == title
    assert page.minimumSizeHint().width() <= 680
    assert page.scroll_area.horizontalScrollBar().maximum() == 0
    await page.drain()


async def test_a_card_says_when_it_was_refreshed_in_the_owner_s_time(
    qtbot: QtBot, database: Database
) -> None:
    backend = InsightsBackend(database, google_on=False)
    await save_layout(backend, spec())
    page = await open_page(qtbot, backend)  # Its clock is 17:00 UTC, noon in Toronto.
    caption = chart_of(page.cards[0]).caption.text()
    assert f"{DATES.zone}. Refreshed 2026-11-08 12:00." in caption
    assert "+00:00" not in caption and "T17:00" not in caption
    await page.drain()


# Insights: the metric manager.


async def test_the_manager_says_so_when_there_is_no_metric_to_log(
    qtbot: QtBot, database: Database, studio: None
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    backend = InsightsBackend(database, google_on=False)
    manager = MetricManager(backend, DATES.zone, (), parent)
    await manager.reload()
    assert manager.metric_choice.count() == 0 and manager.entries_list.count() == 0
    assert manager.metric_choice.placeholderText() == NO_METRIC
    assert not manager.entry_buttons["create"].isEnabled()
    manager.show()
    qtbot.wait(30)
    save_shot(manager.grab(), "metric-manager-empty-light")
    # The first metric made is chosen by itself: the placeholder goes with the empty list.
    made = await backend.define(
        MetricDefinitionSpec(name="Steps", unit="steps", kind=MeasurementKind.AMOUNT, scale=0),
        new_public_id(),
    )
    await manager.reload()
    assert manager.metric_choice.currentData() == made.public_id
    assert manager.metric_choice.placeholderText() == ""
    assert manager.entry_buttons["create"].isEnabled()
    await manager.drain()


async def test_a_long_unbroken_metric_name_never_widens_the_manager(
    qtbot: QtBot, database: Database
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    backend = InsightsBackend(database, google_on=False)
    empty = MetricManager(backend, DATES.zone, (), parent)
    await empty.reload()
    width = empty.minimumSizeHint().width()
    await empty.drain()
    await backend.define(
        MetricDefinitionSpec(name=LONG[:120], unit="u" * 32, kind=MeasurementKind.AMOUNT, scale=0),
        new_public_id(),
    )
    manager = MetricManager(backend, DATES.zone, (), parent)
    await manager.reload()
    assert manager.metric_choice.count() == 1
    assert manager.minimumSizeHint().width() == width
    await manager.drain()


# The planning drawer.


async def test_the_planning_drawer_says_when_no_task_is_found(
    qtbot: QtBot, database: Database, studio: None
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SchedulingDialog(parent, Backend(database))
    dialog.open_range(DAY, DAY, DAY)
    await ready(dialog)
    chooser = dialog.chooser
    assert chooser.tasks.count() == 0 and not chooser.more_button.isEnabled()
    assert chooser.status.text().startswith("No tasks found.")
    dialog.show()
    qtbot.wait(30)
    save_shot(dialog.grab(), "p3-no-tasks-light")
    dialog.force_close()
    await dialog.drain()


async def test_the_planning_drawer_pages_many_tasks_and_keeps_a_long_title_inside(
    qtbot: QtBot, database: Database, studio: None
) -> None:
    async with database.session() as session:
        await setup(session)  # One task and the working hours.
        for number in range(54):
            title = f"{number:02d}{LONG}"[:ACTION_TITLE_MAX_CHARS]
            await ActionService(session).create(
                EDIT.model_copy(update={"title": title, "estimate_minutes": 30})
            )
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SchedulingDialog(parent, Backend(database))
    width = dialog.minimumSizeHint().width()  # Before any task is listed.
    dialog.open_range(DAY, DAY, DAY)
    dialog.resize(390, 680)
    dialog.show()
    await ready(dialog)
    chooser = dialog.chooser
    assert chooser.tasks.count() == 50 and chooser.more_button.isEnabled()
    assert not chooser.status.text().startswith("No tasks found.")
    chooser.more_button.click()
    await ready(dialog)
    assert chooser.tasks.count() == 55 and not chooser.more_button.isEnabled()
    qtbot.wait(30)
    assert chooser.tasks.horizontalScrollBar().maximum() == 0
    assert dialog.minimumSizeHint().width() == width
    save_shot(dialog.grab(), "p3-many-long-light")
    long_item = next(
        chooser.tasks.item(row)
        for row in range(chooser.tasks.count())
        if len(chooser.tasks.item(row).data(0).splitlines()[0]) == ACTION_TITLE_MAX_CHARS
    )
    long_item.setSelected(True)
    chooser._review()
    dialog._generate()
    await ready(dialog)
    qtbot.wait(30)
    assert dialog.form_scroll.horizontalScrollBar().maximum() == 0
    assert dialog.width() == max(390, width)  # Its own minimum, never the title's.
    dialog._discard()
    dialog.force_close()
    await dialog.drain()


# The Calendar's Day view.


def day_panel(qtbot: QtBot, *events: CalendarEvent) -> CalendarPanel:
    panel = CalendarPanel()
    qtbot.addWidget(panel)
    panel.set_view(CalendarView.DAY)
    panel.resize(680, 520)
    panel.show()
    shown = CalendarRange(first_day=TODAY, last_day=TODAY, events=events, markers=())
    panel.show_range(shown, TORONTO, TODAY)
    qtbot.wait(30)
    return panel


def blocks(panel: CalendarPanel) -> list[QRectF]:
    grid = panel.day_grid
    return [hit.rect for hit in grid.hits(grid.hours) if hit.entry is not None]


def test_an_empty_day_says_so(qtbot: QtBot, studio: None) -> None:
    panel = day_panel(qtbot)
    assert panel.empty.text() == EMPTY_DAY and not panel.empty.isHidden()
    save_shot(panel.grab(), "calendar-day-empty-light")
    assert blocks(panel) == []


def test_one_event_takes_the_width_of_its_day(qtbot: QtBot) -> None:
    panel = day_panel(qtbot, event("Standup", TODAY, time(9), time(9, 30)))
    grid = panel.day_grid
    assert panel.empty.isHidden()
    (rect,) = blocks(panel)
    assert rect.width() > grid.column_width() - 8
    assert rect.left() >= grid.column_left(0) and rect.right() <= grid.column_left(1)


def test_many_events_at_one_time_share_the_day_without_overlapping(
    qtbot: QtBot, studio: None
) -> None:
    events = [event(f"Meeting {number}", TODAY, time(10), time(11)) for number in range(12)]
    panel = day_panel(qtbot, *events)
    grid = panel.day_grid
    rects = sorted(blocks(panel), key=lambda rect: rect.left())
    assert len(rects) == 12
    assert rects[0].left() >= grid.column_left(0) and rects[-1].right() <= grid.column_left(1)
    for before, after in zip(rects, rects[1:], strict=False):
        assert before.right() <= after.left()
        assert before.width() >= 4
    save_shot(panel.grab(), "calendar-day-many-light")


def test_a_long_unbroken_event_title_never_widens_the_day(qtbot: QtBot, studio: None) -> None:
    short = day_panel(qtbot, event("Standup", TODAY, time(9), time(9, 30)))
    width = short.minimumSizeHint().width()
    title = LONG[:EVENT_TITLE_MAX_CHARS]
    long_event = event(title, TODAY, time(9), time(9, 30))
    panel = day_panel(qtbot, long_event)
    panel.select_entry(entry_key(long_event), TODAY)
    qtbot.wait(30)
    grid = panel.day_grid
    (rect,) = blocks(panel)
    assert rect.right() <= grid.column_left(1)
    assert panel.minimumSizeHint().width() == width and panel.width() == 680
    assert title in grid.accessibleDescription()  # Cut on screen, whole for a screen reader.
    for label in panel.findChildren(QLabel):
        assert label.width() <= panel.width()
    save_shot(panel.grab(), "calendar-day-long-light")
