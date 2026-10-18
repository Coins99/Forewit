"""Add or change one calendar event: its title, when it happens and its notes (ADR 0018).

Dates and times use fixed numeric formats, so nothing depends on the system language. A
time is typed, chosen from a list in 15-minute steps, or stepped a quarter hour at a time
with the arrow keys, the same way as the date beside it; the end's list says how long the
event lasts. The wheel changes a date or a time only while its field has focus, so
scrolling over the dialog never moves an event. The editor never takes an end at or before
the start: moving the start keeps the event's length, and an end that doesn't fit is
refused with the reason. A Save that comes with a refusal waits, so the reason stays on
screen.
"""

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from PySide6.QtCore import QDate, Qt, QTime, Signal
from PySide6.QtGui import QKeyEvent, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.events import (
    EVENT_MAX_DAYS,
    EVENT_NOTES_MAX_CHARS,
    EVENT_TITLE_MAX_CHARS,
    CalendarEvent,
    EventEdit,
)
from mailbrief.ui.deadline_text import length_text
from mailbrief.ui.fields import takes_wheel
from mailbrief.ui.labels import plain_label, primary_action
from mailbrief.ui.theme import ui_font

DATE_FORMAT: Final = "yyyy-MM-dd"
TIME_FORMAT: Final = "HH:mm"
NO_TITLE: Final = "Add a title."
ENDS_FIRST: Final = "The end must be after the start."
TOO_LONG: Final = "An event can last at most 31 days."
NOTES_TOO_LONG: Final = "Notes can hold at most 10,000 characters."
UNUSABLE: Final = "These details can't be saved; check them and try again."
BAD_TIME: Final = "Type a time as HH:mm, for example 09:30."
WAITING: Final = "Forewit is busy; Save works again once it finishes."
_STEP_MINUTES: Final = 15
STEP: Final = timedelta(minutes=_STEP_MINUTES)
_STEPS_A_DAY: Final = 96
_STEPS_AN_HOUR: Final = 4
# How far a key moves a time, the same way as in the date beside it: Up and Down a quarter
# hour later and earlier, Page Up and Page Down an hour.
_KEY_STEPS: Final = (
    (Qt.Key.Key_Up, 1),
    (Qt.Key.Key_Down, -1),
    (Qt.Key.Key_PageUp, _STEPS_AN_HOUR),
    (Qt.Key.Key_PageDown, -_STEPS_AN_HOUR),
)
_WHEEL_NOTCH: Final = 120  # One notch of a mouse wheel, in eighths of a degree.
_DEFAULT_HOUR: Final = 9
_DEFAULT_LENGTH: Final = timedelta(hours=1)
_TYPED_TIME: Final = re.compile(r"\s*([01]?[0-9]|2[0-3]):([0-5][0-9])\s*")


def _qdate(day: date) -> QDate:
    return QDate(day.year, day.month, day.day)


def _day(value: QDate) -> date:
    return date(value.year(), value.month(), value.day())


def _clock(value: QTime) -> time:
    return time(value.hour(), value.minute())


def local_instant(day: date, clock: time, zone: ZoneInfo) -> datetime:
    """A wall-clock time in ``zone``. A time a DST change skips is read with the offset
    before the change, as the clock would have shown it; a repeated time is its first."""
    return datetime.combine(day, clock).replace(tzinfo=zone, fold=0)


def suggested_start(day: date, now: datetime, zone: ZoneInfo) -> datetime:
    """Where a new event starts: the next whole hour today, else 09:00 on ``day``."""
    local_now = now.astimezone(zone)
    if day == local_now.date() and local_now.hour < 23:
        return local_instant(day, time(local_now.hour + 1), zone)
    return local_instant(day, time(_DEFAULT_HOUR), zone)


def parse_clock(text: str) -> time | None:
    """A typed ``HH:mm`` or ``H:mm`` time; None for anything else."""
    match = _TYPED_TIME.fullmatch(text)
    return None if match is None else time(int(match[1]), int(match[2]))


def _exists(day: date, clock: time, zone: ZoneInfo) -> bool:
    """Whether the clock in ``zone`` shows ``clock`` on ``day``; a DST change skips some."""
    moment = local_instant(day, clock, zone).astimezone(UTC).astimezone(zone)
    return moment.replace(tzinfo=None) == datetime.combine(day, clock)


