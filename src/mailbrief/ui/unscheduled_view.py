"""The Calendar's tasks to schedule (ADR 0029): the owner's open tasks that still need time.

Each row is a task's title, how much of its estimate is left to schedule and its date. Return
or a double-click asks for the next free time for it (``schedule_requested``). A row dragged
out of the list tells the page where the pointer is (``dragged``) and where it was let go
(``dropped``), in screen coordinates; the page knows the grid under it. The list never moves
a task: it only asks, and the window saves a block, which never changes the task.

Titles are the owner's or an email's text, so rows are painted with ``drawText`` by the
display row delegate: never rich text or a tooltip.
"""

from datetime import date, timedelta
from typing import Final

from PySide6.QtCore import QEvent, QObject, QPointF, Qt, Signal
from PySide6.QtGui import QKeyEvent, QMouseEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.availability import UnscheduledTask, UnscheduledTasks
from mailbrief.ui.deadline_text import day_text, length_text
from mailbrief.ui.labels import plain_label
from mailbrief.ui.list_rows import DISPLAY_ROW_ROLE, DisplayRow, DisplayRowDelegate
from mailbrief.ui.lists import ActivatingList, clear_list
from mailbrief.ui.theme import CAPTION_PX, TEXT_PX, ui_font

PANEL_WIDTH: Final = 260
TASK_ROLE: Final = Qt.ItemDataRole.UserRole + 31
EMPTY: Final = "Every open task you own has time set aside."
HINT: Final = "Drag a task onto the hours, or press Return for the next free time."


def left_text(task: UnscheduledTask) -> str:
    """ "2 hrs left of 3 hrs", "3 hrs to schedule" or "No estimate"."""
    if task.estimate_minutes is None:
        return "No estimate"
    left = task.left_minutes or 0
    estimate = length_text(timedelta(minutes=task.estimate_minutes))
    if left == task.estimate_minutes:
        return f"{estimate} to schedule"
    return f"{length_text(timedelta(minutes=left))} left of {estimate}"


def date_note(task: UnscheduledTask, today: date) -> str:
    """ "Due Thu Nov 5" and "Planned Fri Nov 6", whichever it has; both when it has both."""
    parts: list[str] = []
    if task.deadline_day is not None:
        parts.append(f"Due {day_text(task.deadline_day, today=today)}")
    if task.target_date is not None:
        parts.append(f"Planned {day_text(task.target_date, today=today)}")
    return " · ".join(parts)


def count_text(found: UnscheduledTasks) -> str:
    """ "4 tasks", or "First 50 of 63"."""
    if found.total > len(found.tasks):
        return f"First {len(found.tasks)} of {found.total}"
    return "1 task" if found.total == 1 else f"{found.total} tasks"


