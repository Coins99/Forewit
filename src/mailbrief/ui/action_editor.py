"""Edit one action: its own fields and its plan, saved together as one revision."""

import math
import re
from datetime import date, time, timedelta
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
    DEADLINE_FIRST_DAY,
    DEADLINE_LAST_DAY,
    ESTIMATE_MAX_MINUTES,
    MAX_ACTION_STEPS,
    TARGET_REASON_TEXT,
    Action,
    ActionEdit,
    ActionPriority,
    ActionStatus,
    DeadlineChange,
    DeadlineOrigin,
    StepEdit,
    normalize_tags,
)
from mailbrief.domain.analysis import (
    ActionEffort,
    ActionOwnership,
    DeadlineFields,
    DeadlinePrecision,
    TargetReason,
)
from mailbrief.domain.projects import Project
from mailbrief.domain.recurrence import (
    ENDS_FIRST,
    RepeatEdit,
    RepeatFrequency,
    RepeatRule,
    ends_before,
    first_occurrence,
)
from mailbrief.services.deadlines import target_for_day
from mailbrief.ui.deadline_text import (
    FULL_WEEKDAYS,
    WEEKDAYS,
    deadline_text,
    length_text,
    parse_length,
)
from mailbrief.ui.dialog_chrome import dialog_heading
from mailbrief.ui.event_editor import DateBox, TimeBox, parse_clock
from mailbrief.ui.fields import takes_wheel
from mailbrief.ui.labels import outline_button, plain_label, primary_action, wrap_label
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
ANY_TIME: Final = "Any time"
BAD_DEADLINE_TIME: Final = (
    "Type the deadline's time as HH:mm, for example 17:00, or choose Any time."
)
NO_SUGGESTION: Final = "no target was suggested"


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


class DeadlineTimeBox(TimeBox):
    """A deadline's time of day: Any time, a time from the list in 15-minute steps, or one
    typed as HH:mm. It steps as the event editor's times do, Up a quarter hour later and
    Page Up an hour, the wheel only while it has focus, from whatever time it shows: Up from
    16:59 is 17:00, and Down from 00:00 is Any time."""

    def __init__(self) -> None:
        super().__init__("Deadline")
        self.setObjectName("taskDeadlineTime")
        self.setAccessibleName("Deadline time")
        line = self.lineEdit()
        assert line is not None
        line.setAccessibleName("Deadline time")
        self.setMinimumContentsLength(len(ANY_TIME))
        marks = range(0, 24 * 60, _QUARTER)
        self.set_choices(
            [(ANY_TIME, None), *((f"{mark // 60:02d}:{mark % 60:02d}", None) for mark in marks)]
        )
        self.set_clock(None)
        self.stepped.connect(self._step)

    def clock(self) -> time | None:
        """The time chosen or typed, or None for Any time (or nothing typed). Raises
        ValueError, with the reason, for text that isn't a time."""
        text = self.currentText().strip()
        if text in ("", ANY_TIME):
            return None
        found = parse_clock(text)
        if found is None:
            raise ValueError(BAD_DEADLINE_TIME)
        return found

    def set_clock(self, value: time | None) -> None:
        """Show ``value``, or Any time for None."""
        text = ANY_TIME if value is None else f"{value.hour:02d}:{value.minute:02d}"
        self.setCurrentIndex(self.findText(text))  # -1, no item, for a time between steps.
        self.setEditText(text)

    def _step(self, steps: int) -> None:
        """Move ``steps`` quarter hours, later when positive: from a time between marks, to
        the next mark that way. Before 00:00 comes Any time. A half-typed time stays."""
        try:
            clock = self.clock()
        except ValueError:
            return
        position = 0.0 if clock is None else 1 + (clock.hour * 60 + clock.minute) / _QUARTER
        index = math.floor(position) + steps if steps > 0 else math.ceil(position) + steps
        self.setCurrentIndex(max(0, min(self.count() - 1, index)))


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