def day_times(day: date, zone: ZoneInfo) -> tuple[time, ...]:
    """The times of ``day`` in 15-minute steps, leaving out any a DST change skips."""
    steps = (datetime.combine(day, time()) + STEP * step for step in range(_STEPS_A_DAY))
    return tuple(at.time() for at in steps if _exists(day, at.time(), zone))


def end_choices(start: datetime, zone: ZoneInfo) -> tuple[tuple[str, datetime], ...]:
    """Ends from 15 minutes to 24 hours after ``start`` on the clock, in 15-minute steps,
    each labelled with the event's real length; times a DST change skips are left out."""
    local = start.astimezone(zone)
    wall = datetime.combine(local.date(), time(local.hour, local.minute))
    found: list[tuple[str, datetime]] = []
    for step in range(1, _STEPS_A_DAY + 1):
        at = wall + STEP * step
        if not _exists(at.date(), at.time(), zone):
            continue
        end = local_instant(at.date(), at.time(), zone).astimezone(UTC)
        length = end - start.astimezone(UTC)
        if length > timedelta(0):
            found.append((f"{at:%H:%M} ({length_text(length)})", end))
    return tuple(found)


def _wall(moment: datetime) -> datetime:
    """``moment``'s reading on the clock, without its zone, for stepping along the clock."""
    return moment.replace(tzinfo=None, fold=0)


def stepped_wall(wall: datetime, steps: int, zone: ZoneInfo) -> datetime:
    """``wall``, a reading on the clock in ``zone``, moved ``steps`` quarter-hour marks: from
    10:07 one step is 10:15 forward or 10:00 back, and from 10:00 it is 10:15 or 09:45.
    Marks a DST change skips are passed over."""
    if steps == 0:
        return wall
    way = 1 if steps > 0 else -1
    mark = wall.replace(minute=wall.minute - wall.minute % _STEP_MINUTES, second=0, microsecond=0)
    if way < 0 and mark != wall:
        mark += STEP  # Back from between two marks, the first step is the mark before.
    for _ in range(abs(steps)):
        mark += STEP * way
        while not _exists(mark.date(), mark.time(), zone):
            mark += STEP * way
    return mark


class DateBox(QDateEdit):
    """A date, typed as yyyy-MM-dd or picked in its calendar popup. Like a TimeBox, the
    wheel changes it only while it has focus, never gives it focus and ignores a trackpad's
    coasting, so scrolling over the dialog never moves an event by a day. ``stepped`` says
    an arrow key or the wheel moved it, once its change has been taken or refused."""

    stepped = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # Tab and click; not the wheel.

    def stepBy(self, steps: int) -> None:
        super().stepBy(steps)
        self.stepped.emit()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if takes_wheel(self, event):
            super().wheelEvent(event)


