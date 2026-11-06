"""Work blocks on the Calendar (ADR 0028): how they show, dragging and the keyboard in the
week grid, the block editor, the task detail and the window's save, move and Undo."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import ActionFilter, ActionStatus, TaskChoice
from mailbrief.domain.events import CalendarRange, ScheduledBlock
from mailbrief.domain.work_blocks import BlockTime, WorkBlock
from mailbrief.ui.action_text import block_lines, scheduled_text
from mailbrief.ui.actions_view import SCHEDULE
from mailbrief.ui.block_editor import ENDS_FIRST, LENGTH, UNSAVED, BlockEditor
from mailbrief.ui.calendar_layout import day_parts, entry_key, shifted, stretched
from mailbrief.ui.calendar_view import (
    MOVE_BLOCK,
    NEW_BLOCK,
    OPEN_BLOCK,
    UNSCHEDULE_BLOCK,
    CalendarPanel,
    day_rows,
    event_text,
)
from mailbrief.ui.event_editor import local_instant
from mailbrief.ui.main_window import MainWindow, block_status
from mailbrief.ui.today_view import agenda_row
from tests.factories import make_action
from tests.ui.test_workflow import FakeBackend

TORONTO = ZoneInfo("America/Toronto")
MONDAY = date(2026, 10, 12)
TODAY = date(2026, 10, 14)  # A Wednesday.
NOW = datetime(2026, 10, 14, 14, 20, tzinfo=TORONTO)
TASK = "00000000-0000-4000-8000-000000000077"
_numbers = iter(range(1, 10_000))


def scheduled(
    day: date,
    start: time,
    end: time,
    *,
    title: str = "Draft the Q3 report",
    status: ActionStatus = ActionStatus.OPEN,
    task: str = TASK,
) -> ScheduledBlock:
    stored = WorkBlock(
        public_id=f"00000000-0000-4000-9000-{next(_numbers):012d}",
        action_public_id=task,
        starts_at_utc=local_instant(day, start, TORONTO),
        ends_at_utc=local_instant(day, end, TORONTO),
        time_zone="America/Toronto",
        revision=1,
        created_at_utc=datetime(2026, 10, 1, tzinfo=UTC),
        updated_at_utc=datetime(2026, 10, 1, tzinfo=UTC),
    )
    return ScheduledBlock(block=stored, action_title=title, action_status=status)


def week(*blocks: ScheduledBlock) -> CalendarRange:
    return CalendarRange(
        first_day=MONDAY,
        last_day=MONDAY + timedelta(days=6),
        events=(),
        markers=(),
        blocks=blocks,
    )


@pytest.fixture
def panel(qtbot: QtBot) -> CalendarPanel:
    result = CalendarPanel()
    qtbot.addWidget(result)
    result.resize(1000, 700)
    result.show()
    qtbot.waitExposed(result)
    return result


def send(
    widget: QWidget,
    kind: QEvent.Type,
    point: QPointF,
    held: bool,
    screen: QPointF | None = None,
) -> None:
    """A mouse event at ``point`` in ``widget``; ``screen`` is where the pointer is on the
    screen, by default where ``point`` is now."""
    button = Qt.MouseButton.LeftButton
    buttons = Qt.MouseButton.LeftButton if held else Qt.MouseButton.NoButton
    if kind == QEvent.Type.MouseMove:
        button = Qt.MouseButton.NoButton
    at = widget.mapToGlobal(point) if screen is None else screen
    event = QMouseEvent(kind, point, at, button, buttons, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def drag(widget: QWidget, start: QPointF, end: QPointF) -> None:
    send(widget, QEvent.Type.MouseButtonPress, start, True)
    for step in range(1, 5):
        point = start + (end - start) * (step / 4)
        send(widget, QEvent.Type.MouseMove, point, True)
    send(widget, QEvent.Type.MouseButtonRelease, end, False)


# What shows.


def test_blocks_show_as_tasks_beside_events() -> None:
    block = scheduled(TODAY, time(10), time(11, 30))
    shown = week(block)
    assert entry_key(block) == ("block", block.public_id)
    assert [part.event for part in day_parts(shown, TODAY, TORONTO)] == [block]
    assert day_rows(shown, TODAY, TORONTO) == [block]
    assert event_text(block, TODAY, TORONTO, TODAY) == "10:00–11:30 · Task · Draft the Q3 report"
    done = scheduled(TODAY, time(12), time(13), status=ActionStatus.COMPLETED)
    assert event_text(done, TODAY, TORONTO, TODAY).startswith("12:00–13:00 · Task · completed")
    row = agenda_row(block, TORONTO, TODAY)
    assert (row.title, row.note) == ("10:00–11:30 · Draft the Q3 report", "Task")


def test_moves_follow_the_clock_across_a_dst_change() -> None:
    # Toronto falls back on Sunday Nov 1, 2026: 09:00 on Saturday plus a day is 09:00 Sunday.
    saturday = date(2026, 10, 31)
    block = scheduled(saturday, time(9), time(10))
    start, end = shifted(block, TORONTO, days=1)
    assert start.astimezone(TORONTO).time() == time(9)
    assert end - start == timedelta(hours=1)
    later, _ = shifted(block, TORONTO, minutes=45)
    assert later.astimezone(TORONTO).time() == time(9, 45)
    same, longer = stretched(block, TORONTO, 30)
    assert same == block.starts_at_utc and longer.astimezone(TORONTO).time() == time(10, 30)


def test_dragging_a_block_asks_to_move_it_by_whole_quarter_hours(
    panel: CalendarPanel,
) -> None:
    block = scheduled(TODAY, time(10), time(11))
    asked: list[tuple[str, object]] = []
    panel.block_requested.connect(lambda kind, value: asked.append((kind, value)))
    panel.show_range(week(block), TORONTO, TODAY)
    grid = panel.week_grid
    hit = next(hit for hit in grid.hits(grid.hours) if hit.entry == block)
    start = hit.rect.center()
    # A day later, and 50 minutes down: snapped to 45.
    end = QPointF(start.x() + grid.column_width(), start.y() + 50 * grid_px_per_minute())
    drag(grid.hours, start, end)
    assert asked == [(MOVE_BLOCK, (block, 1, 45, False))]
    assert grid._drag is None


def grid_px_per_minute() -> float:
    from mailbrief.ui.calendar_grid import HOUR_PX

    return HOUR_PX / 60


def test_dragging_the_bottom_edge_resizes_and_a_click_moves_nothing(
    panel: CalendarPanel,
) -> None:
    block = scheduled(TODAY, time(10), time(11))
    asked: list[tuple[str, object]] = []
    panel.block_requested.connect(lambda kind, value: asked.append((kind, value)))
    panel.show_range(week(block), TORONTO, TODAY)
    grid = panel.week_grid
    hit = next(hit for hit in grid.hits(grid.hours) if hit.entry == block)
    edge = QPointF(hit.rect.center().x(), hit.rect.bottom() - 2)
    drag(grid.hours, edge, edge + QPointF(0, 30 * grid_px_per_minute()))
    assert asked == [(MOVE_BLOCK, (block, 0, 30, True))]
    asked.clear()
    QTest.mouseClick(
        grid.hours,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        hit.rect.center().toPoint(),
    )
    assert asked == []
    assert panel.selected_block() == block


def test_a_done_task_s_block_never_moves_but_can_be_unscheduled(panel: CalendarPanel) -> None:
    block = scheduled(TODAY, time(10), time(11), status=ActionStatus.COMPLETED)
    asked: list[tuple[str, object]] = []
    panel.block_requested.connect(lambda kind, value: asked.append((kind, value)))
    panel.show_range(week(block), TORONTO, TODAY)
    grid = panel.week_grid
    hit = next(hit for hit in grid.hits(grid.hours) if hit.entry == block)
    drag(grid.hours, hit.rect.center(), hit.rect.center() + QPointF(0, 60))
    assert asked == []
    panel.select_entry(entry_key(block), TODAY)
    grid.setFocus()
    # Ctrl+arrows still ask, so the window can say why the block stays, and the selection
    # stays on it rather than stepping to another entry.
    QTest.keyClick(grid, Qt.Key.Key_Down, Qt.KeyboardModifier.ControlModifier)
    assert asked == [(MOVE_BLOCK, (block, 0, 15, False))]
    assert panel.selected_block() == block
    QTest.keyClick(grid, Qt.Key.Key_Delete)
    assert asked[-1] == (UNSCHEDULE_BLOCK, block)
    assert grid.accessibleDescription() == "Wed 2026-10-14: Task · completed, Draft the Q3 report"


def test_a_scroll_while_pressing_never_moves_the_block(panel: CalendarPanel) -> None:
    """Choosing a block can scroll the hours to show it under a still pointer: only the
    pointer's own movement counts."""
    block = scheduled(TODAY, time(10), time(11))
    asked: list[tuple[str, object]] = []
    panel.block_requested.connect(lambda kind, value: asked.append((kind, value)))
    panel.show_range(week(block), TORONTO, TODAY)
    grid = panel.week_grid
    hit = next(hit for hit in grid.hits(grid.hours) if hit.entry == block)
    point = hit.rect.center()
    screen = grid.hours.mapToGlobal(point)
    send(grid.hours, QEvent.Type.MouseButtonPress, point, True, screen)
    bar = grid.hours_area.verticalScrollBar()
    bar.setValue(bar.value() + 100)  # As _reveal would; the pointer is where it was.
    moved = point + QPointF(0, 100)
    send(grid.hours, QEvent.Type.MouseMove, moved, True, screen + QPointF(0, 3))
    send(grid.hours, QEvent.Type.MouseButtonRelease, moved, False, screen + QPointF(0, 3))
    assert asked == []


