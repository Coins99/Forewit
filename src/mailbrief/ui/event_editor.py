"""Add or change one calendar event: its title, when it happens and its notes (ADR 0018).

Dates and times use fixed numeric formats, so nothing depends on the system language.
"""

from datetime import date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from PySide6.QtCore import QDate, QTime, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QTimeEdit,
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
_DEFAULT_HOUR: Final = 9


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
        self.all_day.toggled.connect(self._show_times)
        form.addRow("", self.all_day)
        self.start_date, self.start_time = self._moment("Starts")
        self.end_date, self.end_time = self._moment("Ends")
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

    @staticmethod
    def _moment(name: str) -> tuple[QDateEdit, QTimeEdit]:
        day = QDateEdit()
        day.setCalendarPopup(True)
        day.setDisplayFormat(DATE_FORMAT)
        day.setAccessibleName(f"{name} on")
        clock = QTimeEdit()
        clock.setDisplayFormat(TIME_FORMAT)
        clock.setAccessibleName(f"{name} at")
        return day, clock

    def editing(self) -> CalendarEvent | None:
        """The event being changed, or None for a new one. (Not ``event``: Qt dispatches
        every event through QObject.event.)"""
        return self._event

    def edit_new(self, day: date, now: datetime, zone: ZoneInfo) -> None:
        """Start a new one-hour event on ``day``."""
        start = suggested_start(day, now, zone)
        end = start + timedelta(hours=1)
        self._event = None
        self._zone = zone
        self.setWindowTitle("New event")
        self.title.setText("")
        self.notes.setPlainText("")
        self.all_day.setChecked(False)
        self._set_moments(start, end)
        self._show_times(False)
        self._reset()

    def edit_event(self, event: CalendarEvent, zone: ZoneInfo) -> None:
        """Change ``event``; its times are shown in ``zone``."""
        self._event = event
        self._zone = zone
        self.setWindowTitle("Edit event")
        self.title.setText(event.title)
        self.notes.setPlainText(event.notes)
        self.all_day.setChecked(event.all_day)
        if event.start_date is not None and event.last_date is not None:
            start = local_instant(event.start_date, time(_DEFAULT_HOUR), zone)
            end = local_instant(event.last_date, time(_DEFAULT_HOUR + 1), zone)
        else:
            assert event.starts_at_utc is not None and event.ends_at_utc is not None
            start = event.starts_at_utc.astimezone(zone)
            end = event.ends_at_utc.astimezone(zone)
        self._set_moments(start, end)
        self._show_times(event.all_day)
        self._reset()

    def _set_moments(self, start: datetime, end: datetime) -> None:
        self.start_date.setDate(_qdate(start.date()))
        self.start_time.setTime(QTime(start.hour, start.minute))
        self.end_date.setDate(_qdate(end.date()))
        self.end_time.setTime(QTime(end.hour, end.minute))

    def _show_times(self, all_day: bool) -> None:
        self.start_time.setVisible(not all_day)
        self.end_time.setVisible(not all_day)

    def _reset(self) -> None:
        self.status.setText("")
        self._saving = False
        self._fields.setEnabled(True)
        self._update_buttons()
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
