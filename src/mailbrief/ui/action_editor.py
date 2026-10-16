"""Edit one action: its own fields and its plan, saved together as one revision."""

import re
from datetime import date, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from PySide6.QtCore import QDate, QModelIndex, QPersistentModelIndex, QStringListModel, Qt, Signal
from PySide6.QtGui import QValidator, QWheelEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QCompleter,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.actions import (
    ACTION_NOTES_MAX_CHARS,
    ACTION_STEP_MAX_CHARS,
    ACTION_TITLE_MAX_CHARS,
    ESTIMATE_MAX_MINUTES,
    MAX_ACTION_STEPS,
    TARGET_REASON_TEXT,
    Action,
    ActionEdit,
    ActionPriority,
    ActionStatus,
    StepEdit,
    normalize_tags,
)
from mailbrief.domain.analysis import ActionEffort, ActionOwnership, DeadlinePrecision
from mailbrief.domain.projects import Project
from mailbrief.domain.recurrence import (
    ENDS_FIRST,
    RepeatEdit,
    RepeatFrequency,
    RepeatRule,
    ends_before,
    first_occurrence,
)
from mailbrief.ui.deadline_text import FULL_WEEKDAYS, WEEKDAYS, length_text, parse_length
from mailbrief.ui.dialog_chrome import dialog_heading
from mailbrief.ui.fields import takes_wheel
from mailbrief.ui.labels import outline_button, plain_label, primary_action
from mailbrief.ui.theme import ui_font

_OWNERS = ((ActionOwnership.MINE, "Mine"), (ActionOwnership.WAITING_FOR, "Waiting for someone"))
_EFFORTS: tuple[tuple[ActionEffort | None, str], ...] = (
    (None, "Not set"),
    (ActionEffort.MINUTES, "Minutes"),
    (ActionEffort.HOURS, "Hours"),
    (ActionEffort.DAYS, "Days"),
)
_PRIORITIES: tuple[tuple[ActionPriority | None, str], ...] = (
    (None, "Not set"),
    (ActionPriority.HIGH, "High"),
    (ActionPriority.MEDIUM, "Medium"),
    (ActionPriority.LOW, "Low"),
)
_REPEATS: tuple[tuple[RepeatFrequency | None, str], ...] = (
    (None, "Doesn't repeat"),
    (RepeatFrequency.DAILY, "Every day"),
    (RepeatFrequency.WEEKLY, "Every week on…"),
    (RepeatFrequency.MONTHLY_DAY, "Every month on day…"),
    (RepeatFrequency.MONTHLY_LAST, "On the last day of each month"),
)
NO_WEEKDAY: Final = "Choose at least one day of the week."
_STEP_ID = Qt.ItemDataRole.UserRole
NO_ESTIMATE: Final = "Not set"
BAD_ESTIMATE: Final = "Type an estimate like 45, 1.5h, 1h 30 or 1:30, or clear it."
_QUARTER: Final = 15
_TYPING_LENGTH: Final = re.compile(r"[\d\s.,:hoursminte]*", re.IGNORECASE)


class EstimateBox(QSpinBox):
    """The owner's estimate, typed as minutes ("90"), hours ("1.5h"), both ("1h 30") or h:mm,
    and shown in the event editor's words ("1 hr 30 mins"). Arrow keys step to the next or
    previous quarter hour. Cleared or 0, it reads "Not set" and means none. Like the event
    editor's fields, the wheel changes it only while it has focus."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("taskEstimate")
        self.setAccessibleName("Estimated minutes")
        self.setRange(0, ESTIMATE_MAX_MINUTES)
        self.setSpecialValueText(NO_ESTIMATE)
        self.setKeyboardTracking(False)  # A typed estimate counts once it's finished.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)  # Tab and click; not the wheel.

    def minutes(self) -> int | None:
        return self.value() or None

    def textFromValue(self, val: int, /) -> str:
        return length_text(timedelta(minutes=val))

    def valueFromText(self, text: str, /) -> int:
        return parse_length(text) or 0  # Cleared, or "Not set": none.

    def validate(self, input: str, pos: int, /) -> tuple[QValidator.State, str, int]:
        text = input.strip()
        minutes = 0 if text in ("", NO_ESTIMATE) else parse_length(text)
        if minutes is None:  # Perhaps half typed, like "1." or "1:".
            typing = _TYPING_LENGTH.fullmatch(text) is not None
            return QValidator.State.Intermediate if typing else QValidator.State.Invalid, input, pos
        fits = minutes <= self.maximum()
        return QValidator.State.Acceptable if fits else QValidator.State.Invalid, input, pos

    def stepBy(self, steps: int) -> None:
        self.interpretText()  # From what is typed, as the arrows would without tracking off.
        value = self.value()
        quarters = value // _QUARTER if steps > 0 else -(-value // _QUARTER)
        self.setValue((quarters + steps) * _QUARTER)
        self.selectAll()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if takes_wheel(self, event):
            super().wheelEvent(event)


class DayBox(QSpinBox):
    """A day of the month for a monthly repeat; like the other fields, the wheel changes it
    only while it has focus."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("taskRepeatDay")
        self.setAccessibleName("Day of the month")
        self.setRange(1, 31)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if takes_wheel(self, event):
            super().wheelEvent(event)