def test_a_short_block_resizes_from_its_own_end(panel: CalendarPanel) -> None:
    """A 10-minute block is drawn 20 minutes tall; its end moves from 10:10, not 10:20."""
    block = scheduled(TODAY, time(10), time(10, 10))
    asked: list[tuple[str, object]] = []
    panel.block_requested.connect(lambda kind, value: asked.append((kind, value)))
    panel.show_range(week(block), TORONTO, TODAY)
    grid = panel.week_grid
    hit = next(hit for hit in grid.hits(grid.hours) if hit.entry == block)
    edge = QPointF(hit.rect.center().x(), hit.rect.bottom() - 2)
    drag(grid.hours, edge, edge + QPointF(0, 20 * grid_px_per_minute()))
    assert asked == [(MOVE_BLOCK, (block, 0, 20, True))]  # 10:10 to 10:30.


def test_the_keyboard_moves_resizes_and_unschedules_the_selected_block(
    panel: CalendarPanel,
) -> None:
    block = scheduled(TODAY, time(10), time(11))
    asked: list[tuple[str, object]] = []
    panel.block_requested.connect(lambda kind, value: asked.append((kind, value)))
    panel.show_range(week(block), TORONTO, TODAY)
    panel.select_entry(entry_key(block), TODAY)
    grid = panel.week_grid
    grid.setFocus()
    control = Qt.KeyboardModifier.ControlModifier
    QTest.keyClick(grid, Qt.Key.Key_Down, control)
    QTest.keyClick(grid, Qt.Key.Key_Up, control)
    QTest.keyClick(grid, Qt.Key.Key_Right, control)
    QTest.keyClick(grid, Qt.Key.Key_Left, control)
    QTest.keyClick(grid, Qt.Key.Key_Down, control | Qt.KeyboardModifier.ShiftModifier)
    QTest.keyClick(grid, Qt.Key.Key_Backspace)
    assert asked == [
        (MOVE_BLOCK, (block, 0, 15, False)),
        (MOVE_BLOCK, (block, 0, -15, False)),
        (MOVE_BLOCK, (block, 1, 0, False)),
        (MOVE_BLOCK, (block, -1, 0, False)),
        (MOVE_BLOCK, (block, 0, 15, True)),
        (UNSCHEDULE_BLOCK, block),
    ]
    # Without Ctrl the arrows still move the selection, as for events.
    QTest.keyClick(grid, Qt.Key.Key_Right)
    assert len(asked) == 6


