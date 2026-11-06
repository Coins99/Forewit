"""P3-2 on the desktop (ADR 0029): Busy or Free, working hours, the Day view and the tasks
to schedule beside the hours."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import ActionFilter
from mailbrief.domain.availability import UnscheduledTask, UnscheduledTasks
from mailbrief.domain.events import CalendarEvent, CalendarRange, EventEdit
from mailbrief.domain.google_calendar import GoogleEvent
from mailbrief.domain.preferences import OwnerPreferences
from mailbrief.domain.work_blocks import BlockSaved, BlockTime
from mailbrief.domain.working_hours import SUGGESTED_HOURS, HoursEdit, WorkingHours, WorkingWindow
from mailbrief.ui.calendar_grid import HOUR_PX, HOURS_PAD, entry_style, is_free
from mailbrief.ui.calendar_layout import CalendarView, off_hours, show_as
from mailbrief.ui.calendar_view import NO_HOURS, event_text, free_text
from mailbrief.ui.event_editor import EventEditor, local_instant
from mailbrief.ui.hours_editor import (
    BAD_WINDOW,
    ENDS_FIRST,
    OVERLAP,
    QUARTER_HOURS,
    TOO_MANY,
    HoursEditor,
    parse_windows,
    windows_text,
)
from mailbrief.ui.main_window import MainWindow, block_status
from mailbrief.ui.theme import current_tokens
from mailbrief.ui.unscheduled_view import UnscheduledPanel, count_text, date_note, left_text
from tests.factories import make_action
from tests.ui.test_workflow import FakeBackend

TORONTO = ZoneInfo("America/Toronto")
MONDAY = date(2026, 10, 12)
TODAY = date(2026, 10, 14)  # A Wednesday.
NOW = datetime(2026, 10, 14, 14, 20, tzinfo=TORONTO)
TASK = "00000000-0000-4000-8000-000000000077"
HOURS = WorkingHours(days=SUGGESTED_HOURS.days, buffer_minutes=0, revision=1)


def event(start: time, end: time, *, busy: bool | None = None, day: date = TODAY) -> CalendarEvent:
    return CalendarEvent(
        public_id="00000000-0000-4000-8000-000000000001",
        title="Coffee",
        notes="",
        all_day=False,
        starts_at_utc=local_instant(day, start, TORONTO),
        ends_at_utc=local_instant(day, end, TORONTO),
        start_date=None,
        last_date=None,
        time_zone="America/Toronto",
        revision=1,
        created_at_utc=datetime(2026, 10, 1, tzinfo=UTC),
        updated_at_utc=datetime(2026, 10, 1, tzinfo=UTC),
        busy=busy,
    )


def task(title: str = "Draft the Q3 report", **extra: object) -> UnscheduledTask:
    return UnscheduledTask.model_validate({"action_public_id": TASK, "title": title, **extra})


def shown(
    first: date, last: date, *events: CalendarEvent, hours: WorkingHours = HOURS
) -> CalendarRange:
    return CalendarRange(first_day=first, last_day=last, events=events, markers=(), hours=hours)


# Busy or Free.


def test_free_and_busy_say_so_only_when_not_the_shape_s_own() -> None:
    assert show_as(event(time(10), time(11))) is None
    free = event(time(10), time(11), busy=False)
    assert show_as(free) == "Free"
    assert event_text(free, TODAY, TORONTO, TODAY) == "10:00–11:00 · Coffee · Free"
    tokens = current_tokens()
    assert is_free(free) and entry_style(free, tokens)[0] == tokens.panel
    google = GoogleEvent(
        calendar_id="w",
        calendar_name="Work",
        event_id="x",
        title="Away",
        all_day=True,
        start_date=TODAY,
        last_date=TODAY,
        busy=True,
    )
    assert show_as(google) == "Busy"  # A day off.
    assert event_text(google, TODAY, TORONTO, TODAY) == "All day · Away · Work · Busy"


def test_the_event_editor_s_show_as_follows_all_day_until_chosen(qtbot: QtBot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    editor = EventEditor(host)
    editor.edit_new(TODAY, NOW, TORONTO)
    editor.title.setText("Coffee")
    assert editor.show_as.currentText() == "Busy"
    editor.all_day.setChecked(True)
    assert editor.show_as.currentText() == "Free"  # An all-day event's own.
    editor.all_day.setChecked(False)
    assert editor.show_as.currentText() == "Busy"
    editor.show_as.setCurrentIndex(editor.show_as.findText("Free"))
    editor.show_as.activated.emit(editor.show_as.currentIndex())
    editor.all_day.setChecked(True)
    assert editor.show_as.currentText() == "Free"
    editor.all_day.setChecked(False)
    assert editor.show_as.currentText() == "Free"  # Chosen: it stays.
    built = editor.built_edit()
    assert isinstance(built, EventEdit) and built.busy is False
    assert editor.has_unsaved_input()
    editor.edit_event(event(time(10), time(11), busy=False), TORONTO)
    assert editor.show_as.currentText() == "Free" and not editor.has_unsaved_input()
    editor.all_day.setChecked(True)
    assert editor.show_as.currentText() == "Free"


# Working hours.


def test_typed_windows_are_read_or_refused_with_the_reason() -> None:
    nine, noon = WorkingWindow(start_minute=540, end_minute=720), None
    assert parse_windows("") == ()
    assert parse_windows("9-12") == (nine,)
    assert parse_windows(" 13:00 to 17:00 , 09:00–12:00 ") == (
        nine,
        WorkingWindow(start_minute=780, end_minute=1020),
    )
    assert parse_windows("22:00-24:00") == (WorkingWindow(start_minute=1320, end_minute=1440),)
    assert noon is None
    assert parse_windows("nine to five") == BAD_WINDOW
    assert parse_windows("9:61-10") == BAD_WINDOW
    assert parse_windows("24:00-24:00") == BAD_WINDOW
    assert parse_windows("9:10-10") == QUARTER_HOURS
    assert parse_windows("12-9") == ENDS_FIRST
    assert parse_windows("9-12, 11-13") == OVERLAP
    assert parse_windows("1-2, 3-4, 5-6, 7-8") == TOO_MANY
    assert windows_text(SUGGESTED_HOURS.windows(0)) == "09:00–17:00"


def test_the_hours_editor_builds_the_week_and_names_a_bad_day(qtbot: QtBot) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    editor = HoursEditor(host)
    editor.edit_hours(WorkingHours.unset())
    assert all(not field.text() for field in editor.days) and not editor.has_unsaved_input()
    editor.suggest_button.click()
    assert [field.text() for field in editor.days] == ["09:00–17:00"] * 5 + ["", ""]
    editor.days[0].setText("08:30–12:00, 13:00–16:30")
    editor.copy_button.click()
    assert editor.days[4].text() == "08:30–12:00, 13:00–16:30" and editor.days[5].text() == ""
    editor.buffer.setCurrentIndex(editor.buffer.findData(15))
    built = editor.built_edit()
    assert isinstance(built, HoursEdit) and built.buffer_minutes == 15
    assert built.weekly_minutes == 5 * 7 * 60
    assert editor.has_unsaved_input()
    editor.days[6].setText("9-")
    sent: list[object] = []
    editor.save_requested.connect(sent.append)
    editor.save_button.click()
    assert sent == [] and editor.status.text() == f"Sunday: {BAD_WINDOW}"
    editor.days[6].setText("")
    editor.save_button.click()
    assert len(sent) == 1 and not editor.save_button.isEnabled()
    editor.keep_open("Nope.")
    assert editor.save_button.isEnabled() and editor.status.text() == "Nope."


def test_off_hours_and_the_free_line() -> None:
    assert off_hours(WorkingHours.unset(), 0) == []
    assert off_hours(HOURS, 0) == [(0, 540), (1020, 1440)]
    assert off_hours(HOURS, 6) == [(0, 1440)]
    day = shown(TODAY, TODAY, event(time(10), time(11)))
    assert free_text(day, TORONTO, CalendarView.DAY) == "Working 09:00–17:00 · 7 hrs free"
    assert free_text(day, TORONTO, CalendarView.MONTH) == ""
    unset = shown(TODAY, TODAY, hours=WorkingHours.unset())
    assert free_text(unset, TORONTO, CalendarView.DAY) == NO_HOURS
    week = shown(MONDAY, MONDAY + timedelta(days=6), event(time(10), time(11)))
    assert free_text(week, TORONTO, CalendarView.WEEK) == "39 hrs free of 40 hrs this week"
    saturday = shown(date(2026, 10, 17), date(2026, 10, 17))
    assert free_text(saturday, TORONTO, CalendarView.DAY) == "No working hours this day"


def test_the_status_says_a_block_is_outside_the_working_hours() -> None:
    block = make_action(public_id=TASK)
    assert block.public_id == TASK
    stored = BlockSaved.model_validate(
        {
            "block": {
                "public_id": TASK,
                "action_public_id": TASK,
                "starts_at_utc": NOW,
                "ends_at_utc": NOW + timedelta(hours=1),
                "time_zone": "America/Toronto",
                "revision": 1,
                "created_at_utc": NOW,
                "updated_at_utc": NOW,
            },
            "outside_hours": True,
        }
    )
    assert block_status("Scheduled", "Draft", stored) == (
        "Scheduled: Draft. Outside your working hours."
    )


# The tasks to schedule.


def test_rows_say_what_is_left_and_when(qtbot: QtBot) -> None:
    assert left_text(task(estimate_minutes=180, scheduled_minutes=60)) == "2 hrs left of 3 hrs"
    assert left_text(task(estimate_minutes=90)) == "1 hr 30 mins to schedule"
    assert left_text(task()) == "No estimate"
    dated = task(deadline_day=TODAY + timedelta(days=1), target_date=TODAY)
    assert date_note(dated, TODAY) == "Due Thu Oct 15 · Planned Wed Oct 14"
    assert count_text(UnscheduledTasks(tasks=(task(),), total=1)) == "1 task"
    assert count_text(UnscheduledTasks(tasks=(task(),), total=60)) == "First 1 of 60"
    panel = UnscheduledPanel()
    qtbot.addWidget(panel)
    panel.show_tasks(UnscheduledTasks(), TODAY)
    assert panel.list.isHidden() and not panel.empty.isHidden()
    panel.show_tasks(UnscheduledTasks(tasks=(task(),), total=1), TODAY)
    assert [item.title for item in panel.tasks()] == ["Draft the Q3 report"]
    assert panel.count.text() == "1 task"


@pytest.fixture
def backend() -> FakeBackend:
    result = FakeBackend()
    result.actions[ActionFilter.OPEN] = (
        make_action(public_id=TASK, title="Draft the Q3 report", estimate_minutes=90),
    )
    result.hours = HOURS
    result.unscheduled = UnscheduledTasks(tasks=(task(estimate_minutes=90),), total=1)
    return result


@pytest.fixture
async def window(qtbot: QtBot, backend: FakeBackend) -> MainWindow:
    result = MainWindow(backend)
    result.now = lambda: NOW
    qtbot.addWidget(result)
    result.resize(1200, 760)
    result.show()
    await result.initialize()
    result._apply_owner_preferences(OwnerPreferences(revision=0, time_zone="America/Toronto"))
    result._show_page("calendar")
    result._read_calendar()
    await result.drain_calendar_reads()
    return result


async def finish(window: MainWindow) -> None:
    assert window.task is not None
    await window.task


async def test_the_day_view_reads_one_day_and_steps_by_days(
    window: MainWindow, backend: FakeBackend
) -> None:
    panel = window.calendar_panel
    panel.set_view(CalendarView.DAY)
    panel.views.currentChanged.emit(panel.views.currentIndex())
    await window.drain_calendar_reads()
    reads = [call for call in backend.event_calls if call[0] == "calendar_range"]
    assert reads[-1][1:] == (TODAY, TODAY)
    assert panel.stack.currentWidget() is panel.day_grid
    assert panel.heading.text() == "Calendar · Wednesday, October 14"
    assert panel.free_line.text() == "Working 09:00–17:00 · 8 hrs free"
    QTest.keyClick(panel.day_grid, Qt.Key.Key_Right)
    await window.drain_calendar_reads()
    reads = [call for call in backend.event_calls if call[0] == "calendar_range"]
    assert reads[-1][1:] == (TODAY + timedelta(days=1), TODAY + timedelta(days=1))
    for _ in range(2):  # Each step from the day read.
        panel.previous_button.click()
        await window.drain_calendar_reads()
    reads = [call for call in backend.event_calls if call[0] == "calendar_range"]
    assert reads[-1][1:] == (TODAY - timedelta(days=1), TODAY - timedelta(days=1))
    panel.this_week_button.click()
    await window.drain_calendar_reads()
    assert backend.event_calls[-1][1:] == (TODAY, TODAY)
    assert window.appearance.calendar_view is CalendarView.DAY


async def test_working_hours_open_save_and_refuse_a_stale_revision(
    window: MainWindow, backend: FakeBackend
) -> None:
    window.calendar_panel.hours_button.click()
    await window.drain_calendar_reads()
    editor = window.hours_editor
    assert editor.isVisible() and editor.revision() == 1
    assert editor.days[0].text() == "09:00–17:00"
    editor.days[0].setText("10:00–16:00")
    assert window._unsaved_input() == "your working hours"
    editor.save_button.click()
    await finish(window)
    assert not editor.isVisible() and window.status.text() == "Working hours saved."
    assert backend.hours.revision == 2 and windows_text(backend.hours.windows(0)) == "10:00–16:00"
    window.calendar_panel.hours_button.click()
    await window.drain_calendar_reads()
    backend.hours = backend.hours.model_copy(update={"revision": 5})  # Changed elsewhere.
    editor.save_button.click()
    await finish(window)
    assert editor.isVisible() and "changed since" in editor.status.text()
    editor.force_close()


async def test_unreadable_hours_never_open_the_editor(
    window: MainWindow, backend: FakeBackend
) -> None:
    backend.hours_fail = RuntimeError("broken")
    window.calendar_panel.hours_button.click()
    await window.drain_calendar_reads()
    assert not window.hours_editor.isVisible()
    assert window.status.text() == "Your working hours couldn't be loaded. Try again."


async def test_the_panel_shows_with_the_hours_and_is_remembered(
    window: MainWindow, backend: FakeBackend
) -> None:
    panel = window.calendar_panel
    assert not panel.tasks_shown() and panel.unscheduled.isHidden()
    assert panel.tasks_button.text() == "Tasks to schedule · 1"
    panel.tasks_button.click()
    assert not panel.unscheduled.isHidden() and window.appearance.calendar_tasks
    assert panel.tasks_button.text() == "Hide tasks to schedule"
    assert [item.title for item in panel.unscheduled.tasks()] == ["Draft the Q3 report"]
    panel.set_view(CalendarView.MONTH)
    assert panel.unscheduled.isHidden() and panel.tasks_button.isHidden()
    panel.set_view(CalendarView.WEEK)
    assert not panel.unscheduled.isHidden()


async def test_return_on_a_task_opens_the_editor_at_the_next_free_time(
    window: MainWindow, backend: FakeBackend
) -> None:
    panel = window.calendar_panel
    panel.set_tasks_shown(True)
    free = local_instant(TODAY, time(15, 30), TORONTO)
    backend.free_at = free
    panel.unscheduled.list.setCurrentRow(0)
    QTest.keyClick(panel.unscheduled.list, Qt.Key.Key_Return)
    await window.drain_calendar_reads()
    editor = window.block_editor
    assert editor.isVisible() and editor.task_id() == TASK
    built = editor.built_time()
    assert isinstance(built, BlockTime)
    assert built.starts_at_utc == free and built.minutes == 90
    call = next(call for call in backend.hours_calls if call[0] == "next_free")
    assert call[1] == NOW and call[2] == 90  # From now, on today.
    editor.force_close()
    backend.free_at = None  # No free stretch: the editor says so and starts as usual.
    QTest.keyClick(panel.unscheduled.list, Qt.Key.Key_Return)
    await window.drain_calendar_reads()
    assert editor.isVisible() and "No free stretch" in editor.status.text()
    editor.force_close()


def send(widget: QWidget, kind: QEvent.Type, point: QPointF, screen: QPointF, held: bool) -> None:
    button = Qt.MouseButton.NoButton if kind == QEvent.Type.MouseMove else Qt.MouseButton.LeftButton
    buttons = Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton
    event = QMouseEvent(kind, point, screen, button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


async def test_a_task_dragged_onto_the_hours_is_scheduled_there(
    window: MainWindow, backend: FakeBackend
) -> None:
    panel = window.calendar_panel
    panel.set_tasks_shown(True)
    grid = panel.week_grid
    grid.hours_area.verticalScrollBar().setValue(int(8 * HOUR_PX))
    QApplication.processEvents()
    viewport = panel.unscheduled.list.viewport()
    row = panel.unscheduled.list.visualItemRect(panel.unscheduled.list.item(0)).center()
    press = QPointF(row)
    # Wednesday (the third column) at 10:15.
    x = grid.column_left(2) + grid.column_width() / 2
    y = HOURS_PAD + (10 * 60 + 15) * HOUR_PX / 60 + 2
    target = QPointF(grid.hours.mapToGlobal(QPoint(int(x), int(y))))
    send(viewport, QEvent.Type.MouseButtonPress, press, viewport.mapToGlobal(press), True)
    for step in range(1, 5):
        screen = viewport.mapToGlobal(press) + (target - viewport.mapToGlobal(press)) * step / 4
        send(viewport, QEvent.Type.MouseMove, press, screen, True)
    placement = grid.placement()
    assert placement is not None and placement[:3] == (TODAY, 10 * 60 + 15, 90)
    send(viewport, QEvent.Type.MouseButtonRelease, press, target, False)
    assert grid.placement() is None
    await finish(window)
    (call,) = [call for call in backend.block_calls if call[0] == "schedule_block"]
    time_sent = call[2]
    assert isinstance(time_sent, BlockTime)
    assert time_sent.starts_at_utc == local_instant(TODAY, time(10, 15), TORONTO)
    assert time_sent.minutes == 90
    assert window.status.text() == "Scheduled: Draft the Q3 report."
    assert window.undo_button.text() == "&Undo schedule"