class TagCompleter(QCompleter):
    """Completes the tag being typed after the last comma from the tags already in use,
    keeping the tags typed before it."""

    def __init__(self, field: QLineEdit) -> None:
        super().__init__(field)
        self._field = field
        # Parented and held here: a model only the completer referred to would be collected.
        self._tags = QStringListModel(self)
        self.setModel(self._tags)
        self.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setFilterMode(Qt.MatchFlag.MatchStartsWith)

    def set_tags(self, tags: tuple[str, ...]) -> None:
        self._tags.setStringList(list(tags))

    def splitPath(self, path: str, /) -> list[str]:
        return [path.rsplit(",", 1)[-1].strip()]

    def pathFromIndex(self, index: QModelIndex | QPersistentModelIndex, /) -> str:
        typed, _, _ = self._field.text().rpartition(",")
        chosen = str(index.data())
        return f"{typed}, {chosen}" if typed.strip() else chosen


def parse_tags(text: str) -> tuple[str, ...]:
    """The tags typed in the editor, separated by commas; raises ValueError like the model."""
    return normalize_tags(text.split(","))


def _plain(text: str) -> QLabel:
    """Action and source text must never become rich text or a link."""
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def _deadline(action: Action, owner_zone: ZoneInfo | None) -> str:
    """An exact deadline in the owner's zone, like the brief, naming the email's own time
    when the email stated it in another zone."""
    at, zone = action.deadline_at_utc, action.deadline_timezone
    if action.deadline_precision is DeadlinePrecision.DATETIME and at and zone:
        stated = at.astimezone(ZoneInfo(zone))  # The zone the time was stated in, not UTC.
        shown = at.astimezone(owner_zone) if owner_zone is not None else stated
        if owner_zone is None or owner_zone.key == zone:
            return f"{shown.isoformat(timespec='minutes')} (as the email states)"
        return f"{shown.isoformat(timespec='minutes')} ({stated:%H:%M} {zone} as the email states)"
    if action.deadline_precision is DeadlinePrecision.DATE and action.deadline_date:
        return f"{action.deadline_date.isoformat()} (date only)"
    if action.deadline_text:
        return f"“{action.deadline_text}” (no specific day)"
    return "none stated"