def test_opening_a_block_and_schedule_task(panel: CalendarPanel) -> None:
    block = scheduled(TODAY, time(10), time(11))
    asked: list[tuple[str, object]] = []
    panel.block_requested.connect(lambda kind, value: asked.append((kind, value)))
    panel.show_range(week(block), TORONTO, TODAY)
    grid = panel.week_grid
    hit = next(hit for hit in grid.hits(grid.hours) if hit.entry == block)
    QTest.mouseDClick(
        grid.hours,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        hit.rect.center().toPoint(),
    )
    assert asked == [(OPEN_BLOCK, block)]
    panel.schedule_button.click()
    assert asked[-1] == (NEW_BLOCK, TODAY)  # The day in focus: the block's.
    assert not panel.empty.isVisible()


# The editor.


@pytest.fixture
def editor(qtbot: QtBot) -> Iterator[BlockEditor]:
    parent = QWidget()  # Kept alive here: the dialog goes with its parent.
    qtbot.addWidget(parent)
    yield BlockEditor(parent)


def test_the_editor_schedules_new_time_for_the_task(editor: BlockEditor) -> None:
    saved: list[object] = []
    editor.save_requested.connect(saved.append)
    start = local_instant(TODAY, time(15), TORONTO)
    editor.edit_new(TASK, "Draft <b>report</b>", start, 90, TORONTO)
    editor.show()
    assert editor.heading.text() == "Draft <b>report</b>"
    assert editor.heading.textFormat() == Qt.TextFormat.PlainText
    assert editor.unschedule_button.isHidden()
    assert not editor.has_unsaved_input()
    editor.save_button.click()
    assert saved == [
        BlockTime(
            starts_at_utc=start,
            ends_at_utc=start + timedelta(minutes=90),
            time_zone="America/Toronto",
        )
    ]
    assert not editor.save_button.isEnabled()
    editor.keep_open("Couldn't save.")
    assert editor.save_button.isEnabled() and editor.status.text() == "Couldn't save."
    editor.finish_saved()
    assert not editor.isVisible()