def _deadline_shown(
    fields: DeadlineFields, zone: ZoneInfo | None
) -> tuple[date, time | None] | None:
    """The day and, for an exact deadline, the time the deadline fields show: in the owner's
    zone ``zone``, or the deadline's own without one. None when it has no day: none at all,
    or only the email's words."""
    at, own_zone = fields.deadline_at_utc, fields.deadline_timezone
    if fields.deadline_precision is DeadlinePrecision.DATETIME and at and own_zone:
        local = at.astimezone(zone or ZoneInfo(own_zone))
        return local.date(), time(local.hour, local.minute)
    if fields.deadline_precision is DeadlinePrecision.DATE and fields.deadline_date:
        return fields.deadline_date, None
    return None


def _deadline_note(action: Action | None, zone: ZoneInfo | None, today: date) -> str:
    """What the editor says under the deadline fields: an email's deadline in the email's own
    words, naming its own time when the email stated it in another zone; or, once the owner
    replaced or cleared it, what the email said. Nothing for a deadline the owner set alone."""
    if action is None:
        return ""
    if action.deadline_origin is DeadlineOrigin.EMAIL and action.deadline_text:
        words = f"The email says “{action.deadline_text}”"
        at, own_zone = action.deadline_at_utc, action.deadline_timezone
        if action.deadline_precision is DeadlinePrecision.UNRESOLVED:
            return f"{words}, with no specific day."
        exact = action.deadline_precision is DeadlinePrecision.DATETIME
        if exact and at and own_zone and zone is not None and zone.key != own_zone:
            local = at.astimezone(ZoneInfo(own_zone))  # The zone it was stated in.
            return f"{words} ({local:%H:%M} {own_zone})."
        return f"{words}."
    stated = action.stated_deadline
    if stated is None:
        return ""
    said = f"The email said “{stated.deadline_text}”"
    if stated.deadline_precision is not DeadlinePrecision.UNRESOLVED:
        own = ZoneInfo(stated.deadline_timezone or "UTC")
        if shown := deadline_text(stated, zone or own, today=today):
            said += f" ({shown})"
    if action.deadline_origin is DeadlineOrigin.OWNER:
        return f"You set this deadline. {said}."
    return f"You cleared the deadline. {said}."


def _qdate(day: date) -> QDate:
    return QDate(day.year, day.month, day.day)


def _suggestion_text(day: date | None, reason: TargetReason | None) -> str:
    if day is None or reason is None:
        return NO_SUGGESTION
    return f"{day.isoformat()}, {TARGET_REASON_TEXT[reason]}"


