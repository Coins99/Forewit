"""Plan with AI (ADR 0018, phase 3b): ask Groq for calendar changes, then keep the ones you want.

The dialog walks four steps: your instruction and what else to send; a preview of exactly
what leaves the device, with the first-use consent box; waiting for Groq; and the checked
proposals, each with its reason and the events it would overlap. Nothing changes until
Apply, and the window offers Undo after it. Titles and Groq's words are untrusted text, so
everything here is plain label or list-item text.
"""

from datetime import date
from typing import Final
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QListWidgetItem,
    QPlainTextEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.calendar_planning import (
    PLAN_INSTRUCTION_MAX_CHARS,
    CalendarPlan,
    PlanningOptions,
    PlanningPart,
    PlanningPreview,
    ProposedChange,
)
from mailbrief.domain.events import ChangeKind, EventChange, EventEdit
from mailbrief.ui.deadline_text import day_text
from mailbrief.ui.labels import outline_button, plain_label, wrap_label
from mailbrief.ui.lists import ActivatingList

NO_INSTRUCTION: Final = "Say what to change first."
TOO_LONG: Final = "Keep the instruction to 1,000 characters."
ASKING: Final = "Asking Groq…"
NOTHING_KEPT: Final = "Tick at least one change to apply."


def _when(edit: EventEdit, zone: ZoneInfo, today: date) -> str:
    """ "Wed Oct 14", "Wed Oct 14 – Fri Oct 16", "Wed Oct 14 11:00–12:00" or, across
    midnight, "Wed Oct 14 22:00 – Thu Oct 15 01:00"."""
    if edit.start_date is not None and edit.last_date is not None:
        first = day_text(edit.start_date, today=today)
        if edit.last_date == edit.start_date:
            return first
        return f"{first} – {day_text(edit.last_date, today=today)}"
    assert edit.starts_at_utc is not None and edit.ends_at_utc is not None
    start, end = edit.starts_at_utc.astimezone(zone), edit.ends_at_utc.astimezone(zone)
    if start.date() == end.date():
        return f"{day_text(start.date(), today=today)} {start:%H:%M}–{end:%H:%M}"
    return (
        f"{day_text(start.date(), today=today)} {start:%H:%M} – "
        f"{day_text(end.date(), today=today)} {end:%H:%M}"
    )


def proposal_text(proposal: ProposedChange, zone: ZoneInfo, today: date) -> str:
    """One proposal as plain text: what it does, then Groq's reason and any overlaps."""
    change = proposal.change
    if change.kind is ChangeKind.CREATE:
        assert change.edit is not None
        line = f"Add · {_when(change.edit, zone, today)} · {change.edit.title}"
    elif change.kind is ChangeKind.DELETE:
        assert change.event is not None
        line = f"Delete · {_when(change.event.edit(), zone, today)} · {change.event.title}"
    else:
        assert change.event is not None and change.edit is not None
        before, after = change.event.edit(), change.edit
        line = f"Change · {before.title}"
        if after.title != before.title:
            line += f" → {after.title}"
        old_when, new_when = _when(before, zone, today), _when(after, zone, today)
        line += f" · {old_when}" if old_when == new_when else f" · {old_when} → {new_when}"
    details = []
    if proposal.reason:
        details.append(proposal.reason)
    if proposal.overlaps:
        details.append("Overlaps: " + ", ".join(proposal.overlaps))
    return "\n".join([line, *details])