def test_the_editor_refuses_an_end_before_the_start_or_past_twelve_hours(
    editor: BlockEditor,
) -> None:
    start = local_instant(TODAY, time(9), TORONTO)
    editor.edit_new(TASK, "Report", start, 60, TORONTO)
    editor.end_time.setEditText("08:00")  # Earlier on the clock: the next morning, too long.
    editor.end_time.commit_typed()
    assert editor.status.text() == LENGTH
    editor.end_time.setEditText("09:00")  # The next day at 09:00 is 24 hours.
    editor.end_time.commit_typed()
    assert editor.status.text() == LENGTH
    assert editor.built_time() != ENDS_FIRST
    editor.end_time.setEditText("10:30")
    editor.end_time.commit_typed()
    assert editor.status.text() == ""
    built = editor.built_time()
    assert isinstance(built, BlockTime) and built.minutes == 90
    assert editor.has_unsaved_input()
    # Moving the start keeps the length.
    editor.start_time.setEditText("13:00")
    editor.start_time.commit_typed()
    built = editor.built_time()
    assert isinstance(built, BlockTime) and built.minutes == 90
    assert built.starts_at_utc == local_instant(TODAY, time(13), TORONTO)


def test_a_done_task_s_block_can_only_be_unscheduled(editor: BlockEditor) -> None:
    block = scheduled(TODAY, time(10), time(11), status=ActionStatus.COMPLETED)
    asked: list[str] = []
    editor.unschedule_requested.connect(lambda: asked.append("unschedule"))
    editor.task_requested.connect(asked.append)
    editor.edit_block(block, TORONTO)
    editor.show()
    assert not editor.save_button.isEnabled()
    assert "Reopen it" in editor.status.text()
    assert not editor.unschedule_button.isHidden()
    editor.unschedule_button.click()
    assert asked == ["unschedule"]
    editor.keep_open("")
    editor.task_button.click()
    assert asked == ["unschedule", TASK]