class ActionEditor(QDialog):
    """``save_requested(action, edit, steps, repeat, deadline)``: steps is None when the plan
    is unchanged, repeat a RepeatEdit, or None for a closed task, whose repeat can't change,
    and deadline a DeadlineChange, or None when the deadline fields are as they were.
    ``create_requested(edit, steps, rule, deadline)`` carries the new task's RepeatRule, or
    None, and its DeadlineChange, or None.

    The dialog validates what it can; the service still checks every bound and the revision.
    After asking, it stays open and locked until the window calls finish_saved(), which
    closes it, or keep_open(), which unlocks it with every field as the owner left it.
    """

    save_requested = Signal(object, object, object, object, object)
    create_requested = Signal(object, object, object, object)

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
        self._zone: ZoneInfo | None = None  # The owner's, which deadlines are shown in.
        self._options = True  # The optional rows are shown (see set_embedded).
        self._filling = False  # The editor is filling the deadline fields itself.
        self._back_to_email = False  # Use the email's was chosen and nothing changed since.
        self._loaded_deadline: tuple[object, ...] | None = None
        self._shown_suggestion: date | None = None  # The target the Suggested row offers.
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
        self.has_deadline = QCheckBox("Due &by")
        self.has_deadline.setObjectName("taskHasDeadline")
        self.has_deadline.setAccessibleName("The task has a deadline")
        self.deadline_date = DateBox()
        self.deadline_date.setObjectName("taskDeadlineDate")
        self.deadline_date.setAccessibleName("Deadline date")
        self.deadline_date.setCalendarPopup(True)
        self.deadline_date.setDisplayFormat("yyyy-MM-dd")
        self.deadline_date.setDateRange(_qdate(DEADLINE_FIRST_DAY), _qdate(DEADLINE_LAST_DAY))
        self.deadline_date.setKeyboardTracking(False)  # A typed date counts once it's finished.
        self.deadline_time = DeadlineTimeBox()
        self.deadline_row = QWidget()
        self.deadline_row.setObjectName("taskDeadline")
        self.deadline_row.setAccessibleName("Deadline")
        deadline_row = QHBoxLayout(self.deadline_row)
        deadline_row.setContentsMargins(0, 0, 0, 0)
        deadline_row.addWidget(self.has_deadline)
        deadline_row.addWidget(self.deadline_date, 1)  # Wide enough for any date, as Target.
        deadline_row.addWidget(self.deadline_time, 1)
        form.addRow("Deadline", self.deadline_row)
        # The email's words for its deadline, or what it said once the owner replaced it.
        self.deadline = wrap_label(tone="secondary")
        self.deadline.setObjectName("taskDeadlineNote")
        self.from_email = outline_button("Use the email's", "taskDeadlineFromEmail")
        self.from_email.setAccessibleName("Use the email's deadline")
        self.from_email.setAutoDefault(False)
        self.deadline_note = QWidget()
        self.deadline_note.setObjectName("taskDeadlineNoteRow")
        self.deadline_note.setAccessibleName("The email's deadline")
        note_row = QHBoxLayout(self.deadline_note)
        note_row.setContentsMargins(0, 0, 0, 0)
        note_row.addWidget(self.deadline, 1, Qt.AlignmentFlag.AlignVCenter)
        note_row.addWidget(self.from_email, 0, Qt.AlignmentFlag.AlignVCenter)
        form.addRow("", self.deadline_note)
        self.has_deadline.toggled.connect(self._deadline_edited)
        self.deadline_date.dateChanged.connect(self._deadline_edited)
        self.deadline_time.editTextChanged.connect(self._deadline_edited)
        self.from_email.clicked.connect(self._use_email_deadline)
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
        self._options = show
        for widget in (
            self.priority,
            self.tags,
            self.estimate,
            self.project,
            self.owner,
            self.effort,
            self.deadline_row,
            self.suggested,
            self.sources,
        ):
            self._form.setRowVisible(widget, show)
        self._show_deadline_note()
        for control in (self.plan_heading, self.steps, self.step_controls):
            control.setVisible(show)

    def _show_deadline_note(self) -> None:
        """The note's row shows with the other options, and only when it says something."""
        self._form.setRowVisible(self.deadline_note, self._options and bool(self.deadline.text()))

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
            self.has_deadline.isChecked()
            and (self.deadline_date.text(), self.deadline_time.currentText()),
            self._back_to_email,
        )

    def has_unsaved_input(self) -> bool:
        """Whether closing now would lose something the owner typed or chose. Not while
        it is being saved: quitting lets that save finish."""
        return not self._saving and self._input() != self._loaded_input

    def edit(self, action: Action, today: date, zone: ZoneInfo | None = None) -> None:
        """Show one action's current values; nothing is kept from an earlier edit.

        ``today`` is the owner's local date, which the date picker offers when the action
        has no target. ``zone`` is the owner's, so an exact deadline reads like the brief;
        without it, an exact deadline shows in the zone it was stated in.
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
        self._today, self._zone = today, zone
        self._show_deadline(action, action.target_date or today)
        self.suggested.setText(_suggestion_text(action.suggested_target_date, action.target_reason))
        self._shown_suggestion = action.suggested_target_date
        self.sources.setText(
            "; ".join(
                f"{source.subject or '(no subject)'} — {source.sender_address}"
                + ("" if source.available else " (no longer in local mail)")
                for source in action.sources
            )
            or "no source"
        )
        self.notes.setPlainText(action.notes)
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

    def new_task(self, today: date, zone: ZoneInfo | None = None) -> None:
        """Open an empty editor; cancelling creates no record. ``zone`` is the owner's."""
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
        self._today, self._zone = today, zone
        self._show_deadline(None, today)
        self.suggested.setText(NO_SUGGESTION)
        self._shown_suggestion = None
        self.sources.setText("Manual task")
        self.notes.clear()
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

    def _show_deadline(self, action: Action | None, day: date) -> None:
        """Show ``action``'s deadline (none for a new task) and what the email said; ``day``
        is the date the fields offer when there is no deadline to show."""
        self._fill_deadline(None if action is None else _deadline_shown(action, self._zone), day)
        self.deadline.setText(_deadline_note(action, self._zone, self._today))
        self.from_email.setVisible(self._offers_email())
        self._show_deadline_note()
        self._back_to_email = False
        self._loaded_deadline = self._deadline_input()

    def _fill_deadline(self, shown: tuple[date, time | None] | None, day: date) -> None:
        """Put a day and time in the deadline fields, ticked; or, for None, untick them and
        offer ``day``. Filling isn't an edit of the owner's."""
        self._filling = True
        try:
            picked, clock = shown if shown is not None else (day, None)
            self.has_deadline.setChecked(shown is not None)
            self.deadline_date.setDate(_qdate(picked))
            self.deadline_time.set_clock(clock)
        finally:
            self._filling = False
        self._enable_deadline()

    def _enable_deadline(self) -> None:
        ticked = self.has_deadline.isChecked()
        self.deadline_date.setEnabled(ticked)
        self.deadline_time.setEnabled(ticked)

    def _deadline_input(self) -> tuple[object, ...] | None:
        """The deadline as the fields hold it, or None when unticked."""
        if not self.has_deadline.isChecked():
            return None
        picked = self.deadline_date.date()
        return (picked.year(), picked.month(), picked.day()), self.deadline_time.currentText()

    def _picked_deadline(self) -> date | None:
        if not self.has_deadline.isChecked():
            return None
        picked = self.deadline_date.date()
        return date(picked.year(), picked.month(), picked.day())

    def _deadline_edited(self) -> None:
        """The owner changed the deadline: going back to the email's no longer applies, and
        the suggested target follows the new day."""
        self._enable_deadline()
        if self._filling:
            return
        self._back_to_email = False
        self.from_email.setVisible(self._offers_email())
        self._follow(self._picked_deadline())

    def _offers_email(self) -> bool:
        action = self._action
        return (
            action is not None
            and action.stated_deadline is not None
            and action.deadline_origin is not DeadlineOrigin.EMAIL
        )

    def _use_email_deadline(self) -> None:
        """Show the email's deadline again; saving goes back to it (DeadlineChange.from_email)."""
        action = self._action
        if action is None or action.stated_deadline is None:
            return
        shown = _deadline_shown(action.stated_deadline, self._zone)
        self._fill_deadline(shown, self._picked_deadline() or action.target_date or self._today)
        self._back_to_email = True
        self.from_email.hide()
        self._follow(None if shown is None else shown[0])

    def _follow(self, due: date | None) -> None:
        """Suggest a target for a deadline on ``due``, counted from today as the service
        counts it. A target date that was the suggestion moves to the new one; no target is
        ever ticked for the owner, and a cleared deadline suggests nothing and moves none."""
        if due is None:
            self.suggested.setText(NO_SUGGESTION)
            self._shown_suggestion = None
            return
        suggestion, reason = target_for_day(due, self._today)
        self.suggested.setText(_suggestion_text(suggestion, reason))
        target = self._picked_target()
        if target is not None and target == self._shown_suggestion:
            self.target.setDate(_qdate(suggestion))
        self._shown_suggestion = suggestion

    def _deadline_change(self) -> DeadlineChange | None:
        """What saving does to the deadline: nothing when the fields are as they were loaded;
        raises ValueError, with the reason, for a time that isn't one."""
        if self._back_to_email:
            return DeadlineChange.from_email()
        due = self._picked_deadline()
        clock = None if due is None else self.deadline_time.clock()
        if self._deadline_input() == self._loaded_deadline:
            return None
        return DeadlineChange.cleared() if due is None else DeadlineChange.to(due, clock)

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
        self.deadline_date.interpretText()  # Take a date still being typed, as leaving would.
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
            deadline = self._deadline_change()
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
            self.create_requested.emit(edit, plan or [], rule, deadline)
        elif action is not None:
            self.save_requested.emit(action, edit, plan, self._repeat_edit(action, rule), deadline)
