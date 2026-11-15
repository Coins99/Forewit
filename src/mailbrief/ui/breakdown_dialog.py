"""Break down with AI (ADR 0033): ask Groq for steps for one task, then keep the ones you want.

The dialog walks four steps: your instruction and what else to send; a preview of exactly
what leaves the device, with the first-use consent box; waiting for Groq; and the proposed
steps, each with a tick and a field to edit it. Nothing changes until Apply, and the window
offers Undo after it. The task's title and Groq's words are untrusted text, so they are
shown only in plain labels and line edits.
"""

from typing import Final

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.actions import ACTION_STEP_MAX_CHARS, Action
from mailbrief.domain.breakdown import (
    BREAKDOWN_INSTRUCTION_MAX_CHARS,
    MAX_PROPOSED_STEPS,
    PART_ORDER,
    BreakdownOptions,
    BreakdownPart,
    BreakdownPreview,
    BreakdownProposal,
    KeptStep,
    available_parts,
)
from mailbrief.services.breakdown import disclosure_lines
from mailbrief.ui.dialog_chrome import dialog_heading
from mailbrief.ui.labels import outline_button, plain_label, wrap_label
from mailbrief.ui.theme import TEXT_PX, ui_font

TOO_LONG: Final = "Keep the instruction to 1,000 characters."
ASKING: Final = "Asking Groq…"
NOTHING_KEPT: Final = "Tick at least one step to apply."
EMPTY_STEP: Final = "A ticked step needs some words; untick it or type them."
ALWAYS_SENT: Final = "The task's title, today's date and your instruction are always sent."
PART_TEXT: Final = {
    BreakdownPart.NOTES: "Also send its notes",
    BreakdownPart.STEPS: "Also send its existing steps and their done marks",
    BreakdownPart.PROJECT: "Also send its project's and area's names",
    BreakdownPart.DATES: "Also send its deadline and target date",
    BreakdownPart.ESTIMATE: "Also send your estimate",
}
_COMPOSE, _CONFIRM, _WORKING, _REVIEW = range(4)


def slots_text(ticked: int, free: int) -> str:
    """How the ticked steps fit under the task's limit of steps."""
    room = "1 more step" if free == 1 else f"{free} more steps"
    chosen = "1 step ticked" if ticked == 1 else f"{ticked} steps ticked"
    if ticked > free:
        return f"{chosen}, but this task has room for {room}. Untick some."
    return f"{chosen}. This task has room for {room}."


class _StepRow(QWidget):
    """One proposed step: a tick to keep it and a field to change its words."""

    def __init__(self, number: int) -> None:
        super().__init__()
        self.setObjectName(f"breakdownStep{number}")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.box = QCheckBox()
        self.box.setObjectName(f"breakdownKeep{number}")
        self.box.setAccessibleName(f"Keep step {number}")
        self.field = QLineEdit()
        self.field.setObjectName(f"breakdownText{number}")
        self.field.setAccessibleName(f"Proposed step {number}")
        self.field.setMaxLength(ACTION_STEP_MAX_CHARS)
        layout.addWidget(self.box)
        layout.addWidget(self.field, 1)