class TimeBox(QComboBox):
    """A time of day, typed as HH:mm or chosen from a list in 15-minute steps.

    ``committed(moment)`` says the owner chose a listed time (``moment`` is the item's
    data, or None when it has none) or finished typing (None). ``stepped(steps)`` asks for
    the quarter-hour mark ``steps`` away, later when positive: Up and Down, Page Up and Page
    Down, or the wheel while the box has focus. While the list is closed, Qt's own stepping
    through it is never used: from a time the list doesn't hold, it jumps to the first
    item. Nothing is committed or stepped when a time is shown with setTime(); the editor
    decides whether a time fits.
    """

    committed = Signal(object)
    stepped = Signal(int)

    def __init__(self, name: str) -> None:
        super().__init__()
        self.setEditable(True)
        # An editable combo box takes focus from the wheel; this one waits for Tab or a click.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setCompleter(QCompleter([], self))  # Typing "1" never jumps to a listed "1…".
        self.setMaxVisibleItems(10)
        self.setMinimumContentsLength(5)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setAccessibleName(f"{name} at")
        self.view().setTextElideMode(Qt.TextElideMode.ElideNone)
        self._time = QTime(0, 0)
        self._moment: datetime | None = None  # The instant shown, in a list of instants.
        self._wheel = 0  # Wheel turns short of a notch; a trackpad sends small ones.
        line = self.lineEdit()
        assert line is not None
        line.setAccessibleName(f"{name} at")
        line.editingFinished.connect(self._typed)
        self.activated.connect(self._chosen)

    def time(self) -> QTime:
        return self._time

    def setTime(self, value: QTime, moment: datetime | None = None) -> None:
        """Show ``value`` alone in the field (QTimeEdit's name, kept for callers). The
        list's current item is the one whose data is ``moment`` when one is given, as in the
        end's list, where the same time on another day is another item; else the one showing
        ``value``. A time the list doesn't hold leaves no item current."""
        self._time = QTime(value.hour(), value.minute())
        self._moment = moment
        text = self._time.toString(TIME_FORMAT)
        self.blockSignals(True)
        listed = [index for index in range(self.count()) if self._shows(index)]
        self.setCurrentIndex(listed[0] if listed else -1)
        self.setEditText(text)
        self.blockSignals(False)

    def set_choices(self, choices: Sequence[tuple[str, object]]) -> None:
        """Replace the list; the time shown stays."""
        self.blockSignals(True)
        self.clear()
        for label, moment in choices:
            self.addItem(label, moment)
        self.blockSignals(False)
        view = self.view()
        view.setMinimumWidth(view.sizeHintForColumn(0) + 2 * view.frameWidth() + 24)
        self.setTime(self._time, self._moment)

    def typing(self) -> bool:
        """Whether the field holds text typed but not yet taken."""
        return self.currentText() != self._time.toString(TIME_FORMAT)

    def _shows(self, index: int) -> bool:
        """Whether the item at ``index`` stands for the time shown."""
        if self._moment is not None:
            return bool(self.itemData(index) == self._moment)
        return self.itemText(index)[:5] == self._time.toString(TIME_FORMAT)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        steps = next((moves for key, moves in _KEY_STEPS if event.key() == key), 0)
        opens = event.modifiers() & Qt.KeyboardModifier.AltModifier
        if not steps or (event.key() == Qt.Key.Key_Down and opens):
            super().keyPressEvent(event)  # Alt+Down opens the list, as in any combo box.
            return
        event.accept()
        self.stepped.emit(steps)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if not takes_wheel(self, event):
            return  # Scrolling over the dialog, or coasting, leaves the time alone.
        event.accept()
        self._wheel += event.angleDelta().y()
        notches = int(self._wheel / _WHEEL_NOTCH)  # Toward zero: half a notch waits.
        if notches:
            self._wheel -= notches * _WHEEL_NOTCH
            self.stepped.emit(notches)  # The way a spin box goes, as the date beside it does.

    def commit_typed(self) -> None:
        """Take a time typed but not yet taken, as leaving the field would."""
        self._typed()

    def _chosen(self, index: int) -> None:
        if index >= 0:
            self.committed.emit(self.itemData(index))

    def _typed(self) -> None:
        if self.typing():
            self.committed.emit(None)