class PlanningDialog(QDialog):
    """Signals ask the window to act; the dialog itself only shows and collects.

    ``preview_requested(options)``, ``ask_requested(agreed)`` with whether the first-use
    consent box is ticked, ``apply_requested(changes)`` with the kept EventChanges, and
    ``cancel_requested()`` while Groq is being asked.
    """

    preview_requested = Signal(object)
    ask_requested = Signal(bool)
    apply_requested = Signal(object)
    cancel_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Plan with AI")
        self._proposals: tuple[ProposedChange, ...] = ()
        self._first_use = False
        layout = QVBoxLayout(self)
        # What went wrong or what is needed, above the step so it can't be missed.
        self.status = plain_label("", tone="warning")
        self.status.setObjectName("planningStatus")
        self.status.setAccessibleName("Plan with AI message")
        self.status.hide()
        layout.addWidget(self.status)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.close_button = outline_button("&Close", "planningCloseButton", mnemonic=True)
        self.close_button.clicked.connect(self.reject)
        row.addWidget(self.close_button)
        layout.addLayout(row)

        compose = QWidget()
        column = QVBoxLayout(compose)
        column.setContentsMargins(0, 0, 0, 0)  # Steps line up with the message and Close.
        column.addWidget(plain_label("What should change in your calendar?"))
        self.instruction = QPlainTextEdit()
        self.instruction.setObjectName("planningInstruction")
        self.instruction.setAccessibleName("Instruction")
        self.instruction.setTabChangesFocus(True)
        column.addWidget(self.instruction, 1)
        column.addWidget(
            plain_label("Your events in these four weeks are always sent.", tone="muted")
        )
        self.google_times = QCheckBox("Also send Google Calendar times, without titles")
        self.google_titles = QCheckBox("Include their titles too")
        self.open_actions = QCheckBox("Also send open actions' titles, deadlines and target dates")
        for box in (self.google_times, self.google_titles, self.open_actions):
            column.addWidget(box)
        self.google_times.toggled.connect(self._times_toggled)
        self.preview_button = outline_button(
            "Pre&view what is sent", "planningPreviewButton", mnemonic=True
        )
        self.preview_button.clicked.connect(self._request_preview)
        column.addWidget(self.preview_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.pages.addWidget(compose)

        confirm = QWidget()
        column = QVBoxLayout(confirm)
        column.setContentsMargins(0, 0, 0, 0)
        self.preview_heading = plain_label("")
        self.preview_lines = wrap_label("")
        self.privacy = wrap_label("", tone="muted")
        self.consent = QCheckBox("I agree to send this to Groq for calendar suggestions")
        self.consent.toggled.connect(lambda _on: self._update_ask())
        for widget in (self.preview_heading, self.preview_lines, self.privacy, self.consent):
            column.addWidget(widget)
        column.addStretch(1)
        row = QHBoxLayout()
        self.back_button = outline_button("&Back", "planningBackButton", mnemonic=True)
        self.ask_button = outline_button("Ask &Groq", "planningAskButton", mnemonic=True)
        self.back_button.clicked.connect(lambda: self._show(0))
        self.ask_button.clicked.connect(self._request_ask)
        row.addWidget(self.back_button)
        row.addWidget(self.ask_button)
        row.addStretch(1)
        column.addLayout(row)
        self.pages.addWidget(confirm)

        working = QWidget()
        column = QVBoxLayout(working)
        column.setContentsMargins(0, 0, 0, 0)
        column.addWidget(plain_label(ASKING))
        column.addStretch(1)
        self.stop_button = outline_button("Cancel request", "planningCancelButton")
        self.stop_button.clicked.connect(self.cancel_requested.emit)
        column.addWidget(self.stop_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.pages.addWidget(working)

        review = QWidget()
        column = QVBoxLayout(review)
        column.setContentsMargins(0, 0, 0, 0)
        self.message = wrap_label("")
        self.message.setObjectName("planningMessage")
        column.addWidget(self.message)
        self.list = ActivatingList()
        self.list.setObjectName("planningProposals")
        self.list.setAccessibleName("Proposed changes")
        self.list.itemChanged.connect(lambda _item: self._update_apply())
        column.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.apply_button = outline_button(
            "&Apply ticked changes", "planningApplyButton", mnemonic=True
        )
        self.discard_button = outline_button("&Discard", "planningDiscardButton", mnemonic=True)
        self.apply_button.clicked.connect(self._request_apply)
        self.discard_button.clicked.connect(self.reject)
        row.addWidget(self.apply_button)
        row.addWidget(self.discard_button)
        row.addStretch(1)
        column.addLayout(row)
        self.pages.addWidget(review)
        self.start_new(google_available=False)

    def start_new(self, *, google_available: bool) -> None:
        """A fresh request; Google's parts are offered only when Calendar is allowed."""
        self.instruction.setPlainText("")
        for box in (self.google_times, self.google_titles, self.open_actions):
            box.setChecked(False)
        self.google_times.setEnabled(google_available)
        self.google_titles.setEnabled(False)
        self.consent.setChecked(False)
        self._proposals = ()
        self.list.clear()
        self._show(0)
        self._say("")
        self.instruction.setFocus()

    def _times_toggled(self, on: bool) -> None:
        self.google_titles.setEnabled(on)
        if not on:
            self.google_titles.setChecked(False)

    def options(self) -> PlanningOptions | str:
        """The owner's choices, or why they can't be sent yet."""
        text = self.instruction.toPlainText().strip()
        if not text:
            return NO_INSTRUCTION
        if len(text) > PLAN_INSTRUCTION_MAX_CHARS:
            return TOO_LONG
        parts = set()
        if self.google_times.isChecked():
            parts.add(PlanningPart.GOOGLE_BUSY)
            if self.google_titles.isChecked():
                parts.add(PlanningPart.GOOGLE_TITLES)
        if self.open_actions.isChecked():
            parts.add(PlanningPart.ACTIONS)
        return PlanningOptions(instruction=text, parts=frozenset(parts))

    def _request_preview(self) -> None:
        options = self.options()
        if isinstance(options, str):
            self._say(options)
            return
        self._say("")
        self.preview_requested.emit(options)

    def show_preview(self, preview: PlanningPreview) -> None:
        """What will be sent, part by part; first use also needs the ticked box."""
        self._first_use = preview.first_use
        self.preview_heading.setText(f"This is sent to {preview.provider} ({preview.model}):")
        self.preview_lines.setText(
            "\n".join(f"· {line.label} ({line.characters:,} characters)" for line in preview.lines)
        )
        self.privacy.setText(preview.privacy_notice)
        self.consent.setVisible(preview.first_use)
        self.consent.setChecked(False)
        self._say("")
        self._show(1)
        self._update_ask()

    def _update_ask(self) -> None:
        self.ask_button.setEnabled(not self._first_use or self.consent.isChecked())

    def _request_ask(self) -> None:
        if self.ask_button.isEnabled():
            self.ask_requested.emit(self.consent.isChecked())

    def show_working(self) -> None:
        self._say("")
        self._show(2)

    def show_proposals(self, plan: CalendarPlan, zone: ZoneInfo, today: date) -> None:
        """The checked changes, all ticked; a note when Groq dropped some or had nothing."""
        self._proposals = plan.proposals
        self.list.clear()
        for proposal in plan.proposals:
            item = QListWidgetItem(proposal_text(proposal, zone, today))
            item.setFlags(
                Qt.ItemFlag.ItemIsEnabled
                | Qt.ItemFlag.ItemIsUserCheckable
                | Qt.ItemFlag.ItemIsSelectable
            )
            item.setCheckState(Qt.CheckState.Checked)
            self.list.addItem(item)
        notes = []
        if plan.message:
            notes.append(plan.message)
        if not plan.proposals:
            notes.append("Groq suggested no changes.")
        if plan.ignored:
            dropped = (
                "1 suggestion was" if plan.ignored == 1 else f"{plan.ignored} suggestions were"
            )
            notes.append(f"{dropped} left out because MailBrief couldn't use it.")
        self.message.setText(" ".join(notes))
        self._say("")
        self._show(3)
        self._update_apply()

    def kept(self) -> tuple[EventChange, ...]:
        return tuple(
            proposal.change
            for row, proposal in enumerate(self._proposals)
            if (item := self.list.item(row)) is not None
            and item.checkState() == Qt.CheckState.Checked
        )

    def _update_apply(self) -> None:
        self.apply_button.setEnabled(bool(self.kept()))

    def _request_apply(self) -> None:
        changes = self.kept()
        if not changes:
            self._say(NOTHING_KEPT)
            return
        self.apply_requested.emit(changes)

    def show_problem(self, message: str) -> None:
        """Back to the instruction, with what went wrong in fixed words."""
        self._show(0)
        self._say(message)

    def keep_reviewing(self, message: str) -> None:
        """Applying failed: stay on the proposals and say why."""
        self._say(message)

    def reject(self) -> None:
        """Close, Esc or Discard; closing while Groq is being asked stops the request too."""
        if self.pages.currentIndex() == 2:
            self.cancel_requested.emit()
        super().reject()

    def _say(self, message: str) -> None:
        """Show what needs attention above the step, or hide the line when there's nothing."""
        self.status.setText(message)
        self.status.setVisible(bool(message))

    def _show(self, page: int) -> None:
        self.pages.setCurrentIndex(page)
        # Close on the first two steps; asking has Cancel request and the review has Discard.
        self.close_button.setVisible(page in (0, 1))
