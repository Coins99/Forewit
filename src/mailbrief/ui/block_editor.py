"""Schedule time for one task, or change or unschedule a block of it (ADR 0028).

A block is planned time for its task and never changes the task. The editor shows the
task's title (the owner's text, or mail-derived) as plain text, and when the block is: a
day, a start and an end, typed or chosen in 15-minute steps. The fields and their helpers
are the event editor's (``DateBox``, ``TimeBox``, ``field_instant`` and the rest), so a time
an hour a DST change repeats keeps its instant here too. A block lasts 5 minutes to 12
hours; an end outside that is refused with the reason.
"""

from datetime import UTC, datetime, time, timedelta
from typing import Final
from uuid import uuid4
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from PySide6.QtCore import QDate, Qt, QTime, Signal
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.events import ScheduledBlock
from mailbrief.domain.work_blocks import BLOCK_MAX_MINUTES, BLOCK_MIN_MINUTES, BlockTime
from mailbrief.ui.date_picker import calendar_popup
from mailbrief.ui.event_editor import (
    BAD_TIME,
    DATE_FORMAT,
    WAITING,
    DateBox,
    TimeBox,
    day_times,
    end_choices,
    field_instant,
    local_instant,
    parse_clock,
    qdate,
    qday,
    stepped_wall,
)
from mailbrief.ui.labels import outline_button, plain_label, wrap_label
from mailbrief.ui.theme import ui_font

ENDS_FIRST: Final = "The end must be after the start."
LENGTH: Final = "Scheduled time lasts 5 minutes to 12 hours."
UNUSABLE: Final = "This time can't be saved; check it and try again."
UNSAVED: Final = "Save or Cancel the changed time first, then open the task."
_LONGEST: Final = timedelta(minutes=BLOCK_MAX_MINUTES)
_SHORTEST: Final = timedelta(minutes=BLOCK_MIN_MINUTES)


