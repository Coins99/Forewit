"""The weekly review (A07, P5-1): one Monday-to-Sunday week from saved records.

The dialog only shows what the window reads and asks the window to act: open a task where
it lives, add one to a focus day, mark the week reviewed, or write the week's reflection as
an ordinary note. Nothing here changes a task by itself.
"""

from datetime import date, timedelta
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.weekly_review import ReviewTask, WeeklyReview, week_dates
from mailbrief.ui.deadline_text import day_text, moment_text
from mailbrief.ui.labels import outline_button, plain_label, wrap_label
from mailbrief.ui.lists import ActivatingList
from mailbrief.ui.theme import SMALL_PX, current_tokens, ui_font
from mailbrief.ui.workload_text import header_text


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


class WeeklyReviewDialog(QDialog):
    """Done, still open and next week, for one week; the window does the reading."""

    week_requested = Signal(object)  # The Monday to read.
    open_requested = Signal(object)  # A TaskRoute.
    focus_requested = Signal(str, object)  # A task's public ID and the focus day.
    review_requested = Signal(bool)  # Mark (True) or unmark (False) the week reviewed.
    reflection_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("weeklyReviewDialog")
        self.setAccessibleName("Weekly review")
        self.setWindowTitle("Weekly review")
        self.resize(660, 560)
        self.shown: WeeklyReview | None = None
        self.week: date | None = None
        self.today = date.min
        self._rows: list[ReviewTask | None] = []
        self._open_ids: set[str] = set()
        layout = QVBoxLayout(self)

        weeks = QHBoxLayout()
        self.previous_button = outline_button("Previous week", "reviewPreviousWeek")
        self.previous_button.clicked.connect(lambda: self._step(-7))
        self.title = plain_label("")
        self.title.setObjectName("reviewWeekTitle")
        self.title.setAccessibleName("The week under review")
        self.title.setFont(ui_font(SMALL_PX, semibold=True))
        self.this_week_button = outline_button("This week", "reviewThisWeek")
        self.this_week_button.clicked.connect(self._this_week)
        self.next_button = outline_button("Next week", "reviewNextWeek")
        self.next_button.clicked.connect(lambda: self._step(7))
        weeks.addWidget(self.previous_button)
        weeks.addWidget(self.title, 1)
        weeks.addWidget(self.this_week_button)
        weeks.addWidget(self.next_button)
        layout.addLayout(weeks)

        self.status = wrap_label("Loading the week…")
        self.status.setObjectName("reviewStatus")
        self.status.setAccessibleName("Whether this week is reviewed, and its counts")
        layout.addWidget(self.status)

        self.list = ActivatingList()
        self.list.setObjectName("reviewRecords")
        self.list.setAccessibleName("This week's done, open and next week's records")
        self.list.itemActivated.connect(lambda _item: self._open())
        self.list.currentRowChanged.connect(lambda _row: self._enable())
        layout.addWidget(self.list, 1)

        focus = QHBoxLayout()
        self.focus_day = QComboBox()
        self.focus_day.setObjectName("reviewFocusDay")
        self.focus_day.setAccessibleName("The day whose focus the selected task joins")
        self.focus_button = outline_button("Add to focus", "reviewAddToFocus")
        self.focus_button.setAccessibleName("Add the selected open task to that day's focus")
        self.focus_button.clicked.connect(self._focus)
        focus.addWidget(self.focus_day, 1)
        focus.addWidget(self.focus_button)
        layout.addLayout(focus)

        actions = QHBoxLayout()
        self.reflection_button = outline_button("Write reflection", "reviewReflection")
        self.reflection_button.setAccessibleName("Write or open this week's reflection note")
        self.reflection_button.clicked.connect(self.reflection_requested.emit)
        self.review_button = outline_button("Mark week reviewed", "reviewMark", primary=True)
        self.review_button.clicked.connect(self._toggle_review)
        actions.addWidget(self.reflection_button)
        actions.addStretch(1)
        actions.addWidget(self.review_button)
        layout.addLayout(actions)
        self._enable()

    def request(self, week: date, today: date) -> None:
        """Read ``week`` (a Monday); ``today`` is the owner's day, for date text."""
        self.week, self.today = week, today
        self.title.setText(self._week_text(week))
        self.status.setText("Loading the week…")
        self.week_requested.emit(week)

    def show_failure(self, text: str) -> None:
        self.status.setText(text)

    def show_review(self, shown: WeeklyReview, zone: ZoneInfo, today: date) -> None:
        if shown.week_start != self.week:
            return  # An older read arrived after the owner moved on.
        self.shown, self.today = shown, today
        self.title.setText(self._week_text(shown.week_start))
        current = self.list.currentRow()
        self.list.clear()
        self._rows = []
        self._open_ids = {row.public_id for row in (*shown.still_open, *shown.waiting)}
        done = f"Done this week · {shown.done_total}"
        if shown.partial_tasks:
            done += f" · {_plural(shown.partial_tasks, 'task')} with partial history"
        self._section(done, shown.done, "Nothing completed this week.")
        if shown.skipped:
            self._section(f"Skipped · {len(shown.skipped)}", shown.skipped, "")
        self._section(
            f"Still open · {len(shown.still_open)}", shown.still_open, "Nothing left open."
        )
        if shown.waiting:
            self._section(f"Waiting · {len(shown.waiting)}", shown.waiting, "")
        self._next_week(shown)
        selectable = [index for index, row in enumerate(self._rows) if row is not None]
        if selectable:
            self.list.setCurrentRow(min(selectable, key=lambda index: abs(index - current)))
        self.focus_day.clear()
        for day in week_dates(shown.next_start):
            self.focus_day.addItem(day_text(day, today=today), day)
        record = shown.record
        if record is not None and record.reviewed_at_utc is not None:
            when = moment_text(record.reviewed_at_utc, zone, today=today)
            state = f"Reviewed {when}."
        else:
            state = "Not marked reviewed."
        self.status.setText(
            f"{state} {_plural(shown.done_total, 'task')} done, "
            f"{len(shown.still_open)} still open, {len(shown.waiting)} waiting. "
            "Changes happen only when you choose them."
        )
        self._enable()

    # Rows.

    def _section(self, title: str, rows: tuple[ReviewTask, ...], empty: str) -> None:
        self._heading(title)
        for row in rows:
            text = f"{row.title}\n{row.detail}" if row.detail else row.title
            self.list.addItem(text)
            self._rows.append(row)
        if not rows and empty:
            self._note(empty)

    def _heading(self, title: str) -> None:
        item = QListWidgetItem(title)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setFont(ui_font(SMALL_PX, semibold=True))
        item.setForeground(QColor(current_tokens().text_muted))
        self.list.addItem(item)
        self._rows.append(None)

    def _note(self, text: str) -> None:
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setForeground(QColor(current_tokens().text_muted))
        self.list.addItem(item)
        self._rows.append(None)

    def _next_week(self, shown: WeeklyReview) -> None:
        self._heading(f"Next week · {_plural(len(shown.next_week.items), 'commitment')}")
        for load in shown.next_days:
            text, warns = header_text(load)
            if text:
                line = f"{day_text(load.day, today=self.today)} · {text}"
                self._note(f"{line} · overfilled" if warns else line)
        for item in shown.next_week.items:
            route = item.route
            if route.kind != "task" or route.public_id is None:
                self._note(f"{item.title}\n{' · '.join(item.reasons)}")
                continue
            self.list.addItem(f"{item.title}\n{' · '.join(item.reasons)}")
            self._rows.append(
                ReviewTask(
                    public_id=route.public_id,
                    title=item.title,
                    detail=" · ".join(item.reasons),
                    route=route,
                )
            )
            self._open_ids.add(route.public_id)
        if not shown.next_week.items:
            self._note("Nothing dated next week yet.")

    def selected(self) -> ReviewTask | None:
        row = self.list.currentRow()
        return self._rows[row] if 0 <= row < len(self._rows) else None

    # Actions.

    def _enable(self) -> None:
        chosen = self.selected()
        ready = self.shown is not None
        self.focus_button.setEnabled(
            ready and chosen is not None and chosen.public_id in self._open_ids
        )
        self.focus_day.setEnabled(ready)
        self.reflection_button.setEnabled(ready)
        record = None if self.shown is None else self.shown.record
        self.reflection_button.setText(
            "Open reflection"
            if record is not None and record.reflection_note_public_id
            else "Write reflection"
        )
        self.review_button.setEnabled(ready)
        self.review_button.setText(
            "Unmark reviewed" if record is not None and record.reviewed else "Mark week reviewed"
        )

    def _open(self) -> None:
        chosen = self.selected()
        if chosen is not None:
            self.open_requested.emit(chosen.route)
            self.reject()

    def _focus(self) -> None:
        chosen, day = self.selected(), self.focus_day.currentData()
        if chosen is not None and isinstance(day, date):
            self.focus_requested.emit(chosen.public_id, day)

    def _toggle_review(self) -> None:
        record = None if self.shown is None else self.shown.record
        self.review_requested.emit(not (record is not None and record.reviewed))

    def _step(self, days: int) -> None:
        if self.week is not None:
            self.request(self.week + timedelta(days=days), self.today)

    def _this_week(self) -> None:
        self.request(self.today - timedelta(days=self.today.weekday()), self.today)

    def _week_text(self, week: date) -> str:
        last = week + timedelta(days=6)
        return f"{day_text(week, today=self.today)} to {day_text(last, today=self.today)}"