class EventEditor(QDialog):
    """``save_requested(edit)`` with an EventEdit; the window knows which event it edits.

    The dialog validates what it can; the service still checks every bound and the revision.
    After asking, it stays open and locked until the window calls finish_saved(), which
    closes it, or keep_open(), which unlocks it with every field as the owner left it.
    """

    save_requested = Signal(object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Event")
        self.setObjectName("eventEditorDialog")
        self.setAccessibleName("Event editor")
        self.resize(520, 520)
        self._event: CalendarEvent | None = None
        self._zone = ZoneInfo("UTC")
        self._busy = False  # Another operation is running, so Save waits.
        self._saving = False  # This dialog's save is running, so nothing can change.
        # The last start and end the fields accepted, in UTC, so a refused change goes back.
        self._start = datetime(2000, 1, 1, _DEFAULT_HOUR, tzinfo=UTC)
        self._end = self._start + _DEFAULT_LENGTH
        self._syncing = False  # The editor is filling the fields itself.
        # A typed time was just refused and the fields went back: the Save that came with
        # it (Return, or the click that took focus from the field) waits once.
        self._refused = False
        # Ticking All day on an event that ends at midnight moves its end to the day before;
        # the start and end before and after, so unticking at once brings the times back.
        self._timed: tuple[datetime, datetime] | None = None
        self._whole_days: tuple[datetime, datetime] | None = None
        layout = QVBoxLayout(self)
        self.setFont(ui_font(10))
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        self.kicker = plain_label("NEW EVENT", tone="muted", px=9)
        self.kicker.setObjectName("eventEditorKicker")
        self.heading = plain_label("Add to your calendar", px=21, medium=True)
        self.heading.setObjectName("eventEditorHeading")
        layout.addWidget(self.kicker)
        layout.addWidget(self.heading)
        layout.addWidget(plain_label("Keep an event on your local calendar.", tone="muted", px=10))
        self._fields = QWidget()
        form = QFormLayout(self._fields)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setContentsMargins(0, 0, 0, 0)
        self.title = QLineEdit()
        self.title.setObjectName("eventTitle")
        self.title.setMaxLength(EVENT_TITLE_MAX_CHARS)
        self.title.setAccessibleName("Title")
        self.title.textEdited.connect(self._edited_elsewhere)
        form.addRow("Title", self.title)
        self.all_day = QCheckBox("All day")
        self.all_day.setObjectName("eventAllDay")
        self.all_day.toggled.connect(self._all_day_toggled)
        form.addRow("", self.all_day)
        self.start_date, self.start_time = self._moment(
            "Starts", "eventStartDate", "eventStartTime"
        )
        self.end_date, self.end_time = self._moment("Ends", "eventEndDate", "eventEndTime")
        self.start_date.dateChanged.connect(self._start_day_changed)
        self.end_date.dateChanged.connect(self._end_day_changed)
        self.start_time.committed.connect(self._start_time_committed)
        self.end_time.committed.connect(self._end_time_committed)
        self.start_time.stepped.connect(self._start_time_stepped)
        self.end_time.stepped.connect(self._end_time_stepped)
        self.start_date.stepped.connect(self._stepped)
        self.end_date.stepped.connect(self._stepped)
        for label, (day, clock) in (
            ("Starts", (self.start_date, self.start_time)),
            ("Ends", (self.end_date, self.end_time)),
        ):
            row = QHBoxLayout()
            row.addWidget(day)
            row.addWidget(clock)
            row.addStretch(1)
            form.addRow(label, row)
        self.notes = QPlainTextEdit()
        self.notes.setObjectName("eventNotes")
        self.notes.setAccessibleName("Notes")
        self.notes.setTabChangesFocus(True)
        self.notes.setFixedHeight(82)
        self.notes.textChanged.connect(self._edited_elsewhere)
        form.addRow("Notes", self.notes)
        layout.addWidget(self._fields)
        self.status = plain_label("", tone="muted")
        self.status.setObjectName("eventStatus")
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons, 0, Qt.AlignmentFlag.AlignLeft)
        if (save := self.buttons.button(QDialogButtonBox.StandardButton.Save)) is not None:
            primary_action(save)
        self._update_buttons()
        self._loaded_input = self._input()

    def _input(self) -> tuple[object, ...]:
        """Everything the owner can change here, to tell whether any of it is unsaved. Dates
        and times are read as their fields show them, so one typed but not yet taken counts."""
        return (
            self.title.text(),
            self.notes.toPlainText(),
            self.all_day.isChecked(),
            self.start_date.text(),
            self.start_time.currentText(),
            self.end_date.text(),
            self.end_time.currentText(),
        )

    def has_unsaved_input(self) -> bool:
        """Whether closing now would lose something the owner typed or chose. Not while
        it is being saved: quitting lets that save finish."""
        return not self._saving and self._input() != self._loaded_input

    @staticmethod
    def _moment(name: str, day_name: str, clock_name: str) -> tuple[DateBox, TimeBox]:
        day = DateBox()
        day.setObjectName(day_name)
        day.setCalendarPopup(True)
        day.setDisplayFormat(DATE_FORMAT)
        day.setAccessibleName(f"{name} on")
        day.setKeyboardTracking(False)  # A typed date counts once it's finished.
        clock = TimeBox(name)
        clock.setObjectName(clock_name)
        return day, clock

    def editing(self) -> CalendarEvent | None:
        """The event being changed, or None for a new one. (Not ``event``: Qt dispatches
        every event through QObject.event.)"""
        return self._event

    def edit_new(self, day: date, now: datetime, zone: ZoneInfo) -> None:
        """Start a new one-hour event on ``day``."""
        start = suggested_start(day, now, zone)
        self.kicker.setText("NEW EVENT")
        self.heading.setText("Add to your calendar")
        self._event = None
        self._zone = zone
        self.setWindowTitle("New event")
        if (save := self.buttons.button(QDialogButtonBox.StandardButton.Save)) is not None:
            save.setText("Create event")
        self.title.setText("")
        self.notes.setPlainText("")
        self._load(start, start + _DEFAULT_LENGTH, all_day=False)
        self._reset()

    def edit_event(self, event: CalendarEvent, zone: ZoneInfo) -> None:
        """Change ``event``; its times are shown in ``zone``."""
        self._event = event
        self._zone = zone
        self.setWindowTitle("Edit event")
        if (save := self.buttons.button(QDialogButtonBox.StandardButton.Save)) is not None:
            save.setText("Save changes")
        self.kicker.setText("EVENT DETAILS")
        self.heading.setText("Edit event")
        self.title.setText(event.title)
        self.notes.setPlainText(event.notes)
        if event.start_date is not None and event.last_date is not None:
            start = local_instant(event.start_date, time(_DEFAULT_HOUR), zone)
            end = local_instant(event.last_date, time(_DEFAULT_HOUR + 1), zone)
        else:
            assert event.starts_at_utc is not None and event.ends_at_utc is not None
            start = event.starts_at_utc.astimezone(zone)
            end = event.ends_at_utc.astimezone(zone)
        self._load(start, end, all_day=event.all_day)
        self._reset()

    def _load(self, start: datetime, end: datetime, *, all_day: bool) -> None:
        self._timed = self._whole_days = None
        self._syncing = True
        try:
            self.all_day.setChecked(all_day)
        finally:
            self._syncing = False
        self._show_times(all_day)
        self._show_moments(start, end)

    def _show_moments(self, start: datetime, end: datetime) -> None:
        """Take ``start`` and ``end`` as accepted and show them: the dates, the times, both
        lists and the end date's range, from the start's day to the longest an event lasts."""
        self._start, self._end = start.astimezone(UTC), end.astimezone(UTC)
        first = self._start.astimezone(self._zone)
        longest = EVENT_MAX_DAYS - 1 if self.all_day.isChecked() else EVENT_MAX_DAYS
        latest = first.date() + timedelta(days=longest)
        last = self._end.astimezone(self._zone)
        if last.date() > latest:
            self._end = local_instant(latest, time(last.hour, last.minute), self._zone)
            self._end = self._end.astimezone(UTC)
            last = self._end.astimezone(self._zone)
        self._syncing = True
        try:
            self.start_date.setDate(_qdate(first.date()))
            self.end_date.setDateRange(_qdate(first.date()), _qdate(latest))
            self.end_date.setDate(_qdate(last.date()))
            self.start_time.set_choices(
                [(f"{clock:%H:%M}", None) for clock in day_times(first.date(), self._zone)]
            )
            self.start_time.setTime(QTime(first.hour, first.minute))
            self.end_time.set_choices(end_choices(self._start, self._zone))
            self.end_time.setTime(QTime(last.hour, last.minute), self._end)
        finally:
            self._syncing = False

    def _edited_elsewhere(self) -> None:
        """The owner moved on from a refused time, so the next Save goes ahead."""
        self._refused = False

    def _show_times(self, all_day: bool) -> None:
        self.start_time.setVisible(not all_day)
        self.end_time.setVisible(not all_day)

    def _all_day_toggled(self, all_day: bool) -> None:
        self._show_times(all_day)
        if self._syncing:
            return
        self._refused = False
        if all_day:
            self._end_on_its_last_day()
        elif self._timed is not None and (self._start, self._end) == self._whole_days:
            self._start, self._end = self._timed  # Unticked with nothing changed: as it was.
        elif self._end <= self._start:
            self._end = self._start + _DEFAULT_LENGTH  # Back to times: an end after the start.
        if not all_day:
            self._timed = self._whole_days = None
        self._show_moments(self._start, self._end)  # The end date's range follows the shape.

    def _end_on_its_last_day(self) -> None:
        """An event that ends at midnight doesn't touch the day that begins then, so as an
        all-day event it ends the day before (a 20:00 to 00:00 evening is one day)."""
        end = self._end.astimezone(self._zone)
        first = self._start.astimezone(self._zone).date()
        if (end.hour, end.minute) != (0, 0) or end.date() <= first:
            return
        self._timed = (self._start, self._end)
        last = end.date() - timedelta(days=1)
        self._end = local_instant(last, time(), self._zone).astimezone(UTC)
        self._whole_days = (self._start, self._end)

    def _start_day_changed(self, value: QDate) -> None:
        if not self._syncing:
            clock = _clock(self.start_time.time())
            self._move_start(local_instant(_day(value), clock, self._zone))

    def _start_time_committed(self, _moment: object) -> None:
        self._take_start_text()

    def _take_start_text(self) -> bool:
        """Take the time typed or chosen in Starts, or refuse it; whether it was taken."""
        typed = parse_clock(self.start_time.currentText())
        if typed is None:
            self._refuse(BAD_TIME)
            return False
        day = _day(self.start_date.date())
        self._move_start(self._field_instant(day, typed, self._start))
        return True

    def _start_time_stepped(self, steps: int) -> None:
        """Up, Down, Page Up, Page Down or the wheel in Starts. A time typed but not yet
        taken is taken first, as leaving the field would; if it's refused, nothing steps.
        The start keeps its day, which has its own field: stepping past either end stops at
        the day's first or last listed time. The end moves with it."""
        try:
            if self.start_time.typing() and not self._take_start_text():
                return
            local = self._start.astimezone(self._zone)
            wall = stepped_wall(_wall(local), steps, self._zone)
            if wall.date() != local.date():
                times = day_times(local.date(), self._zone)
                wall = datetime.combine(local.date(), times[0] if steps < 0 else times[-1])
            self._move_start(local_instant(wall.date(), wall.time(), self._zone))
        finally:
            self._stepped()

    def _end_time_stepped(self, steps: int) -> None:
        """The same in Ends, on whichever day the end falls, with the usual refusals."""
        try:
            if self.end_time.typing() and not self._take_end_text():
                return
            wall = stepped_wall(_wall(self._end.astimezone(self._zone)), steps, self._zone)
            self._try_end(local_instant(wall.date(), wall.time(), self._zone))
        finally:
            self._stepped()

    def _stepped(self) -> None:
        """No Save comes with a step, and a refused one already says why on screen, so the
        next Save goes ahead rather than waiting (see _save)."""
        self._refused = False

    def _move_start(self, start: datetime) -> None:
        """A new start keeps the event's length, so the end moves with it."""
        length = self._end - self._start
        self._refused = False
        self.status.setText("")
        self._show_moments(start, start.astimezone(UTC) + length)

    def _end_day_changed(self, value: QDate) -> None:
        if not self._syncing:
            clock = _clock(self.end_time.time())
            self._try_end(local_instant(_day(value), clock, self._zone))

    def _end_time_committed(self, moment: object) -> None:
        if isinstance(moment, datetime):
            self._try_end(moment)
        else:
            self._take_end_text()

    def _take_end_text(self) -> bool:
        """Take the time typed in Ends, or refuse it; whether it was taken."""
        typed = parse_clock(self.end_time.currentText())
        if typed is None:
            self._refuse(BAD_TIME)
            return False
        return self._try_end(self._field_instant(_day(self.end_date.date()), typed, self._end))

    def _try_end(self, end: datetime) -> bool:
        """Take ``end`` only after the start and within the longest an event lasts; whether
        it was taken. For an all-day event only the day counts, and the end date's range
        already keeps it in."""
        late = end.astimezone(UTC) - self._start
        if not self.all_day.isChecked() and late <= timedelta(0):
            self._refuse(ENDS_FIRST)
            return False
        if not self.all_day.isChecked() and late > timedelta(days=EVENT_MAX_DAYS):
            self._refuse(TOO_LONG)
            return False
        self._refused = False
        self.status.setText("")
        self._show_moments(self._start, end)
        return True

    def _refuse(self, message: str) -> None:
        """Put the fields back to the last start and end they accepted, and say why. The
        Save that came with it, if any, waits (see _save)."""
        self._show_moments(self._start, self._end)
        self.status.setText(message)
        self._refused = True

    def _reset(self) -> None:
        self.status.setText(WAITING if self._busy else "")
        self._saving = False
        self._refused = False
        self._fields.setEnabled(True)
        self._update_buttons()
        self._loaded_input = self._input()  # What was just loaded counts as saved.
        self.title.setFocus()

    def built_edit(self) -> EventEdit | str:
        """The owner's edit, or the reason it can't be saved."""
        title = self.title.text().strip()
        if not title:
            return NO_TITLE
        notes = self.notes.toPlainText()
        if len(notes.strip()) > EVENT_NOTES_MAX_CHARS:
            return NOTES_TOO_LONG
        first, last = _day(self.start_date.date()), _day(self.end_date.date())
        try:
            if self.all_day.isChecked():
                if last < first:
                    return ENDS_FIRST
                if (last - first).days >= EVENT_MAX_DAYS:
                    return TOO_LONG
                return EventEdit(
                    title=title,
                    notes=notes,
                    all_day=True,
                    start_date=first,
                    last_date=last,
                    time_zone=self._zone.key,
                )
            start = self._field_instant(first, _clock(self.start_time.time()), self._start)
            end = self._field_instant(last, _clock(self.end_time.time()), self._end)
            if end <= start:
                return ENDS_FIRST
            if end - start > timedelta(days=EVENT_MAX_DAYS):
                return TOO_LONG
            return EventEdit(
                title=title,
                notes=notes,
                all_day=False,
                starts_at_utc=start,
                ends_at_utc=end,
                time_zone=self._zone.key,
            )
        except ValidationError:
            return UNUSABLE

    def _field_instant(self, day: date, clock: time, accepted: datetime) -> datetime:
        """The instant a date and time field show: the accepted one while they still show
        it, since rebuilding it from the clock would take the first of an hour a DST change
        repeats."""
        shown = accepted.astimezone(self._zone)
        if (shown.date(), time(shown.hour, shown.minute)) == (day, clock):
            return accepted
        return local_instant(day, clock, self._zone)

    def _save(self) -> None:
        if self._busy or self._saving:
            return
        # A time still being typed counts, as if the owner had left its field: a Save can
        # come without focus leaving it (a screen reader's press, for one).
        self.start_time.commit_typed()
        self.end_time.commit_typed()
        if self._refused:
            # A time was just refused (here, or by the Return or click that brought this
            # Save) and the fields went back: saving now would save what the owner was
            # trying to change.
            self._refused = False
            return
        edit = self.built_edit()
        if isinstance(edit, str):
            self.status.setText(edit)
            return
        # Lock first: the window may answer with keep_open() before emit() returns.
        self._set_saving(True)
        self.status.setText("Saving…")
        self.save_requested.emit(edit)

    def set_busy(self, busy: bool) -> None:
        """Another operation is running: Save waits for it, and says so, but Cancel still
        works. The editor's own save says Saving… instead."""
        self._busy = busy
        if busy and not self._saving and self.isVisible() and not self.status.text():
            self.status.setText(WAITING)
        elif not busy and self.status.text() == WAITING:
            self.status.setText("")
        self._update_buttons()

    def finish_saved(self) -> None:
        """The save succeeded: close the dialog."""
        self._set_saving(False)
        self.accept()

    def keep_open(self, message: str) -> None:
        """The save did not happen: unlock the dialog, keep every field and say why."""
        self._set_saving(False)
        self.status.setText(message)

    def force_close(self) -> None:
        """Close even while a save is running, as the window does when it quits."""
        self._set_saving(False)
        self.reject()

    def reject(self) -> None:
        if not self._saving:
            super().reject()

    def _set_saving(self, saving: bool) -> None:
        self._saving = saving
        self._fields.setEnabled(not saving)
        self._update_buttons()

    def _update_buttons(self) -> None:
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        if save is not None:
            save.setEnabled(not (self._busy or self._saving))