def test_busy_waits_and_says_so(editor: BlockEditor) -> None:
    editor.edit_new(TASK, "Report", local_instant(TODAY, time(9), TORONTO), 60, TORONTO)
    editor.show()
    editor.set_busy(True)
    assert not editor.save_button.isEnabled() and "busy" in editor.status.text()
    editor.set_busy(False)
    assert editor.save_button.isEnabled() and editor.status.text() == ""


# The task detail.


def test_the_detail_says_how_much_time_is_scheduled() -> None:
    over = scheduled(TODAY, time(10), time(11)).block  # Ended before 14:20.
    first = scheduled(TODAY, time(15), time(16)).block
    second = scheduled(TODAY + timedelta(days=1), time(9), time(9, 30)).block
    task = make_action(estimate_minutes=120, blocks=(over, first, second))
    assert scheduled_text(task) == "2 hrs 30 mins in 3 blocks · estimate 2 hrs"
    assert block_lines(task, now=NOW, zone=TORONTO, today=TODAY) == [
        "Wed Oct 14 · 15:00–16:00",
        "Thu Oct 15 · 09:00–09:30",
    ]
    assert scheduled_text(make_action()) is None


# The window.


@pytest.fixture
def backend() -> FakeBackend:
    result = FakeBackend()
    task = make_action(public_id=TASK, title="Draft the Q3 report", estimate_minutes=45)
    result.actions[ActionFilter.OPEN] = (task,)
    result.open_task_list = (
        TaskChoice(
            public_id=TASK,
            title=task.title,
            status=ActionStatus.OPEN,
            ownership=task.ownership,
        ),
    )
    return result


@pytest.fixture
def window(qtbot: QtBot, backend: FakeBackend) -> MainWindow:
    result = MainWindow(backend)
    result.now = lambda: NOW
    qtbot.addWidget(result)
    result.show()
    return result


async def finish(window: MainWindow) -> None:
    assert window.task is not None
    await window.task


def names(backend: FakeBackend) -> list[object]:
    return [call[0] for call in backend.block_calls]