class ActionEditor(QDialog):
    """``save_requested(action, edit, steps, repeat)``: steps is None when the plan is
    unchanged, and repeat a RepeatEdit, or None for a closed task, whose repeat can't change.
    ``create_requested(edit, steps, rule)`` carries the new task's RepeatRule, or None.

    The dialog validates what it can; the service still checks every bound and the revision.
    After asking, it stays open and locked until the window calls finish_saved(), which
    closes it, or keep_open(), which unlocks it with every field as the owner left it.
    """

    save_requested = Signal(object, object, object, object)
    create_requested = Signal(object, object, object)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit action")
        self.setObjectName("actionEditor")
        self.setAccessibleName("Task editor")
        self.resize(620, 650)
        self._projects: tuple[Project, ...] = ()
        self._creating = False
        self.embedded = False
        self._action: Action | None = None
        self._original: list[tuple[int | None, str, bool]] = []
        self._today = date.today()  # Replaced by the owner's day whenever the editor opens.
        self._later_offered = False  # The task repeats, so its edit can go to later repeats.
        self._busy = False  # Another operation is running, so Save waits.
        self._saving = False  # This dialog's save is running, so nothing can change.
        layout = QVBoxLayout(self)
        dialog_heading(self, layout, "Task details", "Keep the task, dates and plan together.")
        # Every field and step control, so one switch locks them all while saving.
        self._fields = QWidget()
        fields = QVBoxLayout(self._fields)
        fields.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        self._form = form
        self.title = QLineEdit()
        self.title.setMaxLength(ACTION_TITLE_MAX_CHARS)
        form.addRow("&Title", self.title)
        self.project = QComboBox()
        self.project.setObjectName("taskProject")
        self.project.setAccessibleName("Task project")
        self.project.setMinimumContentsLength(18)
        self.project.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        form.addRow("Pro&ject", self.project)
        self.priority = QComboBox()
        self.priority.setObjectName("taskPriority")
        self.priority.setAccessibleName("Task priority")
        for _priority, name in _PRIORITIES:
            self.priority.addItem(name)
        self.tags = QLineEdit()
        self.tags.setObjectName("taskTags")
        self.tags.setAccessibleName("Task tags, separated by commas")
        self.tags.setPlaceholderText("Separate tags with commas")
        self._tag_completer = TagCompleter(self.tags)
        self.tags.setCompleter(self._tag_completer)
        # One row for both, like Target: the editor must still fit a small screen.
        priority_label = QLabel("&Priority")
        priority_label.setBuddy(self.priority)
        tags_label = QLabel("Ta&gs")
        tags_label.setBuddy(self.tags)
        organize_row = QHBoxLayout()
        organize_row.addWidget(self.priority)
        organize_row.addWidget(tags_label)
        organize_row.addWidget(self.tags, 1)
        form.addRow(priority_label, organize_row)
        self.owner = QComboBox()
        for _value, name in _OWNERS:
            self.owner.addItem(name)
        form.addRow("&Whose", self.owner)
        self.effort = QComboBox()
        for _effort, name in _EFFORTS:
            self.effort.addItem(name)
        form.addRow("Eff&ort", self.effort)
        self.estimate = EstimateBox()
        form.addRow("E&stimate", self.estimate)
        self.has_target = QCheckBox("Target &date")
        self.target = QDateEdit()
        self.target.setCalendarPopup(True)
        self.target.setDisplayFormat("yyyy-MM-dd")
        self.has_target.toggled.connect(self.target.setEnabled)
        target_row = QHBoxLayout()
        target_row.addWidget(self.has_target)
        target_row.addWidget(self.target)
        form.addRow("Target", target_row)
        self.repeat = QComboBox()
        self.repeat.setObjectName("taskRepeat")
        self.repeat.setAccessibleName("How the task repeats")
        for _frequency, name in _REPEATS:
            self.repeat.addItem(name)
        self.month_day = DayBox()
        self.repeat_ends = QCheckBox("Unt&il")
        self.repeat_ends.setObjectName("taskRepeatEnds")
        self.repeat_ends.setAccessibleName("The repeat has an end date")
        self.repeat_end = QDateEdit()
        self.repeat_end.setObjectName("taskRepeatEnd")
        self.repeat_end.setAccessibleName("The last date it repeats on")
        self.repeat_end.setCalendarPopup(True)
        self.repeat_end.setDisplayFormat("yyyy-MM-dd")
        self.repeat_ends.toggled.connect(self.repeat_end.setEnabled)
        repeat_label = QLabel("R&epeats")
        repeat_label.setBuddy(self.repeat)
        repeat_row = QHBoxLayout()
        repeat_row.addWidget(self.repeat)
        repeat_row.addWidget(self.month_day)
        repeat_row.addWidget(self.repeat_ends)
        repeat_row.addWidget(self.repeat_end, 1)
        form.addRow(repeat_label, repeat_row)
        self.weekdays = QWidget()
        self.weekdays.setObjectName("taskRepeatWeekdays")
        self.weekdays.setAccessibleName("Days of the week it repeats on")
        weekday_row = QHBoxLayout(self.weekdays)
        weekday_row.setContentsMargins(0, 0, 0, 0)
        self.weekday_boxes: list[QCheckBox] = []
        for short, full in zip(WEEKDAYS, FULL_WEEKDAYS, strict=True):
            box = QCheckBox(short)
            box.setAccessibleName(full)
            weekday_row.addWidget(box)
            self.weekday_boxes.append(box)
        weekday_row.addStretch(1)
        form.addRow("On", self.weekdays)
        self.later_too = QCheckBox("Change later repeats too")
        self.later_too.setObjectName("taskRepeatLater")
        self.later_too.setAccessibleName(
            "Change later repeats too: they start from this task's details, but not its date"
        )
        form.addRow("", self.later_too)
        self.repeat.currentIndexChanged.connect(self._repeat_changed)
        self.deadline = _plain("")
        form.addRow("Deadline", self.deadline)
        self.suggested = _plain("")
        form.addRow("Suggested", self.suggested)
        self.sources = _plain("")
        form.addRow("From", self.sources)
        self.notes = QPlainTextEdit()
        self.notes.setMinimumHeight(100)
        self.notes.setTabChangesFocus(True)
        form.addRow("&Notes", self.notes)
        fields.addLayout(form)
        self.plan_heading = _plain("Plan (check a step when it's done; double-click to rename):")
        fields.addWidget(self.plan_heading)
        self.steps = QListWidget()
        self.steps.setMinimumHeight(100)
        self.steps.setAccessibleName("Plan steps")
        fields.addWidget(self.steps)
        self.step_controls = QWidget()
        step_buttons = QHBoxLayout(self.step_controls)
        step_buttons.setContentsMargins(0, 0, 0, 0)
        self.add_step = QPushButton("&Add step")
        self.remove_step = QPushButton("&Remove step")
        self.up = QPushButton("Move &up")
        self.down = QPushButton("Move do&wn")
        for button in (self.add_step, self.remove_step, self.up, self.down):
            step_buttons.addWidget(button)
        fields.addWidget(self.step_controls)
        self.more_options = outline_button("More options", "taskMoreOptions")
        self.more_options.setCheckable(True)
        self.more_options.hide()
        self.more_options.toggled.connect(self._show_options)
        fields.addWidget(self.more_options, 0, Qt.AlignmentFlag.AlignLeft)
        fields.addStretch(1)
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("taskEditorScroll")
        self.scroll_area.setAccessibleName("Task fields and plan")
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setWidget(self._fields)
        layout.addWidget(self.scroll_area, 1)
        self.status = _plain("")
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        layout.addWidget(self.buttons)
        if (save := self.buttons.button(QDialogButtonBox.StandardButton.Save)) is not None:
            primary_action(save)
        self.add_step.clicked.connect(self._add_step)
        self.remove_step.clicked.connect(self._remove_step)
        self.up.clicked.connect(lambda: self._move(-1))
        self.down.clicked.connect(lambda: self._move(1))
        self.steps.currentRowChanged.connect(lambda _row: self._update_step_buttons())
        self.buttons.accepted.connect(self._save)
        self.buttons.rejected.connect(self.reject)
        self._loaded_input = self._input()
        self.title.textChanged.connect(self._update_buttons)

    def set_embedded(self, parent: QWidget | None, *, window: QWidget) -> None:
        """The web design creates tasks in the selected-action pane."""
        self.embedded = parent is not None
        self.setParent(parent or window)
        self.setWindowFlags(Qt.WindowType.Widget if self.embedded else Qt.WindowType.Dialog)
        heading = self.findChild(QLabel, "dialogHeading")
        caption = self.findChild(QLabel, "dialogDescription")
        if heading is not None:
            heading.setText("Create an action" if self.embedded else "Task details")
            heading.setFont(ui_font(23 if self.embedded else 21))
        if caption is not None:
            caption.setVisible(not self.embedded)
        layout = self.layout()
        if isinstance(layout, QVBoxLayout):
            layout.setContentsMargins(44 if self.embedded else 24, 30, 44, 24)
            if self.embedded and self.findChild(QLabel, "taskEditorKicker") is None:
                kicker = plain_label("NEW TASK", tone="muted", px=9)
                kicker.setObjectName("taskEditorKicker")
                layout.insertWidget(0, kicker)
            if (existing_kicker := self.findChild(QLabel, "taskEditorKicker")) is not None:
                existing_kicker.setVisible(self.embedded)
        self._form.setRowWrapPolicy(
            QFormLayout.RowWrapPolicy.WrapAllRows
            if self.embedded
            else QFormLayout.RowWrapPolicy.DontWrapRows
        )
        self._form.setVerticalSpacing(10 if self.embedded else 8)
        self._fields.setFont(ui_font(10))
        self.more_options.setVisible(self.embedded)
        self.more_options.setChecked(False)
        self._show_options(not self.embedded)
        fields_layout = self._fields.layout()
        root_layout = self.layout()
        if isinstance(fields_layout, QVBoxLayout) and isinstance(root_layout, QVBoxLayout):
            if self.embedded:
                root_layout.removeWidget(self.buttons)
                fields_layout.insertWidget(
                    fields_layout.count() - 1, self.buttons, 0, Qt.AlignmentFlag.AlignLeft
                )
                self.buttons.setCenterButtons(False)
                self.notes.setFixedHeight(100)
                self.title.setPlaceholderText("What needs to be done?")
                self.notes.setPlaceholderText("Add context or next steps")
            else:
                fields_layout.removeWidget(self.buttons)
                root_layout.addWidget(self.buttons)
                self.notes.setMinimumHeight(100)
                self.notes.setMaximumHeight(16777215)
        if (save := self.buttons.button(QDialogButtonBox.StandardButton.Save)) is not None:
            save.setText("Create action" if self.embedded else "Save")

    def _show_options(self, show: bool) -> None:
        for widget in (
            self.priority,
            self.tags,
            self.estimate,
            self.project,
            self.owner,
            self.effort,
            self.deadline,
            self.suggested,
            self.sources,
        ):
            self._form.setRowVisible(widget, show)
        for control in (self.plan_heading, self.steps, self.step_controls):
            control.setVisible(show)

    def _input(self) -> tuple[object, ...]:
        """Everything the owner can change here, to tell whether any of it is unsaved."""
        picked = self.target.date()
        return (
            self.title.text(),
            self.priority.currentIndex(),
            self.tags.text().strip(),
            self.owner.currentIndex(),
            self.project.currentData(),
            self.effort.currentIndex(),
            self.estimate.text(),
            (picked.year(), picked.month(), picked.day()) if self.has_target.isChecked() else None,
            self.notes.toPlainText(),
            tuple(self._current_steps()),
            self.repeat.currentIndex(),
            self.month_day.value(),
            tuple(box.isChecked() for box in self.weekday_boxes),
            self.repeat_ends.isChecked() and self.repeat_end.date().toString(Qt.DateFormat.ISODate),
            self.later_too.isChecked(),
        )

    def has_unsaved_input(self) -> bool:
        """Whether closing now would lose something the owner typed or chose. Not while
        it is being saved: quitting lets that save finish."""
        return not self._saving and self._input() != self._loaded_input

    def edit(self, action: Action, today: date, zone: ZoneInfo | None = None) -> None:
        """Show one action's current values; nothing is kept from an earlier edit.

        ``today`` is the owner's local date, which the date picker offers when the action
        has no target. ``zone`` is the owner's, so an exact deadline reads like the brief.
        """
        self.setWindowTitle("Edit action")
        self._creating = False
        self._set_saving(False)
        self._action = action
        self.title.setText(action.title)
        self._fill_projects(None if action.project is None else action.project.public_id)
        self.priority.setCurrentIndex([value for value, _ in _PRIORITIES].index(action.priority))
        self.tags.setText(", ".join(action.tags))
        self.owner.setCurrentIndex([value for value, _ in _OWNERS].index(action.ownership))
        self.effort.setCurrentIndex([value for value, _ in _EFFORTS].index(action.effort))
        self.estimate.setValue(action.estimate_minutes or 0)
        self.has_target.setChecked(action.target_date is not None)
        self.target.setEnabled(action.target_date is not None)
        shown = action.target_date or action.suggested_target_date or today
        self.target.setDate(QDate(shown.year, shown.month, shown.day))
        self.deadline.setText(_deadline(action, zone))
        if action.suggested_target_date is not None and action.target_reason is not None:
            self.suggested.setText(
                f"{action.suggested_target_date.isoformat()}, "
                f"{TARGET_REASON_TEXT[action.target_reason]}"
            )
        else:
            self.suggested.setText("no target was suggested")
        self.sources.setText(
            "; ".join(
                f"{source.subject or '(no subject)'} — {source.sender_address}"
                + ("" if source.available else " (no longer in local mail)")
                for source in action.sources
            )
            or "no source"
        )
        self.notes.setPlainText(action.notes)
        self._today = today
        repeat = action.repeat
        is_open = action.status is ActionStatus.OPEN
        self._later_offered = repeat is not None and not repeat.ended and is_open
        self._set_repeat(
            None if repeat is None or repeat.ended else repeat.rule, action.target_date or today
        )
        for control in (self.repeat, self.month_day, self.repeat_ends, self.weekdays):
            control.setEnabled(is_open)  # A closed task's repeat can't change.
        self.repeat_end.setEnabled(is_open and self.repeat_ends.isChecked())
        self.steps.clear()
        for step in action.steps:
            self._append_step(step.text, step.done, step.step_id)
        self._original = self._current_steps()
        self.status.clear()
        self._update_step_buttons()
        self._loaded_input = self._input()

    def new_task(self, today: date) -> None:
        """Open an empty editor; cancelling creates no record."""
        self._creating = True
        self._action = None
        self._set_saving(False)
        self.setWindowTitle("New task")
        self.title.clear()
        self._fill_projects(None)
        self.priority.setCurrentIndex(0)
        self.tags.clear()
        self.owner.setCurrentIndex(0)
        self.effort.setCurrentIndex(0)
        self.estimate.setValue(0)
        self.has_target.setChecked(False)
        self.target.setEnabled(False)
        self.target.setDate(QDate(today.year, today.month, today.day))
        self.deadline.setText("none stated")
        self.suggested.setText("no target was suggested")
        self.sources.setText("Manual task")
        self.notes.clear()
        self._today = today
        self._later_offered = False
        self._set_repeat(None, today)
        for control in (self.repeat, self.month_day, self.repeat_ends, self.weekdays):
            control.setEnabled(True)
        self.steps.clear()
        self._original = []
        self.status.clear()
        self._update_step_buttons()
        self._loaded_input = self._input()
        self.title.setFocus()

    def _set_repeat(self, rule: RepeatRule | None, day: date) -> None:
        """Show ``rule``, or no repeat; ``day`` (the task's date, or today) fills the day of
        the month and the end date it offers."""
        self.month_day.setValue(rule.month_day if rule and rule.month_day else day.day)
        chosen = set(rule.weekdays) if rule is not None else set()
        for number, box in enumerate(self.weekday_boxes):
            box.setChecked(number in chosen)
        end = None if rule is None else rule.end_date
        self.repeat_ends.setChecked(end is not None)
        self.repeat_end.setEnabled(end is not None)
        shown = end or day
        self.repeat_end.setDate(QDate(shown.year, shown.month, shown.day))
        self.later_too.setChecked(True)
        index = [frequency for frequency, _ in _REPEATS].index(
            None if rule is None else rule.frequency
        )
        self.repeat.setCurrentIndex(index)
        self._repeat_changed(index)

    def _repeat_changed(self, index: int) -> None:
        """Show what the chosen repeat needs; a weekly one starts on the task's weekday."""
        frequency = _REPEATS[index][0]
        weekly = frequency is RepeatFrequency.WEEKLY
        if weekly and not any(box.isChecked() for box in self.weekday_boxes):
            day = self._picked_target() or self._today
            self.weekday_boxes[day.weekday()].setChecked(True)
        self.month_day.setVisible(frequency is RepeatFrequency.MONTHLY_DAY)
        self.repeat_ends.setVisible(frequency is not None)
        self.repeat_end.setVisible(frequency is not None)
        self._form.setRowVisible(self.weekdays, weekly)
        self._form.setRowVisible(self.later_too, self._later_offered and frequency is not None)

    def _picked_target(self) -> date | None:
        if not self.has_target.isChecked():
            return None
        picked = self.target.date()
        return date(picked.year(), picked.month(), picked.day())

    def _rule(self) -> RepeatRule | None:
        """The repeat chosen; raises ValueError, with the reason, for one that can't be."""
        frequency = _REPEATS[self.repeat.currentIndex()][0]
        if frequency is None:
            return None
        weekdays = (
            tuple(number for number, box in enumerate(self.weekday_boxes) if box.isChecked())
            if frequency is RepeatFrequency.WEEKLY
            else ()
        )
        if frequency is RepeatFrequency.WEEKLY and not weekdays:
            raise ValueError(NO_WEEKDAY)
        end = self.repeat_end.date()
        rule = RepeatRule(
            frequency=frequency,
            weekdays=weekdays,
            month_day=self.month_day.value() if frequency is RepeatFrequency.MONTHLY_DAY else None,
            end_date=(
                date(end.year(), end.month(), end.day()) if self.repeat_ends.isChecked() else None
            ),
        )
        # The checks the service makes: a series keeps its occurrence date however the target
        # moves, and a task that starts repeating takes its target or the rule's first date.
        repeat = None if self._action is None else self._action.repeat
        if repeat is not None:
            if ends_before(rule, repeat.occurrence_date):
                raise ValueError(ENDS_FIRST)
        elif first_occurrence(rule, self._picked_target(), self._today) is None:
            raise ValueError(ENDS_FIRST)
        return rule

    def _repeat_edit(self, action: Action, rule: RepeatRule | None) -> RepeatEdit | None:
        if action.status is not ActionStatus.OPEN:
            return None
        return RepeatEdit(
            rule=rule,
            series_revision=None if action.repeat is None else action.repeat.series_revision,
            later_too=rule is not None and self._later_offered and self.later_too.isChecked(),
        )

    def set_projects(self, projects: tuple[Project, ...]) -> None:
        self._projects = projects

    def set_tags(self, tags: tuple[str, ...]) -> None:
        """The tags in use, sorted by key as the service lists them, for the Tags field to
        complete; the service keeps their spelling."""
        self._tag_completer.set_tags(tags)

    def _fill_projects(self, chosen: str | None) -> None:
        self.project.clear()
        self.project.addItem("Unassigned", None)
        for project in self._projects:
            if project.archived_at_utc is None or project.public_id == chosen:
                suffix = " (archived)" if project.archived_at_utc else ""
                self.project.addItem(project.name + suffix, project.public_id)
        # Retain a link if an editor was opened directly before the organization list loaded.
        if chosen is not None and self.project.findData(chosen) < 0 and self._action is not None:
            linked_project = self._action.project
            if linked_project is not None:
                self.project.addItem(linked_project.name, chosen)
        self.project.setCurrentIndex(max(0, self.project.findData(chosen)))

    def set_busy(self, busy: bool) -> None:
        """Another operation is running: Save waits for it, but Cancel still works."""
        self._busy = busy
        self._update_buttons()

    def finish_saved(self) -> None:
        """The save succeeded: close the dialog."""
        self._set_saving(False)
        self.accept()

    def done(self, result: int) -> None:
        """Accepted or discarded input must not resume as a pending inline task."""
        self._loaded_input = self._input()
        self._creating = False
        super().done(result)

    def keep_open(self, message: str) -> None:
        """The save did not happen: unlock the dialog, keep every field and say why."""
        self._set_saving(False)
        self.status.setText(message)

    def force_close(self) -> None:
        """Close even while a save is running, as the window does when it quits."""
        self._set_saving(False)
        super().reject()

    def reject(self) -> None:
        """Cancel, Esc and the close button do nothing while a save is running."""
        if not self._saving:
            super().reject()

    def _set_saving(self, saving: bool) -> None:
        self._saving = saving
        self._fields.setEnabled(not saving)
        self._update_buttons()

    def _update_buttons(self) -> None:
        save = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        if save is not None:
            save.setEnabled(
                not (self._busy or self._saving)
                and (not self.embedded or bool(self.title.text().strip()))
            )
        cancel = self.buttons.button(QDialogButtonBox.StandardButton.Cancel)
        if cancel is not None:
            cancel.setEnabled(not self._saving)

    def _append_step(self, text: str, done: bool, step_id: int | None) -> QListWidgetItem:
        item = QListWidgetItem(text)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEditable)
        item.setCheckState(Qt.CheckState.Checked if done else Qt.CheckState.Unchecked)
        item.setData(_STEP_ID, step_id)
        self.steps.addItem(item)
        return item

    def _current_steps(self) -> list[tuple[int | None, str, bool]]:
        found: list[tuple[int | None, str, bool]] = []
        for index in range(self.steps.count()):
            item = self.steps.item(index)
            if item is not None:
                step_id = item.data(_STEP_ID)
                found.append(
                    (
                        step_id if isinstance(step_id, int) else None,
                        item.text().strip(),
                        item.checkState() == Qt.CheckState.Checked,
                    )
                )
        return found

    def _update_step_buttons(self) -> None:
        row = self.steps.currentRow()
        self.add_step.setEnabled(self.steps.count() < MAX_ACTION_STEPS)
        self.remove_step.setEnabled(row >= 0)
        self.up.setEnabled(row > 0)
        self.down.setEnabled(0 <= row < self.steps.count() - 1)

    def _add_step(self) -> None:
        if self.steps.count() >= MAX_ACTION_STEPS:
            return
        item = self._append_step("New step", False, None)
        self.steps.setCurrentItem(item)
        self.steps.editItem(item)
        self._update_step_buttons()

    def _remove_step(self) -> None:
        row = self.steps.currentRow()
        if row >= 0:
            self.steps.takeItem(row)
        self._update_step_buttons()

    def _move(self, offset: int) -> None:
        row = self.steps.currentRow()
        target = row + offset
        if row < 0 or not 0 <= target < self.steps.count():
            return
        item = self.steps.takeItem(row)
        self.steps.insertItem(target, item)
        self.steps.setCurrentRow(target)
        self._update_step_buttons()

    def _save(self) -> None:
        action = self._action
        if (action is None and not self._creating) or self._busy or self._saving:
            return
        title = self.title.text().strip()
        notes = self.notes.toPlainText()
        steps = [step for step in self._current_steps() if step[1]]
        if not title:
            self.status.setText("Enter a title.")
            return
        if len(notes) > ACTION_NOTES_MAX_CHARS:
            self.status.setText(f"Notes can be at most {ACTION_NOTES_MAX_CHARS:,} characters.")
            return
        if any(len(text) > ACTION_STEP_MAX_CHARS for _, text, _ in steps):
            self.status.setText(f"A step can be at most {ACTION_STEP_MAX_CHARS} characters.")
            return
        if not self.estimate.hasAcceptableInput():
            self.status.setText(BAD_ESTIMATE)
            return
        try:
            tags = parse_tags(self.tags.text())
        except ValueError as exc:
            self.status.setText(str(exc))  # The model's own words, which name no tag.
            return
        try:
            rule = self._rule()
        except ValueError as exc:
            self.status.setText(str(exc))
            return
        self.estimate.interpretText()  # Take what is typed, as leaving the field would.
        picked = self.target.date()
        project_id = self.project.currentData()
        edit = ActionEdit(
            project_public_id=project_id if isinstance(project_id, str) else None,
            title=title,
            ownership=_OWNERS[self.owner.currentIndex()][0],
            priority=_PRIORITIES[self.priority.currentIndex()][0],
            tags=tags,
            effort=_EFFORTS[self.effort.currentIndex()][0],
            estimate_minutes=self.estimate.minutes(),
            target_date=(
                date(picked.year(), picked.month(), picked.day())
                if self.has_target.isChecked()
                else None
            ),
            notes=notes,
        )
        plan = (
            None
            if steps == self._original
            else [StepEdit(step_id=step_id, text=text, done=done) for step_id, text, done in steps]
        )
        # Lock first: the window may answer with keep_open() before emit() returns.
        self._set_saving(True)
        self.status.setText("Saving…")
        if self._creating:
            self.create_requested.emit(edit, plan or [], rule)
        elif action is not None:
            self.save_requested.emit(action, edit, plan, self._repeat_edit(action, rule))