class UnscheduledPanel(QWidget):
    """``schedule_requested(task)``: Return or a double-click on a row. ``dragged(task,
    screen)`` as a row is dragged, ``dropped(task, screen)`` where it is let go and
    ``drag_ended()`` once it is over, dropped or stopped with Escape."""

    schedule_requested = Signal(object)
    dragged = Signal(object, object)
    dropped = Signal(object, object)
    drag_ended = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("unscheduledPanel")
        self.setAccessibleName("Tasks to schedule")
        self.setFixedWidth(PANEL_WIDTH)
        self._today: date | None = None
        self._pressed: tuple[UnscheduledTask, QPointF] | None = None
        self._dragging = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        top = QHBoxLayout()
        self.heading = plain_label("To schedule", px=TEXT_PX, medium=True)
        self.heading.setObjectName("unscheduledHeading")
        top.addWidget(self.heading)
        top.addStretch(1)
        self.count = plain_label("", tone="muted", px=CAPTION_PX)
        self.count.setObjectName("unscheduledCount")
        top.addWidget(self.count)
        layout.addLayout(top)
        self.list = ActivatingList()
        self.list.setObjectName("unscheduledList")
        self.list.setAccessibleName("Tasks to schedule")
        self.list.setItemDelegate(DisplayRowDelegate(self.list))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setAutoScroll(False)  # A drag out of the list never scrolls it.
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.itemActivated.connect(self._activated)
        self.list.viewport().installEventFilter(self)
        self.list.installEventFilter(self)
        layout.addWidget(self.list, 1)
        self.empty = plain_label(EMPTY, tone="muted", px=CAPTION_PX)
        self.empty.setObjectName("unscheduledEmpty")
        layout.addWidget(self.empty)
        self.hint = plain_label(HINT, tone="muted", px=CAPTION_PX)
        self.hint.setObjectName("unscheduledHint")
        layout.addWidget(self.hint)
        self.empty.hide()
        self.setFont(ui_font(TEXT_PX))

    def show_tasks(self, found: UnscheduledTasks, today: date) -> None:
        """Replace the rows, keeping the same task current when it is still listed."""
        current = self.selected()
        self._today = today
        self.list.blockSignals(True)
        try:
            clear_list(self.list)
            for task in found.tasks:
                item = QListWidgetItem(task.title)
                item.setData(TASK_ROLE, task)
                item.setData(
                    DISPLAY_ROW_ROLE,
                    DisplayRow(
                        title=task.title, meta=left_text(task), preview=date_note(task, today)
                    ),
                )
                self.list.addItem(item)
        finally:
            self.list.blockSignals(False)
        if current is not None:
            for row in range(self.list.count()):
                listed = self._task_at(row)
                if listed is not None and listed.action_public_id == current.action_public_id:
                    self.list.setCurrentRow(row)
                    break
        self.count.setText(count_text(found) if found.total else "")
        self.empty.setVisible(not found.tasks)
        self.list.setVisible(bool(found.tasks))
        self.hint.setVisible(bool(found.tasks))

    def tasks(self) -> list[UnscheduledTask]:
        return [task for row in range(self.list.count()) if (task := self._task_at(row))]

    def selected(self) -> UnscheduledTask | None:
        return self._task_at(self.list.currentRow())

    def _task_at(self, row: int) -> UnscheduledTask | None:
        item = self.list.item(row) if 0 <= row < self.list.count() else None
        task = None if item is None else item.data(TASK_ROLE)
        return task if isinstance(task, UnscheduledTask) else None

    def _activated(self, item: QListWidgetItem) -> None:
        task = item.data(TASK_ROLE)
        if isinstance(task, UnscheduledTask) and not self._dragging:
            self.schedule_requested.emit(task)

    # Dragging a row out: pressed, then moved the platform's drag distance, it reports the
    # pointer on the screen until it is let go. The list's own drag and drop stays off.

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        kind = event.type()
        if watched is self.list and kind == QEvent.Type.KeyPress:
            escape = isinstance(event, QKeyEvent) and event.key() == Qt.Key.Key_Escape
            if escape and self._dragging:
                self._end_drag()
                return True
            return False
        if watched is self.list and kind == QEvent.Type.FocusOut:
            # The app or the list lost focus mid-drag (deactivated, or the list hidden): its
            # release may never come, so the drag ends here.
            if self._dragging:
                self._end_drag()
            return False
        if watched is not self.list.viewport() or not isinstance(event, QMouseEvent):
            return False
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            if self._dragging:  # A release went missing: that drag is over.
                self._end_drag()
            item = self.list.itemAt(event.position().toPoint())
            task = None if item is None else item.data(TASK_ROLE)
            self._pressed = (
                (task, event.globalPosition()) if isinstance(task, UnscheduledTask) else None
            )
            return False
        if kind == QEvent.Type.MouseMove and self._pressed is not None:
            task, origin = self._pressed
            if not self._dragging:
                if (event.globalPosition() - origin).manhattanLength() < (
                    QApplication.startDragDistance()
                ):
                    return False
                self._dragging = True
                self.list.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)
            self.dragged.emit(task, event.globalPosition())
            return True
        if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
            pressed, dragging = self._pressed, self._dragging
            self._pressed = None
            if dragging and pressed is not None:
                self.dropped.emit(pressed[0], event.globalPosition())
                self._end_drag()
                return True
        return False

    def _end_drag(self) -> None:
        self._pressed = None
        self._dragging = False
        self.list.viewport().unsetCursor()
        self.drag_ended.emit()

    def dragging(self) -> bool:
        return self._dragging