class BreakdownDialog(QDialog):
    """Signals ask the window to act; the dialog itself only shows and collects.

    ``preview_requested(options)``, ``ask_requested(agreed)`` with whether the first-use
    consent box is ticked, ``apply_requested(kept)`` with the KeptSteps, and
    ``cancel_requested()`` while Groq is being asked. Closing is the dialog's ``finished``:
    the window records a preview that was closed, or a proposal that was turned down.
    """

    preview_requested = Signal(object)
    ask_requested = Signal(bool)
    apply_requested = Signal(object)
    cancel_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Break down with AI")
        self.setObjectName("breakdownDialog")
        self.setFont(ui_font(10))
        self.setMinimumSize(480, 520)
        self._first_use = False
        self._free = 0
        self._shown = 0  # How many of the step rows hold a proposed step.
        self._applying = False  # Apply was asked; the review waits for its answer.
        layout = QVBoxLayout(self)
        dialog_heading(
            self,
            layout,
            "Break down with AI",
            "Preview what is sent, then keep the steps you want.",
        )
        self.task = wrap_label("", px=TEXT_PX, medium=True)
        self.task.setObjectName("breakdownTask")
        self.task.setAccessibleName("Task")
        layout.addWidget(self.task)
        # What went wrong or what is needed, above the step and outside any scrolled field.
        self.status = wrap_label("", tone="warning")
        self.status.setObjectName("breakdownStatus")
        self.status.setAccessibleName("Break down with AI message")
        self.status.hide()
        layout.addWidget(self.status)
        self.pages = QStackedWidget()
        layout.addWidget(self.pages, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        self.close_button = outline_button("&Close", "breakdownCloseButton", mnemonic=True)
        self.close_button.clicked.connect(self.reject)
        row.addWidget(self.close_button)
        layout.addLayout(row)

        compose = QWidget()
        column = QVBoxLayout(compose)
        column.setContentsMargins(0, 0, 0, 0)  # Steps line up with the message and Close.
        column.addWidget(plain_label("Anything the steps should cover? (optional)"))
        self.instruction = QPlainTextEdit()
        self.instruction.setObjectName("breakdownInstruction")
        self.instruction.setAccessibleName("Instruction")
        self.instruction.setTabChangesFocus(True)
        self.instruction.setFixedHeight(84)
        column.addWidget(self.instruction)
        column.addWidget(plain_label(ALWAYS_SENT, tone="muted"))
        self.part_boxes: dict[BreakdownPart, QCheckBox] = {}
        for part in PART_ORDER:
            box = QCheckBox(PART_TEXT[part])
            box.setObjectName(f"breakdownPart_{part.value}")
            self.part_boxes[part] = box
            column.addWidget(box)
        self.preview_button = outline_button(
            "Pre&view what is sent", "breakdownPreviewButton", mnemonic=True
        )
        self.preview_button.clicked.connect(self._request_preview)
        column.addWidget(self.preview_button, 0, Qt.AlignmentFlag.AlignLeft)
        column.addStretch(1)
        self.pages.addWidget(compose)

        confirm = QWidget()
        column = QVBoxLayout(confirm)
        column.setContentsMargins(0, 0, 0, 0)
        self.preview_lines = wrap_label("")
        self.preview_lines.setObjectName("breakdownPreview")
        self.preview_lines.setAccessibleName("What is sent")
        self.consent = QCheckBox("I agree to send this to Groq to propose steps")
        self.consent.setObjectName("breakdownConsent")
        self.consent.toggled.connect(lambda _on: self._update_ask())
        # The disclosure can be longer than the dialog, so it scrolls; the consent box and
        # the buttons stay in view below it. The area takes no focus of its own.
        holder = QWidget()
        holder.setObjectName("breakdownPreviewHolder")
        held = QVBoxLayout(holder)
        held.setContentsMargins(0, 0, 8, 0)
        held.addWidget(self.preview_lines)
        held.addStretch(1)
        self.preview_area = QScrollArea()
        self.preview_area.setObjectName("breakdownPreviewArea")
        self.preview_area.setAccessibleName("What is sent")
        self.preview_area.setWidgetResizable(True)
        self.preview_area.setFrameShape(QFrame.Shape.NoFrame)
        self.preview_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.preview_area.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.preview_area.setWidget(holder)
        column.addWidget(self.preview_area, 1)
        column.addWidget(self.consent)
        row = QHBoxLayout()
        self.back_button = outline_button("&Back", "breakdownBackButton", mnemonic=True)
        self.ask_button = outline_button(
            "Ask &Groq", "breakdownAskButton", mnemonic=True, primary=True
        )
        self.back_button.clicked.connect(lambda: self._show(_COMPOSE))
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
        self.stop_button = outline_button("Cancel request", "breakdownCancelButton")
        self.stop_button.clicked.connect(self.cancel_requested.emit)
        column.addWidget(self.stop_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.pages.addWidget(working)

        review = QWidget()
        column = QVBoxLayout(review)
        column.setContentsMargins(0, 0, 0, 0)
        self.message = wrap_label("")
        self.message.setObjectName("breakdownMessage")
        column.addWidget(self.message)
        # All eight rows are made once and shown as needed, so no row leaves a layout.
        self.step_rows = [_StepRow(number) for number in range(1, MAX_PROPOSED_STEPS + 1)]
        for step_row in self.step_rows:
            step_row.box.toggled.connect(lambda _on: self._update_apply())
            step_row.field.textChanged.connect(lambda _text: self._update_apply())
            column.addWidget(step_row)
        self.slots = wrap_label("", tone="muted")
        self.slots.setObjectName("breakdownSlots")
        column.addWidget(self.slots)
        self.missing = wrap_label("", tone="muted")
        self.missing.setObjectName("breakdownMissing")
        self.missing.setAccessibleName("What Groq would need to know")
        column.addWidget(self.missing)
        column.addStretch(1)
        row = QHBoxLayout()
        self.apply_button = outline_button(
            "&Apply ticked steps", "breakdownApplyButton", mnemonic=True, primary=True
        )
        self.dismiss_button = outline_button("&Dismiss", "breakdownDismissButton", mnemonic=True)
        self.apply_button.clicked.connect(self._request_apply)
        self.dismiss_button.clicked.connect(self.reject)
        row.addWidget(self.apply_button)
        row.addWidget(self.dismiss_button)
        row.addStretch(1)
        column.addLayout(row)
        self.pages.addWidget(review)
        self._review_page = review
        self._show(_COMPOSE)

    def start_new(self, action: Action) -> None:
        """A fresh request for ``action``: no part is ticked, and only the parts the task
        has can be."""
        self.task.setText(action.title)
        self.instruction.setPlainText("")
        offered = available_parts(action)
        for part, box in self.part_boxes.items():
            box.setChecked(False)
            box.setVisible(part in offered)
        self._set_composing(True)
        self._set_applying(False)
        self.consent.setChecked(False)
        self._clear_steps()
        self._show(_COMPOSE)
        self._say("")
        self.instruction.setFocus()

    def _set_applying(self, applying: bool) -> None:
        """While Apply is saving, the review can't be changed, applied again or closed, so
        nothing turns the proposal down as it is being applied."""
        self._applying = applying
        self._review_page.setEnabled(not applying)

    def _clear_steps(self) -> None:
        self._shown = 0
        for step_row in self.step_rows:
            step_row.box.setChecked(False)
            step_row.field.clear()
            step_row.hide()
        self.missing.setText("")

    def _set_composing(self, editable: bool) -> None:
        """The instruction and its parts can change, or are held while a preview is prepared
        from them, so that what the preview lists and Ask sends is what the page shows."""
        self.instruction.setReadOnly(not editable)
        for box in self.part_boxes.values():
            box.setEnabled(editable)
        self.preview_button.setEnabled(editable)

    def has_unsaved_input(self) -> bool:
        """Whether closing now would lose something: a typed instruction, or steps under
        review."""
        return self.pages.currentIndex() == _REVIEW or bool(self.instruction.toPlainText().strip())

    def options(self) -> BreakdownOptions | str:
        """The owner's choices, or why they can't be sent yet."""
        text = self.instruction.toPlainText().strip()
        if len(text) > BREAKDOWN_INSTRUCTION_MAX_CHARS:
            return TOO_LONG
        parts = frozenset(
            part for part, box in self.part_boxes.items() if box.isChecked() and not box.isHidden()
        )
        return BreakdownOptions(instruction=text, parts=parts)

    def _request_preview(self) -> None:
        options = self.options()
        if isinstance(options, str):
            self._say(options)
            return
        self._say("")
        self._set_composing(False)  # Until the preview, or a problem, shows.
        self.preview_requested.emit(options)

    def show_preview(self, preview: BreakdownPreview) -> None:
        """What will be sent, part by part; first use also needs the ticked box."""
        self._first_use = preview.first_use
        self._set_composing(True)
        self.preview_lines.setText("\n\n".join(disclosure_lines(preview)))
        self.consent.setVisible(preview.first_use)
        self.consent.setChecked(False)
        self._say("")
        self._show(_CONFIRM)
        self._update_ask()

    def _update_ask(self) -> None:
        self.ask_button.setEnabled(not self._first_use or self.consent.isChecked())

    def _request_ask(self) -> None:
        if self.ask_button.isEnabled():
            self.ask_requested.emit(self.consent.isChecked())

    def show_working(self) -> None:
        self._say("")
        self._show(_WORKING)

    def show_proposal(self, proposal: BreakdownProposal) -> None:
        """The checked steps, all ticked, each in a field the owner can change."""
        self._clear_steps()
        self._free = proposal.free_slots
        self._shown = len(proposal.steps)
        for step_row, step in zip(self.step_rows, proposal.steps, strict=False):
            step_row.field.setText(step)
            step_row.field.setCursorPosition(0)
            step_row.box.setChecked(True)
            step_row.show()
        count = len(proposal.steps)
        self.message.setText(
            ("Groq proposed 1 step." if count == 1 else f"Groq proposed {count} steps.")
            + " Untick or edit them, then apply. They are added after the task's own steps."
        )
        self.missing.setText(
            "Groq would need to know: " + "; ".join(proposal.missing_context)
            if proposal.missing_context
            else ""
        )
        self.missing.setVisible(bool(proposal.missing_context))
        self._say("")
        self._show(_REVIEW)
        self._update_apply()
        self.step_rows[0].field.setFocus()

    def kept(self) -> tuple[KeptStep, ...]:
        """The ticked steps with words, as the owner left them."""
        return tuple(
            KeptStep(index=index, text=words)
            for index, step_row in enumerate(self.step_rows[: self._shown])
            if step_row.box.isChecked() and (words := step_row.field.text().strip())
        )

    def _ticked(self) -> int:
        return sum(step_row.box.isChecked() for step_row in self.step_rows[: self._shown])

    def _update_apply(self) -> None:
        ticked = self._ticked()
        self.slots.setText(slots_text(ticked, self._free))
        self.apply_button.setEnabled(0 < ticked <= self._free and len(self.kept()) == ticked)

    def _request_apply(self) -> None:
        ticked = self._ticked()
        kept = self.kept()
        if not ticked:
            self._say(NOTHING_KEPT)
        elif len(kept) != ticked:
            self._say(EMPTY_STEP)
        elif ticked <= self._free:
            self._say("")
            self._set_applying(True)
            self.apply_requested.emit(kept)

    def show_problem(self, message: str) -> None:
        """Back to the instruction, with what went wrong in fixed words."""
        self._set_composing(True)
        self._show(_COMPOSE)
        self._say(message)

    def keep_reviewing(self, message: str) -> None:
        """Applying failed: stay on the proposed steps and say why."""
        self._set_applying(False)
        self._say(message)

    def finish_applied(self) -> None:
        """The steps were applied: close without anything left to turn down."""
        self._set_applying(False)
        self.instruction.setPlainText("")
        self._clear_steps()
        self._show(_COMPOSE)
        self.accept()

    def reject(self) -> None:
        """Close, Esc or Dismiss; closing while Groq is being asked stops the request too.
        While Apply is saving, the dialog stays until it has its answer."""
        if self._applying:
            return
        if self.pages.currentIndex() == _WORKING:
            self.cancel_requested.emit()
        super().reject()

    def _say(self, message: str) -> None:
        """Show what needs attention above the step, or hide the line when there's nothing."""
        self.status.setText(message)
        self.status.setVisible(bool(message))

    def _show(self, page: int) -> None:
        self.pages.setCurrentIndex(page)
        # Close on the first two steps; asking has Cancel request and the review has Dismiss.
        self.close_button.setVisible(page in (_COMPOSE, _CONFIRM))
