"""The owner's weekly working hours and buffer (C04, ADR 0029), in a drawer.

Each weekday is one line of text, such as "09:00–12:00, 13:00–17:00": up to three windows,
on quarter hours, and empty for a day without working hours. Typing is quicker than 21 time
pickers and works the same with a screen reader. The buffer is a choice. Nothing is saved
until Save, and Save sends the revision the editor was opened with, so a change made
elsewhere meanwhile is never overwritten. A day off is not set here: it is an all-day event
shown as Busy.
"""

import re
from typing import Final

from pydantic import ValidationError
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.working_hours import (
    BUFFER_CHOICES,
    DAY_MINUTES,
    HOURS_STEP_MINUTES,
    MAX_WINDOWS_PER_DAY,
    SUGGESTED_HOURS,
    DayWindows,
    HoursEdit,
    WorkingHours,
    WorkingWindow,
)
from mailbrief.ui.event_editor import WAITING
from mailbrief.ui.labels import outline_button, plain_label, wrap_label
from mailbrief.ui.theme import ui_font

DAY_NAMES: Final = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
NOT_WORKING: Final = "No working hours"
BAD_WINDOW: Final = "Write hours as 09:00–17:00, with commas between up to three."
QUARTER_HOURS: Final = "Working hours start and end on a quarter hour, such as 09:15."
ENDS_FIRST: Final = "Working hours must end after they start."
TOO_MANY: Final = "A day can have at most 3 working hours windows."
OVERLAP: Final = "A day's working hours can't overlap."
UNUSABLE: Final = "These working hours can't be saved; check them and try again."
_CLOCK: Final = r"(\d{1,2})(?::(\d{2}))?"
_WINDOW: Final = re.compile(rf"\s*{_CLOCK}\s*(?:-|–|—|to)\s*{_CLOCK}\s*", re.IGNORECASE)


def buffer_text(minutes: int) -> str:
    return "None" if minutes == 0 else f"{minutes} mins"


def windows_text(windows: DayWindows) -> str:
    """ "09:00–12:00, 13:00–17:00", the way the field shows a day."""
    return ", ".join(
        f"{_clock_text(window.start_minute)}–{_clock_text(window.end_minute)}" for window in windows
    )