async def test_schedule_task_then_move_resize_and_undo_together(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    window._show_page("calendar")
    window.calendar_panel.schedule_button.click()
    await window.drain_calendar_reads()
    picker = window.block_picker
    assert picker.isVisible() and picker.choices.count() == 1
    picker.link_button.click()
    await window.drain_calendar_reads()
    editor = window.block_editor
    assert editor.isVisible() and editor.task_title() == "Draft the Q3 report"
    built = editor.built_time()
    assert isinstance(built, BlockTime) and built.minutes == 45  # The estimate.
    editor.save_button.click()
    await finish(window)
    assert not editor.isVisible()
    assert window.status.text() == "Scheduled: Draft the Q3 report."
    assert window.undo_button.text() == "&Undo schedule"
    (block,) = backend.blocks.values()
    shown = window.calendar_panel.selected_block()
    assert shown is not None and shown.public_id == block.public_id

    # Two nudges in a row undo together, back to where the first began.
    window._request_block_change(shown, 0, 15, resize=False)
    await finish(window)
    moved = window.calendar_panel.selected_block()
    assert moved is not None and moved.starts_at_utc == block.starts_at_utc + timedelta(minutes=15)
    window._request_block_change(moved, 0, 15, resize=True)
    await finish(window)
    assert window.undo_button.text() == "&Undo resize"
    assert window.status.text().startswith("Resized: Draft the Q3 report.")
    window.undo_button.click()
    await finish(window)
    back = backend.blocks[block.public_id]
    assert back.block.time == block.block.time
    assert names(backend)[-1] == "undo_block_move"


async def test_unschedule_with_the_key_and_undo(window: MainWindow, backend: FakeBackend) -> None:
    await window.initialize()
    saved = await backend.schedule_block(
        TASK,
        BlockTime(
            starts_at_utc=local_instant(TODAY, time(10), TORONTO),
            ends_at_utc=local_instant(TODAY, time(11), TORONTO),
            time_zone="America/Toronto",
        ),
    )
    window._show_page("calendar")
    window._read_calendar()
    await window.drain_calendar_reads()
    block = next(iter(backend.blocks.values()))
    window._request_block(UNSCHEDULE_BLOCK, block)
    await finish(window)
    assert window.status.text() == "Unscheduled: Draft the Q3 report."
    assert saved.block.public_id in backend.unscheduled_blocks
    window.undo_button.click()
    await finish(window)
    assert saved.block.public_id not in backend.unscheduled_blocks


async def test_a_refused_move_says_why_and_a_too_long_one_is_never_sent(
    window: MainWindow, backend: FakeBackend
) -> None:
    from mailbrief.services.work_blocks import BlockConflictError

    await window.initialize()
    block = scheduled(TODAY, time(10), time(21))
    window._request_block_change(block, 0, 120, resize=True)  # 13 hours.
    assert window.status.text() == LENGTH
    assert backend.block_calls == []
    backend.block_fail = BlockConflictError("Only an open task can be scheduled. Reopen it first.")
    window._request_block_change(block, 0, 15, resize=False)
    await finish(window)
    assert window.status.text() == "Only an open task can be scheduled. Reopen it first."


def test_the_status_names_overlaps_and_a_late_end() -> None:
    from mailbrief.domain.work_blocks import BlockSaved

    block = scheduled(TODAY, time(10), time(11)).block
    saved = BlockSaved(block=block, overlaps=("Standup", "Lunch", "Task · Read"), more_overlaps=2)
    assert block_status("Moved", "Report", saved) == (
        "Moved: Report. Overlaps Standup, Lunch, Task · Read and 2 more."
    )
    late = BlockSaved(block=block, after_deadline=True)
    assert block_status("Scheduled", "Report", late) == (
        "Scheduled: Report. Ends after the task's deadline."
    )


async def test_the_editor_s_buttons_fit_the_drawer(window: MainWindow) -> None:
    window.resize(1100, 720)
    block = scheduled(TODAY, time(10), time(11))
    window._request_block(OPEN_BLOCK, block)
    QApplication.processEvents()
    editor = window.block_editor
    assert editor.width() <= 390  # A drawer.
    for button in (
        editor.save_button,
        editor.cancel_button,
        editor.unschedule_button,
        editor.task_button,
    ):
        assert button.isVisible()
        assert button.width() >= button.sizeHint().width(), button.objectName()


def test_open_task_keeps_changed_time(editor: BlockEditor) -> None:
    asked: list[str] = []
    editor.task_requested.connect(asked.append)
    editor.edit_block(scheduled(TODAY, time(10), time(11)), TORONTO)
    editor.show()
    editor.start_time.setEditText("13:00")
    editor.start_time.commit_typed()
    editor.task_button.click()
    assert asked == [] and editor.isVisible()
    assert editor.status.text() == UNSAVED
    editor.cancel_button.click()
    editor.edit_block(scheduled(TODAY, time(10), time(11)), TORONTO)
    editor.show()
    editor.task_button.click()
    assert asked == [TASK] and not editor.isVisible()


async def test_a_done_task_s_block_is_refused_with_the_reason(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    block = scheduled(TODAY, time(10), time(11), status=ActionStatus.COMPLETED)
    window._request_block(MOVE_BLOCK, (block, 0, 15, False))
    assert "Reopen the task" in window.status.text()
    assert backend.block_calls == []


async def test_only_the_last_schedule_opens_and_never_over_another_panel(
    window: MainWindow, backend: FakeBackend
) -> None:
    other = make_action(public_id=f"{TASK[:-2]}78", title="Read the contract")
    backend.actions[ActionFilter.OPEN] = (*backend.actions[ActionFilter.OPEN], other)
    await window.initialize()
    window._open_new_block(TASK, TODAY)
    window._open_new_block(other.public_id, TODAY)
    await window.drain_calendar_reads()
    assert window.block_editor.isVisible()
    assert window.block_editor.task_id() == other.public_id
    # With the editor open, another Schedule never replaces what it holds.
    window._open_new_block(TASK, TODAY)
    await window.drain_calendar_reads()
    assert window.block_editor.task_id() == other.public_id
    assert window.status.text() == "Close the open panel, then schedule the task again."


async def test_schedule_time_counts_only_blocks_from_today_on(
    window: MainWindow, backend: FakeBackend
) -> None:
    # As the tasks to schedule count it (ADR 0029): yesterday's block is never work done.
    yesterday = scheduled(TODAY - timedelta(days=1), time(9), time(11)).block
    later = scheduled(TODAY, time(15), time(16)).block
    task = make_action(
        public_id=TASK, title="Draft the Q3 report", estimate_minutes=180, blocks=(yesterday, later)
    )
    backend.actions[ActionFilter.OPEN] = (task,)
    await window.initialize()
    window._open_new_block(TASK, TODAY)
    await window.drain_calendar_reads()
    built = window.block_editor.built_time()
    assert isinstance(built, BlockTime) and built.minutes == 120  # 3 hrs less today's 1 hr.
    window.block_editor.cancel_button.click()


async def test_schedule_time_from_the_detail_opens_on_a_future_target(
    window: MainWindow, backend: FakeBackend
) -> None:
    later = TODAY + timedelta(days=3)
    task = make_action(public_id=TASK, title="Draft the Q3 report", target_date=later)
    backend.actions[ActionFilter.OPEN] = (task,)
    await window.initialize()
    window._request_action(SCHEDULE, task)
    await window.drain_calendar_reads()
    built = window.block_editor.built_time()
    assert isinstance(built, BlockTime)
    assert built.starts_at_utc.astimezone(window.zone).date() == later
    window.block_editor.cancel_button.click()
    window._request_action(SCHEDULE, task.model_copy(update={"target_date": MONDAY}))
    await window.drain_calendar_reads()
    built = window.block_editor.built_time()
    assert isinstance(built, BlockTime)
    assert built.starts_at_utc.astimezone(window.zone).date() == window._local_today()


async def test_the_editor_unschedules_with_undo_and_keeps_a_stale_move(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    saved = await backend.schedule_block(
        TASK,
        BlockTime(
            starts_at_utc=local_instant(TODAY, time(10), TORONTO),
            ends_at_utc=local_instant(TODAY, time(11), TORONTO),
            time_zone="America/Toronto",
        ),
    )
    block = backend.blocks[saved.block.public_id]
    window._request_block(OPEN_BLOCK, block)
    window.block_editor.unschedule_button.click()
    await finish(window)
    assert not window.block_editor.isVisible()
    assert saved.block.public_id in backend.unscheduled_blocks
    assert window.undo_button.text() == "&Undo unschedule"
    window.undo_button.click()
    await finish(window)
    assert saved.block.public_id not in backend.unscheduled_blocks

    # The block went away meanwhile: the editor stays open with its time and says why.
    from mailbrief.services.work_blocks import BlockNotFoundError

    window._request_block(OPEN_BLOCK, backend.blocks[saved.block.public_id])
    backend.block_fail = BlockNotFoundError("That scheduled time was not found.")
    window.block_editor.save_button.click()
    await finish(window)
    assert window.block_editor.isVisible()
    assert "changed since you opened it" in window.block_editor.status.text()


def test_the_task_editor_shows_scheduled_time(qtbot: QtBot) -> None:
    from mailbrief.ui.action_editor import ActionEditor

    parent = QWidget()
    qtbot.addWidget(parent)
    editor = ActionEditor(parent)
    first = scheduled(TODAY, time(10), time(11)).block
    second = scheduled(TODAY, time(14), time(14, 30)).block
    editor.edit(make_action(blocks=(first, second)), TODAY, TORONTO)
    assert editor.scheduled.text() == "Scheduled 1 hr 30 mins in 2 blocks"
    assert not editor.scheduled.isHidden()
    editor.edit(make_action(), TODAY, TORONTO)
    assert editor.scheduled.text() == "" and editor.scheduled.isHidden()