class BlockEditor(QDialog):
    """``save_requested(time)`` with a BlockTime, for the task in ``task_id()`` and, when
    one is open, the block in ``editing()``; ``unschedule_requested()`` for the open block;
    ``task_requested(public_id)`` to show the task.

    After asking, it stays open and locked until the window calls finish_saved(), which
    closes it, or keep_open(), which unlocks it with every field as the owner left it.
    """

    save_requested = Signal(object)
    lock_requested = Signal(bool)
    unschedule_requested = Signal()
    task_requested = Signal(str)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("blockEditorDialog")
        self.setAccessibleName("Scheduled time")
        self.setWindowTitle("Schedule time")
        self.resize(520, 440)
        self._block: ScheduledBlock | None = None
        self._task_id = ""
        self._title = ""
        self._zone = ZoneInfo("UTC")
        self.command_id = str(uuid4())
        self._uncertain = False
        self._busy = False
        self._saving = False
        self._syncing = False
        self._start = datetime(2000, 1, 1, 9, tzinfo=UTC)
        self._end = self._start + timedelta(hours=1)
        layout = QVBoxLayout(self)
        self.setFont(ui_font(10))
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        self.kicker = plain_label("SCHEDULE TIME", tone="muted", px=9)
        self.kicker.setObjectName("blockEditorKicker")
        layout.addWidget(self.kicker)
        # The task's title: the owner's text, or mail-derived; plain, wrapped if unbroken.
        self.heading = wrap_label("", px=21, medium=True)
        self.heading.setObjectName("blockEditorHeading")
        layout.addWidget(self.heading)
        self.blurb = plain_label(
            "Set aside time for this task. The task, its deadline and its dates stay as they are.",
            tone="muted",
            px=10,
        )
        self.blurb.setObjectName("blockEditorBlurb")
        layout.addWidget(self.blurb)
        self._fields = QWidget()
        form = QFormLayout(self._fields)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setContentsMargins(0, 0, 0, 0)
        self.day = DateBox()
        self.day.setObjectName("blockDay")
        calendar_popup(self.day)
        self.day.setDisplayFormat(DATE_FORMAT)
        self.day.setAccessibleName("On")
        self.day.setKeyboardTracking(False)
        self.start_time = TimeBox("Starts")
        self.start_time.setObjectName("blockStartTime")
        self.end_time = TimeBox("Ends")
        self.end_time.setObjectName("blockEndTime")
        form.addRow("On", self.day)
        row = QHBoxLayout()
        row.addWidget(self.start_time)
        until = plain_label("to", tone="muted")
        until.setObjectName("blockUntil")
        until.setAccessibleName("to")
        row.addWidget(until)
        row.addWidget(self.end_time)
        row.addStretch(1)
        form.addRow("From", row)
        layout.addWidget(self._fields)
        self.lock_button = outline_button("Lock block", "blockLockButton")
        self.lock_button.setAccessibleName("Lock or unlock this reservation")
        self.lock_button.clicked.connect(self._toggle_lock)
        layout.addWidget(self.lock_button)
        self.status = wrap_label("", tone="muted")
        self.status.setObjectName("blockStatus")
        layout.addWidget(self.status)
        layout.addStretch(1)
        # Two rows: the drawer is 320 to 390 px wide, too narrow for four pills in one.
        self.save_button = outline_button("Schedule", "blockSaveButton", primary=True)
        self.cancel_button = outline_button("Cancel", "blockCancelButton")
        self.unschedule_button = outline_button("Unschedule", "blockUnscheduleButton")
        self.task_button = outline_button("Open task", "blockTaskButton")
        for pair in (
            (self.save_button, self.cancel_button),
            (self.unschedule_button, self.task_button),
        ):
            buttons = QHBoxLayout()
            for button in pair:
                button.setAccessibleName(button.text())
                buttons.addWidget(button)
            buttons.addStretch(1)
            layout.addLayout(buttons)
        self.day.dateChanged.connect(self._day_changed)
        self.start_time.committed.connect(lambda _moment: self._take_start())
        self.start_time.stepped.connect(self._start_stepped)
        self.end_time.committed.connect(self._end_committed)
        self.end_time.stepped.connect(self._end_stepped)
        self.save_button.clicked.connect(self._save)
        self.unschedule_button.clicked.connect(self._unschedule)
        self.task_button.clicked.connect(self._open_task)
        self.cancel_button.clicked.connect(self.reject)
        self._loaded_input = self._input()

    # What the window asks.

    def editing(self) -> ScheduledBlock | None:
        """The block being changed, or None for new time."""
        return self._block

    def task_id(self) -> str:
        return self._task_id

    def task_title(self) -> str:
        """The task's title as shown: the owner's text, or mail-derived; never logged."""
        return self._title

    def edit_new(
        self, task_id: str, title: str, start: datetime, minutes: int, zone: ZoneInfo
    ) -> None:
        """New time for the task ``task_id`` (titled ``title``), from ``start``."""
        self.command_id = str(uuid4())
        self._uncertain = False
        self.lock_button.hide()
        self._block, self._task_id, self._zone = None, task_id, zone
        self._title = title
        self.setWindowTitle("Schedule time")
        self.kicker.setText("SCHEDULE TIME")
        self.heading.setText(title)
        self.save_button.setText("Schedule")
        self.save_button.setAccessibleName("Schedule")
        self.unschedule_button.hide()
        self._show(start, start + timedelta(minutes=minutes))
        self._reset()

    def edit_block(self, block: ScheduledBlock, zone: ZoneInfo) -> None:
        """Change or unschedule ``block``; its times are shown in ``zone``."""
        self.command_id = str(uuid4())
        self._uncertain = False
        self.lock_button.show()
        self.lock_button.setText("Unlock block" if block.block.locked else "Lock block")
        self._block, self._task_id, self._zone = block, block.action_public_id, zone
        self._title = block.title
        self.setWindowTitle("Scheduled time")
        self.kicker.setText("SCHEDULED TIME")
        self.heading.setText(block.title)
        self.save_button.setText("Save changes")
        self.save_button.setAccessibleName("Save changes")
        self.unschedule_button.show()
        self._show(block.starts_at_utc, block.ends_at_utc)
        self._reset()
        if not block.open:
            self.status.setText(
                "This task is done. Reopen it to move its time, or unschedule the time."
            )

    def built_time(self) -> BlockTime | str:
        """The time shown, or the reason it can't be saved."""
        length = self._end - self._start
        if length <= timedelta(0):
            return ENDS_FIRST
        if length < _SHORTEST or length > _LONGEST:
            return LENGTH
        try:
            return BlockTime(
                starts_at_utc=self._start, ends_at_utc=self._end, time_zone=self._zone.key
            )
        except ValidationError:
            return UNUSABLE

    def has_unsaved_input(self) -> bool:
        return self._uncertain or (not self._saving and self._input() != self._loaded_input)

    def set_busy(self, busy: bool) -> None:
        """Another operation is running: Save and Unschedule wait for it, and say so."""
        self._busy = busy
        if busy and not self._saving and self.isVisible() and not self.status.text():
            self.status.setText(WAITING)
        elif not busy and self.status.text() == WAITING:
            self.status.setText("")
        self._update_buttons()

    def finish_saved(self) -> None:
        self._set_saving(False)
        self.accept()

    def keep_open(self, message: str, *, uncertain: bool = False) -> None:
        self._uncertain = uncertain
        if not uncertain:
            self.command_id = str(uuid4())
        self._set_saving(False)
        if uncertain:
            self.save_button.setText("Retry save")
        self.status.setText(message)

    def force_close(self) -> None:
        self._uncertain = False
        self._set_saving(False)
        self.reject()

    def reject(self) -> None:
        if not self._saving and not self._uncertain:
            super().reject()

    # The fields.

    def _input(self) -> tuple[object, ...]:
        return (
            self.day.text(),
            self.start_time.currentText(),
            self.end_time.currentText(),
        )

    def _show(self, start: datetime, end: datetime) -> None:
        """Take ``start`` and ``end`` as accepted and show them."""
        self._start, self._end = start.astimezone(UTC), end.astimezone(UTC)
        first = self._start.astimezone(self._zone)
        last = self._end.astimezone(self._zone)
        self._syncing = True
        try:
            self.day.setDate(qdate(first.date()))
            self.start_time.set_choices(
                [(f"{clock:%H:%M}", None) for clock in day_times(first.date(), self._zone)]
            )
            self.start_time.setTime(QTime(first.hour, first.minute))
            choices = [
                (label, moment)
                for label, moment in end_choices(self._start, self._zone)
                if moment - self._start <= _LONGEST
            ]
            self.end_time.set_choices(choices)
            self.end_time.setTime(QTime(last.hour, last.minute), self._end)
        finally:
            self._syncing = False

    def _move_start(self, start: datetime) -> None:
        """A new start keeps the block's length."""
        length = self._end - self._start
        self.status.setText("")
        self._show(start, start.astimezone(UTC) + length)

    def _day_changed(self, value: QDate) -> None:
        if not self._syncing:
            local = self._start.astimezone(self._zone)
            clock = time(local.hour, local.minute)
            self._move_start(field_instant(qday(value), clock, self._start, self._zone))

    def _take_start(self) -> bool:
        typed = parse_clock(self.start_time.currentText())
        if typed is None:
            self._refuse(BAD_TIME)
            return False
        self._move_start(field_instant(qday(self.day.date()), typed, self._start, self._zone))
        return True

    def _start_stepped(self, steps: int) -> None:
        if self.start_time.typing() and not self._take_start():
            return
        local = self._start.astimezone(self._zone)
        wall = stepped_wall(local.replace(tzinfo=None, fold=0), steps, self._zone)
        self._move_start(local_instant(wall.date(), wall.time(), self._zone))

    def _end_committed(self, moment: object) -> None:
        if isinstance(moment, datetime):
            self._try_end(moment)
        else:
            self._take_end()

    def _take_end(self) -> bool:
        typed = parse_clock(self.end_time.currentText())
        if typed is None:
            self._refuse(BAD_TIME)
            return False
        # A typed end is on the start's day, or the next when it is earlier on the clock.
        local = self._start.astimezone(self._zone)
        day = local.date()
        if typed <= time(local.hour, local.minute):
            day += timedelta(days=1)
        return self._try_end(field_instant(day, typed, self._end, self._zone))

    def _end_stepped(self, steps: int) -> None:
        if self.end_time.typing() and not self._take_end():
            return
        wall = stepped_wall(
            self._end.astimezone(self._zone).replace(tzinfo=None, fold=0), steps, self._zone
        )
        self._try_end(local_instant(wall.date(), wall.time(), self._zone))

    def _try_end(self, end: datetime) -> bool:
        length = end.astimezone(UTC) - self._start
        if length <= timedelta(0):
            self._refuse(ENDS_FIRST)
            return False
        if length < _SHORTEST or length > _LONGEST:
            self._refuse(LENGTH)
            return False
        self.status.setText("")
        self._show(self._start, end)
        return True

    def _refuse(self, message: str) -> None:
        self._show(self._start, self._end)
        self.status.setText(message)

    def _reset(self) -> None:
        self.status.setText(WAITING if self._busy else "")
        self._saving = False
        self._fields.setEnabled(True)
        self._update_buttons()
        self._loaded_input = self._input()
        self.day.setFocus(Qt.FocusReason.OtherFocusReason)

    # The buttons.

    def _toggle_lock(self) -> None:
        if self._block is not None and not (self._busy or self._saving):
            if self.has_unsaved_input():
                self.status.setText("Save or cancel the changed time before changing its lock.")
                return
            self._set_saving(True)
            self.lock_requested.emit(not self._block.block.locked)

    def _save(self) -> None:
        if self._busy or self._saving:
            return
        for box, take in ((self.start_time, self._take_start), (self.end_time, self._take_end)):
            if box.typing() and not take():
                return
        built = self.built_time()
        if isinstance(built, str):
            self.status.setText(built)
            return
        self._set_saving(True)
        self.status.setText("Saving…")
        self.save_requested.emit(built)

    def _unschedule(self) -> None:
        if self._busy or self._saving or self._block is None:
            return
        self._set_saving(True)
        self.status.setText("Unscheduling…")
        self.unschedule_requested.emit()

    def _open_task(self) -> None:
        if not self._task_id or self._saving:
            return
        if self.has_unsaved_input():
            # Leaving would drop the changed time; quitting asks, and so does this.
            self.status.setText(UNSAVED)
            return
        task = self._task_id
        self.reject()
        self.task_requested.emit(task)

    def _set_saving(self, saving: bool) -> None:
        self._saving = saving
        self._fields.setEnabled(not saving and not self._uncertain)
        self._update_buttons()

    def _update_buttons(self) -> None:
        idle = not (self._busy or self._saving)
        done = self._block is not None and not self._block.open
        locked = self._block is not None and self._block.block.locked
        self.lock_button.setEnabled(idle and not self._uncertain)
        self.save_button.setEnabled(idle and not done and not locked)
        self.unschedule_button.setEnabled(idle and not locked and not self._uncertain)
        self.task_button.setEnabled(not self._saving and not self._uncertain)