def _clock_text(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def _minute(hours: str, minutes: str | None) -> int | None:
    hour, minute = int(hours), int(minutes or 0)
    if minute >= 60 or hour > 24 or (hour == 24 and minute):
        return None
    return hour * 60 + minute


def parse_windows(text: str) -> DayWindows | str:
    """A day's windows from the owner's text, in order, or the reason they can't be used.
    Empty text is a day without working hours."""
    pieces = [piece for piece in text.split(",") if piece.strip()]
    if not pieces:
        return ()
    if len(pieces) > MAX_WINDOWS_PER_DAY:
        return TOO_MANY
    found: list[WorkingWindow] = []
    for piece in pieces:
        match = _WINDOW.fullmatch(piece)
        if match is None:
            return BAD_WINDOW
        start, end = _minute(match[1], match[2]), _minute(match[3], match[4])
        if start is None or end is None or start >= DAY_MINUTES:
            return BAD_WINDOW
        if start % HOURS_STEP_MINUTES or end % HOURS_STEP_MINUTES:
            return QUARTER_HOURS
        if end <= start:
            return ENDS_FIRST
        found.append(WorkingWindow(start_minute=start, end_minute=end))
    found.sort(key=lambda window: window.start_minute)
    pairs = zip(found, found[1:], strict=False)
    if any(later.start_minute < earlier.end_minute for earlier, later in pairs):
        return OVERLAP
    return tuple(found)


class HoursEditor(QDialog):
    """``save_requested(edit)`` with an HoursEdit; ``revision()`` is the revision the hours
    had when the editor was opened. After asking, it stays open and locked until the window
    calls finish_saved(), which closes it, or keep_open(), which unlocks it as it was."""

    save_requested = Signal(object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("hoursEditorDialog")
        self.setAccessibleName("Working hours")
        self.setWindowTitle("Working hours")
        self.resize(520, 560)
        self._revision = 0
        self._busy = False
        self._saving = False
        layout = QVBoxLayout(self)
        self.setFont(ui_font(10))
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        kicker = plain_label("WORKING HOURS", tone="muted", px=9)
        kicker.setObjectName("hoursEditorKicker")
        layout.addWidget(kicker)
        heading = plain_label("When you work", px=21, medium=True)
        heading.setObjectName("hoursEditorHeading")
        layout.addWidget(heading)
        blurb = plain_label(
            "Free time is your working hours less busy events and the time set aside for "
            "tasks. For a day off, add an all-day event shown as Busy.",
            tone="muted",
            px=10,
        )
        blurb.setObjectName("hoursEditorBlurb")
        layout.addWidget(blurb)
        self.status = wrap_label("", tone="muted")  # Under the fields; each edit clears it.
        self.status.setObjectName("hoursStatus")
        self._fields = QWidget()
        form = QFormLayout(self._fields)
        form.setContentsMargins(0, 0, 0, 0)
        self.days: list[QLineEdit] = []
        for name in DAY_NAMES:
            field = QLineEdit()
            field.setObjectName(f"hours{name}")
            field.setAccessibleName(f"{name} working hours")
            field.setPlaceholderText(NOT_WORKING)
            field.setMaxLength(80)
            field.textEdited.connect(lambda _text: self.status.setText(""))
            self.days.append(field)
            form.addRow(name, field)
        self.buffer = QComboBox()
        self.buffer.setObjectName("hoursBuffer")
        self.buffer.setAccessibleName("Buffer before and after busy events")
        for minutes in BUFFER_CHOICES:
            self.buffer.addItem(buffer_text(minutes), minutes)
        form.addRow("Buffer", self.buffer)
        layout.addWidget(self._fields)
        presets = QHBoxLayout()
        self.suggest_button = outline_button("Weekdays 9 to 5", "hoursSuggestButton")
        self.copy_button = outline_button("Copy Monday to weekdays", "hoursCopyButton")
        for button in (self.suggest_button, self.copy_button):
            button.setAccessibleName(button.text())
            presets.addWidget(button)
        presets.addStretch(1)
        layout.addLayout(presets)
        layout.addWidget(self.status)
        layout.addStretch(1)
        buttons = QHBoxLayout()
        self.save_button = outline_button("Save", "hoursSaveButton", primary=True)
        self.cancel_button = outline_button("Cancel", "hoursCancelButton")
        for button in (self.save_button, self.cancel_button):
            button.setAccessibleName(button.text())
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.suggest_button.clicked.connect(self._suggest)
        self.copy_button.clicked.connect(self._copy_monday)
        self.save_button.clicked.connect(self._save)
        self.cancel_button.clicked.connect(self.reject)
        self._loaded_input = self._input()

    def revision(self) -> int:
        return self._revision

    def edit_hours(self, hours: WorkingHours) -> None:
        """Show ``hours``; when none are set, the fields start empty, and Weekdays 9 to 5
        fills them in."""
        self._revision = hours.revision
        for field, windows in zip(self.days, hours.days, strict=True):
            field.setText(windows_text(windows))
        self.buffer.setCurrentIndex(max(0, self.buffer.findData(hours.buffer_minutes)))
        self.status.setText(WAITING if self._busy else "")
        self._set_saving(False)
        self._loaded_input = self._input()
        self.days[0].setFocus(Qt.FocusReason.OtherFocusReason)

    def built_edit(self) -> HoursEdit | str:
        """The hours the fields show, or the reason they can't be saved, naming the day."""
        week: list[DayWindows] = []
        for name, field in zip(DAY_NAMES, self.days, strict=True):
            parsed = parse_windows(field.text())
            if isinstance(parsed, str):
                return f"{name}: {parsed}"
            week.append(parsed)
        minutes = self.buffer.currentData()
        try:
            return HoursEdit(days=tuple(week), buffer_minutes=int(minutes or 0))
        except ValidationError:
            return UNUSABLE

    def has_unsaved_input(self) -> bool:
        return not self._saving and self._input() != self._loaded_input

    def set_busy(self, busy: bool) -> None:
        """Another operation is running: Save waits for it, and says so."""
        self._busy = busy
        if busy and not self._saving and self.isVisible() and not self.status.text():
            self.status.setText(WAITING)
        elif not busy and self.status.text() == WAITING:
            self.status.setText("")
        self._update_buttons()

    def finish_saved(self) -> None:
        self._set_saving(False)
        self.accept()

    def keep_open(self, message: str) -> None:
        self._set_saving(False)
        self.status.setText(message)

    def force_close(self) -> None:
        self._set_saving(False)
        self.reject()

    def reject(self) -> None:
        if not self._saving:
            super().reject()

    def _input(self) -> tuple[object, ...]:
        return (*(field.text() for field in self.days), self.buffer.currentIndex())

    def _suggest(self) -> None:
        for field, windows in zip(self.days, SUGGESTED_HOURS.days, strict=True):
            field.setText(windows_text(windows))
        self.status.setText("")

    def _copy_monday(self) -> None:
        for field in self.days[1:5]:
            field.setText(self.days[0].text())
        self.status.setText("")

    def _save(self) -> None:
        if self._busy or self._saving:
            return
        built = self.built_edit()
        if isinstance(built, str):
            self.status.setText(built)
            return
        self._set_saving(True)
        self.status.setText("Saving…")
        self.save_requested.emit(built)

    def _set_saving(self, saving: bool) -> None:
        self._saving = saving
        self._fields.setEnabled(not saving)
        for button in (self.suggest_button, self.copy_button):
            button.setEnabled(not saving)
        self._update_buttons()

    def _update_buttons(self) -> None:
        self.save_button.setEnabled(not (self._busy or self._saving))
