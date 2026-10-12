"""Add or change one calendar event: its title, when it happens and its notes (ADR 0018).

Dates and times use fixed numeric formats, so nothing depends on the system language. A
time is typed or chosen from a list in 15-minute steps; the end's list says how long the
event lasts. The editor never takes an end at or before the start: moving the start keeps
the event's length, and an end that doesn't fit is refused with the reason.
"""

import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from PySide6.QtCore import QDate, Qt, QTime, Signal
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
from mailbrief.ui.labels import plain_label

DATE_FORMAT: Final = "yyyy-MM-dd"
TIME_FORMAT: Final = "HH:mm"
NO_TITLE: Final = "Add a title."
ENDS_FIRST: Final = "The end must be after the start."
TOO_LONG: Final = "An event can last at most 31 days."
NOTES_TOO_LONG: Final = "Notes can hold at most 10,000 characters."
UNUSABLE: Final = "These details can't be saved; check them and try again."
BAD_TIME: Final = "Type a time as HH:mm, for example 09:30."
STEP: Final = timedelta(minutes=15)
_STEPS_A_DAY: Final = 96
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


def length_text(length: timedelta) -> str:
    """How long an event lasts, in a list of end times: "45 mins", "1 hr", "2 hrs 15 mins"."""
    hours, minutes = divmod(int(length.total_seconds()) // 60, 60)
    parts = [f"{hours} hr" if hours == 1 else f"{hours} hrs"] if hours else []
    if minutes or not hours:
        parts.append(f"{minutes} mins")
    return " ".join(parts)


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


class TimeBox(QComboBox):
    """A time of day, typed as HH:mm or chosen from a list in 15-minute steps.

    ``committed(moment)`` says the owner chose a listed time (``moment`` is the item's
    data, or None when it has none) or finished typing (None). Nothing is committed when a
    time is shown with setTime(); the editor decides whether a committed time fits.
    """

    committed = Signal(object)

    def __init__(self, name: str) -> None:
        super().__init__()
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setCompleter(QCompleter([], self))  # Typing "1" never jumps to a listed "1…".
        self.setMaxVisibleItems(10)
        self.setMinimumContentsLength(5)
        self.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.setAccessibleName(f"{name} at")
        self.view().setTextElideMode(Qt.TextElideMode.ElideNone)
        self._time = QTime(0, 0)
        line = self.lineEdit()
        assert line is not None
        line.setAccessibleName(f"{name} at")
        line.editingFinished.connect(self._typed)
        self.activated.connect(self._chosen)

    def time(self) -> QTime:
        return self._time

    def setTime(self, value: QTime) -> None:  # noqa: N802 - QTimeEdit's name, kept for callers.
        """Show ``value`` alone in the field, picked in the list when it's there."""
        self._time = QTime(value.hour(), value.minute())
        text = self._time.toString(TIME_FORMAT)
        self.blockSignals(True)
        listed = [index for index in range(self.count()) if self.itemText(index)[:5] == text]
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
        self.setTime(self._time)

    def _chosen(self, index: int) -> None:
        if index >= 0:
            self.committed.emit(self.itemData(index))

    def _typed(self) -> None:
        if self.currentText() != self._time.toString(TIME_FORMAT):
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
        self._event: CalendarEvent | None = None
        self._zone = ZoneInfo("UTC")
        self._busy = False  # Another operation is running, so Save waits.
        self._saving = False  # This dialog's save is running, so nothing can change.
        # The last start and end the fields accepted, in UTC, so a refused change goes back.
        self._start = datetime(2000, 1, 1, _DEFAULT_HOUR, tzinfo=UTC)
        self._end = self._start + _DEFAULT_LENGTH
        self._syncing = False  # The editor is filling the fields itself.
        layout = QVBoxLayout(self)
        self._fields = QWidget()
        form = QFormLayout(self._fields)
        form.setContentsMargins(0, 0, 0, 0)
        self.title = QLineEdit()
        self.title.setObjectName("eventTitle")
        self.title.setMaxLength(EVENT_TITLE_MAX_CHARS)
        self.title.setAccessibleName("Title")
        form.addRow("Title", self.title)
        self.all_day = QCheckBox("All day")
        self.all_day.setObjectName("eventAllDay")
        self.all_day.toggled.connect(self._all_day_toggled)
        form.addRow("", self.all_day)
        self.start_date, self.start_time = self._moment("Starts", "eventStartTime")
        self.end_date, self.end_time = self._moment("Ends", "eventEndTime")
        self.start_date.dateChanged.connect(self._start_day_changed)
        self.end_date.dateChanged.connect(self._end_day_changed)
        self.start_time.committed.connect(self._start_time_committed)
        self.end_time.committed.connect(self._end_time_committed)
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
        layout.addWidget(self.buttons)
        self._update_buttons()
        self._loaded_input = self._input()

    def _input(self) -> tuple[object, ...]:
        """Everything the owner can change here, to tell whether any of it is unsaved."""
        return (
            self.title.text(),
            self.notes.toPlainText(),
            self.all_day.isChecked(),
            self.start_date.date(),
            self.start_time.time(),
            self.end_date.date(),
            self.end_time.time(),
        )

    def has_unsaved_input(self) -> bool:
        """Whether closing now would lose something the owner typed or chose. Not while
        it is being saved: quitting lets that save finish."""
        return not self._saving and self._input() != self._loaded_input

    @staticmethod
    def _moment(name: str, clock_name: str) -> tuple[QDateEdit, TimeBox]:
        day = QDateEdit()
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
        self._event = None
        self._zone = zone
        self.setWindowTitle("New event")
        self.title.setText("")
        self.notes.setPlainText("")
        self._load(start, start + _DEFAULT_LENGTH, all_day=False)
        self._reset()

    def edit_event(self, event: CalendarEvent, zone: ZoneInfo) -> None:
        """Change ``event``; its times are shown in ``zone``."""
        self._event = event
        self._zone = zone
        self.setWindowTitle("Edit event")
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
            self.end_time.setTime(QTime(last.hour, last.minute))
        finally:
            self._syncing = False

    def _show_times(self, all_day: bool) -> None:
        self.start_time.setVisible(not all_day)
        self.end_time.setVisible(not all_day)

    def _all_day_toggled(self, all_day: bool) -> None:
        self._show_times(all_day)
        if self._syncing:
            return
        if not all_day and self._end <= self._start:
            self._end = self._start + _DEFAULT_LENGTH  # Back to times: an end after the start.
        self._show_moments(self._start, self._end)  # The end date's range follows the shape.

    def _start_day_changed(self, value: QDate) -> None:
        if not self._syncing:
            clock = _clock(self.start_time.time())
            self._move_start(local_instant(_day(value), clock, self._zone))

    def _start_time_committed(self, _moment: object) -> None:
        typed = parse_clock(self.start_time.currentText())
        if typed is None:
            self._refuse(BAD_TIME)
            return
        self._move_start(local_instant(_day(self.start_date.date()), typed, self._zone))

    def _move_start(self, start: datetime) -> None:
        """A new start keeps the event's length, so the end moves with it."""
        length = self._end - self._start
        self.status.setText("")
        self._show_moments(start, start.astimezone(UTC) + length)

    def _end_day_changed(self, value: QDate) -> None:
        if not self._syncing:
            clock = _clock(self.end_time.time())
            self._try_end(local_instant(_day(value), clock, self._zone))

    def _end_time_committed(self, moment: object) -> None:
        if isinstance(moment, datetime):
            self._try_end(moment)
            return
        typed = parse_clock(self.end_time.currentText())
        if typed is None:
            self._refuse(BAD_TIME)
            return
        self._try_end(local_instant(_day(self.end_date.date()), typed, self._zone))

    def _try_end(self, end: datetime) -> None:
        """Take ``end`` only after the start and within the longest an event lasts. For an
        all-day event only the day counts, and the end date's range already keeps it in."""
        late = end.astimezone(UTC) - self._start
        if not self.all_day.isChecked() and late <= timedelta(0):
            self._refuse(ENDS_FIRST)
        elif not self.all_day.isChecked() and late > timedelta(days=EVENT_MAX_DAYS):
            self._refuse(TOO_LONG)
        else:
            self.status.setText("")
            self._show_moments(self._start, end)

    def _refuse(self, message: str) -> None:
        """Put the fields back to the last start and end they accepted, and say why."""
        self._show_moments(self._start, self._end)
        self.status.setText(message)

    def _reset(self) -> None:
        self.status.setText("")
        self._saving = False
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
            start = local_instant(first, _clock(self.start_time.time()), self._zone)
            end = local_instant(last, _clock(self.end_time.time()), self._zone)
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

    def _save(self) -> None:
        if self._busy or self._saving:
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
        """Another operation is running: Save waits for it, but Cancel still works."""
        self._busy = busy
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
