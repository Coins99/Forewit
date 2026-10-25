"""The selected action: its title, state, dates, plan, notes, thread activity, proposals and
sources, with the buttons that act on it.

The buttons and the field that adds a step are made once and stay, so the window's Tab
order and the Actions panel's names for them never go stale; only the text between them,
with an open task's step check boxes, is rebuilt for each action. Text the mail, the AI or
the owner wrote appears only in plain-text wrapping labels, never in a tooltip, and the
pane never opens a URL itself.
"""

from collections.abc import Sequence
from datetime import date, datetime
from typing import Final
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QMouseEvent, QResizeEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.actions import (
    MAX_ACTION_STEPS,
    Action,
    ActionActivity,
    ActionActivityKind,
    ActionFilter,
    ActionSource,
    ActionStatus,
    ActionStep,
)
from mailbrief.ui.action_text import (
    ActionDetails,
    action_details,
    completed_text,
    follow_up_text,
    followed_up_at,
    gmail_source,
    person_text,
    priority_text,
    since_text,
    tags_text,
    target_text,
)
from mailbrief.ui.brief_detail import DETAIL_MAX_WIDTH
from mailbrief.ui.brief_list import NO_SUBJECT
from mailbrief.ui.deadline_text import day_text, moment_text
from mailbrief.ui.hairline import HairlineDivider
from mailbrief.ui.labels import outline_button, plain_label, wrap_label
from mailbrief.ui.theme import SMALL_PX, TEXT_PX
from mailbrief.ui.who_text import UNNAMED_WAITING, in_sentence

EMPTY_TEXT: Final = {
    ActionFilter.OPEN: "No open actions. Create a task or accept a suggestion in a brief.",
    ActionFilter.WAITING: "Nothing you're waiting for.",
    ActionFilter.COMPLETED: "No completed actions yet.",
}
DONE_MARK: Final = "✓ "
OPEN_MARK: Final = "○ "
NEW_STEP_TEXT: Final = "Add a step"
STEP_PX: Final = 11


def state_text(action: Action, details: ActionDetails) -> str:
    """The state line: "Yours · open", "Waiting for someone · open", or with the person the
    owner named, "Yours, for Sam · open" and "Waiting on Alex · open", each with " · carried
    over" while that applies; or "Completed Tue Oct 6"."""
    completed = completed_text(details)
    if action.status is ActionStatus.COMPLETED and completed is not None:
        return completed
    state = f"{owner_text(action, mine='Yours')} · {action.status.value}"
    return f"{state} · carried over" if details.carried_over else state


def owner_text(action: Action, *, mine: str) -> str:
    """Whose the action is, with the person the owner named (B08): ``mine``, or "``mine``,
    for Sam"; "Waiting for someone", or "Waiting on Alex"."""
    who = person_text(action, unnamed=UNNAMED_WAITING)
    if action.waiting and who is not None:
        return who
    return mine if who is None else f"{mine}, {in_sentence(who)}"


def follow_up_prompt(action: Action, details: ActionDetails) -> tuple[str, str] | None:
    """What the callout of an action the owner waits for says, as a heading and a line, or
    None while nothing calls for it (B08): a reply from others in its thread, then a deadline
    that has passed or a follow-up date that has come, unless the owner has sent or marked
    seen a message in its threads since then (followed_up_at)."""
    if not details.waiting:
        return None
    thread = action.thread
    if details.new_messages and thread is not None and thread.latest_sender is not None:
        return (
            f"{thread.latest_sender} replied",
            "If it's what you were waiting for, mark it received.",
        )
    target = details.target
    followed, due_at = followed_up_at(action), action.due_at_utc()
    late = details.overdue and (followed is None or due_at is None or followed < due_at)
    planned = (
        target is not None
        and target <= details.today
        and (details.followed_up_on is None or details.followed_up_on < target)
    )
    if not (late or planned):
        return None
    heading = "Time to follow up" if action.person is None else f"Follow up with {action.person}"
    if late:
        return heading, "The deadline has passed. Draft a short check-in."
    if target == details.today:
        return heading, "It's your follow-up date. Draft a short check-in."
    return heading, f"Your follow-up date was {follow_up_text(details, reason=False)}."


def proposals_text(count: int) -> str:
    if count == 1:
        return "A follow-up reply proposes an update to this action."
    return f"{count} follow-up replies propose updates to this action."


def source_text(source: ActionSource, *, today: date, zone: ZoneInfo) -> str:
    """The sender's address and the received date in ``zone``, and whether the email has
    left local mail."""
    received = day_text(source.received_at_utc.astimezone(zone).date(), today=today)
    text = f"{source.sender_address} · Received {received}"
    return text if source.available else f"{text} · No longer in local mail"


class _Section(QWidget):
    """A column of labels that is replaced as a whole."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.setObjectName(name)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(8)

    def set_widgets(self, widgets: Sequence[QWidget | int]) -> None:
        """Show ``widgets`` in order (an int is extra space above the next); hidden when
        there are none."""
        while (item := self._layout.takeAt(0)) is not None:
            old = item.widget()
            if old is not None:
                old.setParent(None)  # Gone now, not at the next turn of the event loop.
        for widget in widgets:
            if isinstance(widget, int):
                self._layout.addSpacing(widget)
            else:
                self._layout.addWidget(widget)
        self.setVisible(any(not isinstance(widget, int) for widget in widgets))


def _button_row(name: str, *buttons: QPushButton) -> QWidget:
    row = QWidget()
    row.setObjectName(name)
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    for button in buttons:
        layout.addWidget(button)
    layout.addStretch(1)  # Buttons keep their own width.
    return row


def _heading(text: str) -> QLabel:
    """A section's caption: app text, muted."""
    label = plain_label(text, tone="muted", px=9)
    label.setObjectName("actionSectionHeading")
    return label


_SECTION_GAP: Final = 6


class StepRow(QWidget):
    """One step of an open task's plan (B04): a check box beside the step's text, muted once
    it is done. The box has no text of its own, so the step's words stay plain and wrap, and
    an "&" in them never becomes a mnemonic; the step is its accessible name. A click on the
    text ticks the box too, as a check box's own label would."""

    def __init__(self, step: ActionStep) -> None:
        super().__init__()
        self.setObjectName("actionStep")
        self.step_id = step.step_id
        self.box = QCheckBox()
        self.box.setObjectName("actionStepBox")
        self.label = wrap_label(px=STEP_PX)
        self.label.setObjectName("actionStepText")
        self.show_step(step)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(self.box, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.label, 1)
        # The text's first line sits level with the box; later lines run on below it.
        rise = self.box.sizeHint().height() - self.label.minimumSizeHint().height()
        if rise > 0:
            self.label.setContentsMargins(0, rise // 2, 0, 0)

    def show_step(self, step: ActionStep) -> None:
        """Show ``step`` (this row's, as saved now): its words, and whether it is done."""
        self.setAccessibleName(step.text)
        self.box.setAccessibleName(step.text)
        self.box.setChecked(step.done)
        self.label.setText(step.text)
        tone = "muted" if step.done else None
        if self.label.property("tone") != tone:
            self.label.setProperty("tone", tone)
            self.label.style().unpolish(self.label)
            self.label.style().polish(self.label)

    def _on_text(self, event: QMouseEvent) -> bool:
        return event.button() == Qt.MouseButton.LeftButton and self.label.geometry().contains(
            event.position().toPoint()
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._on_text(event):
            event.accept()  # So the release comes here too.
        else:
            super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._on_text(event) and self.box.isEnabled():
            self.box.click()
            event.accept()
        else:
            super().mouseReleaseEvent(event)


class ActionDetailPane(QScrollArea):
    """Shows one action, or the view's empty state. The Actions panel connects its buttons;
    the plan of an open task emits ``step_toggled(step_id, done)`` when the owner ticks or
    unticks a step and ``step_added(text)`` when they add one (B04). The window decides
    whether they can be saved now, and puts back a tick it can't save (put_back_step)."""

    step_toggled = Signal(object, bool)
    step_added = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("actionDetail")
        self.setAccessibleName("Selected action")
        # Tab goes from the list straight to the buttons; a click on the pane still lets
        # the arrow keys scroll it.
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("actionDetailContent")
        # A readable measure on wide windows; the scroll area keeps it at the left.
        content.setMaximumWidth(DETAIL_MAX_WIDTH)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(44, 30, 44, 32)
        layout.setSpacing(16)
        # K: the window's Sync and review already takes S.
        self.seen_button = outline_button("Mar&k seen", "seenButton", mnemonic=True)
        self.seen_button.setToolTip("Clear the new-in-thread and you-replied notes.")
        # R: the other letters of "Proposals" are taken by Complete, Delete, Open source and
        # the window's Sync and review.
        self.proposals_button = outline_button("P&roposals…", "proposalsButton", mnemonic=True)
        self.proposals_button.setToolTip("Review follow-up replies that propose an update.")
        self.source_button = outline_button(
            "Open s&ource", "sourceButton", mnemonic=True, icon_name="external-link"
        )
        self.edit_button = outline_button("&Edit…", "editButton", mnemonic=True)
        self.complete_button = outline_button(
            "Com&plete", "completeButton", mnemonic=True, primary=True, icon_name="check"
        )
        self.delete_button = outline_button(
            "De&lete", "deleteButton", mnemonic=True, icon_name="trash"
        )
        # I: the other letters of "Skip" are taken by the window's Sync, Complete and
        # Complete's p.
        self.skip_button = outline_button("Sk&ip", "skipButton", mnemonic=True)
        self.skip_button.setToolTip("Skip this one; the next repeat comes as usual.")
        self.skip_button.setProperty("variant", "ghost")
        self._can_skip = False
        self.edit_button.setProperty("variant", "ghost")
        self.delete_button.setProperty("variant", "ghost")
        self.draft_button = outline_button(
            "Dra&ft reply", "draftButton", mnemonic=True, icon_name="pencil"
        )
        self.empty = wrap_label("", tone="muted", px=TEXT_PX)
        self.empty.setObjectName("actionEmpty")
        self._top = _Section("actionSummary")
        self._plan_steps = _Section("actionPlan")
        self._plan_steps.setAccessibleName("Plan steps")
        self._plan_heading: QLabel | None = None
        # No length limit here: a step too long is refused with the reason, as in the editor,
        # rather than cut short.
        self.new_step = QLineEdit()
        self.new_step.setObjectName("newStepField")
        self.new_step.setAccessibleName(NEW_STEP_TEXT)
        self.new_step.setPlaceholderText(NEW_STEP_TEXT)
        self.new_step.setToolTip("Type a step and press Return to add it to the plan.")
        self.new_step.returnPressed.connect(self._add_step)
        self._plan_box = QWidget()
        self._plan_box.setObjectName("actionPlanBox")
        self._plan_box.setAccessibleName("Plan")
        plan_layout = QVBoxLayout(self._plan_box)
        plan_layout.setContentsMargins(0, 0, 0, 0)
        plan_layout.setSpacing(8)
        plan_layout.addWidget(self._plan_steps)
        plan_layout.addWidget(self.new_step)
        self._step_rows: list[StepRow] = []
        self._activity_summary = _Section("actionActivitySummary")
        self._seen_row = _button_row("seenRow", self.seen_button)
        self._proposals = _Section("actionProposals")
        self._proposals_row = _button_row("proposalsRow", self.proposals_button)
        self._sources = _Section("actionSources")
        self._source_row = _button_row("sourceRow", self.source_button)
        self._later_sources = _Section("actionLaterSources")
        self._footer = QWidget()
        self._footer.setObjectName("actionFooter")
        self._footer_layout = QGridLayout(self._footer)
        self._footer_layout.setContentsMargins(0, 0, 0, 0)
        self._footer_layout.setSpacing(8)
        # Skip lives in the footer from the start, hidden, so showing it never shows it as a
        # window of its own and it shares the window's Tab order.
        self.skip_button.setParent(self._footer)
        self._arrange_footer(4)
        # Created in display order, which is also the Tab order of the buttons.
        for widget in (
            self.empty,
            self._top,
            self._footer,
            self._plan_box,
            self._activity_summary,
            self._seen_row,
            self._proposals,
            self._proposals_row,
            self._sources,
            self._source_row,
            self._later_sources,
        ):
            layout.addWidget(widget)
        layout.addStretch(1)
        self.setWidget(content)
        self.title: QWidget | None = None
        self._shown: str | None = None  # The public ID of the action shown.
        self.show_empty(ActionFilter.OPEN)

    def _arrange_footer(self, columns: int) -> None:
        for button in self.all_footer_buttons():
            self._footer_layout.removeWidget(button)
        self.skip_button.setVisible(self._can_skip)
        buttons = self._footer_buttons()
        for number, button in enumerate(buttons):
            self._footer_layout.addWidget(
                button, number // columns, number % columns, Qt.AlignmentFlag.AlignLeft
            )
        for column in range(6):
            self._footer_layout.setColumnStretch(column, 1 if column == columns else 0)
        for first, second in zip(buttons, buttons[1:], strict=False):
            QWidget.setTabOrder(first, second)

    def _link_below_footer(self) -> None:
        """Tab goes from the footer's last button (Delete) to the plan's boxes and the field
        that adds a step, then Mark seen, Proposals and Open source. The whole run is linked
        each time the boxes are made: Qt skips a link that already holds, so a partial one
        could leave other widgets between them."""
        chain = [self.delete_button, *self._below_footer()]
        for first, second in zip(chain, chain[1:], strict=False):
            QWidget.setTabOrder(first, second)

    def _below_footer(self) -> list[QWidget]:
        """The controls after the footer, in display order: the plan's boxes and field, then
        Mark seen, Proposals and Open source."""
        return [
            *(row.box for row in self._step_rows),
            self.new_step,
            self.seen_button,
            self.proposals_button,
            self.source_button,
        ]

    def focus_order(self) -> list[QWidget]:
        """The pane's controls in display order, which is their Tab order: the footer's
        buttons as laid out, then the controls below it."""
        return [*self._footer_buttons(), *self._below_footer()]

    def step_rows(self) -> list[StepRow]:
        """The open task's steps shown, in order; none for a closed task."""
        return list(self._step_rows)

    def put_back_step(self, step_id: int, done: bool) -> None:
        """Show the step as it was before a tick to ``done`` that nothing saved. The box is set
        back, never remade, so this is safe from within its own click."""
        for row in self._step_rows:
            if row.step_id == step_id:
                row.box.setChecked(not done)

    def _add_step(self) -> None:
        text = self.new_step.text().strip()
        if text:
            self.step_added.emit(text)

    def clear_added_step(self, public_id: str, text: str) -> None:
        """Empty the field once its step is added to the action ``public_id``, unless another
        action shows now or the field holds something else."""
        if self._shown == public_id and self.new_step.text().strip() == text:
            self.new_step.clear()

    def all_footer_buttons(self) -> list[QPushButton]:
        """The footer's buttons, Skip included whether it is shown or not."""
        leading = [self.complete_button, self.skip_button, self.draft_button]
        if self.draft_button.property("variant") == "primary":
            leading = [self.draft_button, self.complete_button, self.skip_button]
        return [*leading, self.edit_button, self.delete_button]

    def _footer_buttons(self) -> list[QPushButton]:
        """The footer's buttons as laid out: Skip only for a task that can be skipped."""
        return [
            button
            for button in self.all_footer_buttons()
            if button is not self.skip_button or self._can_skip
        ]

    def set_can_skip(self, can_skip: bool) -> None:
        """Offer Skip, for an open occurrence of a task that still repeats."""
        if can_skip != self._can_skip:
            self._can_skip = can_skip
            self.arrange_controls()

    def arrange_controls(self) -> None:
        buttons = self._footer_buttons()
        width = self.viewport().width()
        if len(buttons) > 4:  # Skip too: one row while every button's own width fits.
            content = self.widget()
            margins = content.contentsMargins() if content is not None else None
            room = min(width, DETAIL_MAX_WIDTH) - (
                0 if margins is None else margins.left() + margins.right()
            )
            needed = sum(button.sizeHint().width() for button in buttons) + 8 * (len(buttons) - 1)
            self._arrange_footer(len(buttons) if needed <= room else 2)
        else:
            self._arrange_footer(4 if width >= 480 else 2)
        self._link_below_footer()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self.arrange_controls()

    def all_buttons(self) -> list[QPushButton]:
        """Every button, shown or not, in display order."""
        return [
            *self.all_footer_buttons(),
            self.seen_button,
            self.proposals_button,
            self.source_button,
        ]

    def buttons(self) -> list[QPushButton]:
        """The buttons shown, in display order."""
        content = self.widget()
        if content is None:
            return []
        return [button for button in self.all_buttons() if button.isVisibleTo(content)]

    def show_empty(self, view: ActionFilter) -> None:
        self._shown = None
        self.title = None
        self.empty.setText(EMPTY_TEXT[view])
        self.empty.show()
        self._release_focus()
        self._step_rows = []
        self._plan_heading = None
        self.new_step.clear()
        for section in (
            self._top,
            self._plan_steps,
            self._activity_summary,
            self._proposals,
            self._sources,
            self._later_sources,
        ):
            section.set_widgets(())
        for row in (
            self._plan_box,
            self._seen_row,
            self._proposals_row,
            self._source_row,
            self._footer,
        ):
            row.hide()

    def show_action(self, action: Action, *, today: date, zone: ZoneInfo, now: datetime) -> None:
        """Show ``action``; another action than the one shown starts at the top."""
        details = action_details(action, today=today, zone=zone, now=now)
        self.empty.hide()
        self.title = wrap_label(action.title, px=23, medium=True)
        self.title.setObjectName("actionTitle")
        state = wrap_label(state_text(action, details), tone="secondary", px=9)
        state.setObjectName("actionState")
        top: list[QWidget | int] = [state, self.title, *self._dates(details)]
        if action.project is not None:
            project = action.project
            organization = "Project: " + project.name
            if project.area_name:
                organization += " · " + project.area_name
            if project.archived_at_utc:
                organization += " · Archived"
            label = wrap_label(organization, tone="secondary", px=TEXT_PX)
            label.setObjectName("actionProject")
            top.append(label)
        labels = [text for text in (priority_text(action.priority), tags_text(action.tags)) if text]
        if labels:
            # Tags are the owner's text: plain, and wrapped inside a long unbroken one.
            label = wrap_label(" · ".join(labels), tone="secondary", px=TEXT_PX)
            label.setObjectName("actionPriorityTags")
            top.append(label)
        if action.notes.strip():
            top += [12, wrap_label(action.notes, px=11)]
        facts = QWidget()
        facts.setObjectName("actionFacts")
        facts.setAccessibleName("Action details")
        facts_layout = QVBoxLayout(facts)
        facts_layout.setContentsMargins(0, 12, 0, 8)
        facts_layout.setSpacing(14)
        facts_layout.addWidget(HairlineDivider())
        columns = QHBoxLayout()
        source = action.sources[0] if action.sources else None
        values = (
            (
                ("WAITING ON", action.person or "Not set"),
                ("WAITING SINCE", since_text(details) or "Not set"),
                ("FOLLOW-UP", follow_up_text(details) or "No date set"),
            )
            if details.waiting
            else (
                ("OWNER", owner_text(action, mine="You")),
                ("PROJECT", action.project.name if action.project else "Unassigned"),
                ("SOURCE EMAIL", (source.subject or NO_SUBJECT) if source else "Manual task"),
            )
        )
        for caption, value in values:
            column = QVBoxLayout()
            column.setSpacing(6)
            column.addWidget(plain_label(caption, tone="muted", px=8))
            column.addWidget(wrap_label(value, px=10))
            column.addStretch(1)
            columns.addLayout(column, 1)
        facts_layout.addLayout(columns)
        facts_layout.addWidget(HairlineDivider())
        top.append(facts)
        prompt = follow_up_prompt(action, details)
        if prompt is not None:
            heading, line = prompt
            callout = QWidget()
            callout.setObjectName("waitingFollowUp")
            callout.setAccessibleName("Follow-up")
            callout.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            copy = QVBoxLayout(callout)
            copy.setContentsMargins(14, 12, 14, 12)
            copy.setSpacing(5)
            # A person's name or a sender's: plain, and wrapped inside a long unbroken one.
            title = wrap_label(heading, px=11, medium=True)
            title.setObjectName("waitingFollowUpHeading")
            title.setAccessibleName(heading)
            copy.addWidget(title)
            note = wrap_label(line, tone="muted", px=9)
            note.setObjectName("waitingFollowUpLine")
            note.setAccessibleName(line)
            copy.addWidget(note)
            top.append(callout)
        summary = top
        top = []
        activity = [text for text in (details.activity, details.replied) if text is not None]
        if activity:
            top += [_SECTION_GAP, _heading("Thread")]
            top += [wrap_label(_sentence(text), px=TEXT_PX) for text in activity]
        if action.activity:
            top += [_SECTION_GAP, _heading("Activity")]
            # Edits in a row, as ticking steps makes, read as one line with their count.
            entries: list[tuple[ActionActivity, int]] = []
            for event in reversed(action.activity[-20:]):
                if entries and _plain_edit(event) and _plain_edit(entries[-1][0]):
                    entries[-1] = (entries[-1][0], entries[-1][1] + 1)
                else:
                    entries.append((event, 1))
            for event, edits in entries:
                at = moment_text(event.occurred_at_utc, zone, today=today)
                name = event.kind.value.replace("_", " ").capitalize()
                detail = f" {edits} times" if edits > 1 else ""
                if event.undo_of_revision is not None:
                    reversed_event = next(
                        (
                            item
                            for item in action.activity
                            if item.revision == event.undo_of_revision
                        ),
                        None,
                    )
                    if reversed_event is not None:
                        reversed_name = {
                            "created": "creation",
                            "completed": "completion",
                            "skipped": "skip",
                            "reopened": "reopening",
                            "deleted": "deletion",
                            "source_added": "email addition",
                            "suggestion_accepted": "suggestion acceptance",
                            "proposal_applied": "proposal",
                        }.get(reversed_event.kind.value, "change")
                        name = f"Undid {reversed_name}"
                legacy = " · Migrated record" if event.legacy else ""
                label = wrap_label(f"{name}{detail} · {at}{legacy}", tone="secondary", px=SMALL_PX)
                label.setObjectName("actionActivityEntry")
                top.append(label)
        self._top.set_widgets(summary)
        if action.public_id != self._shown:
            self.new_step.clear()  # A step typed for another action isn't this one's.
        self._show_plan(action, details)
        self._activity_summary.set_widgets(top)
        self._seen_row.setVisible(bool(activity))
        count = len(details.proposals)
        self._proposals.set_widgets(
            [_SECTION_GAP, wrap_label(proposals_text(count), px=TEXT_PX)] if count else ()
        )
        self._proposals_row.setVisible(bool(count))
        self._show_sources(action, today=today, zone=zone)
        self._footer.show()
        if action.public_id != self._shown:
            self.verticalScrollBar().setValue(0)
        self._shown = action.public_id

    def _dates(self, details: ActionDetails) -> list[QWidget | int]:
        widgets: list[QWidget | int] = []
        if details.due is not None:
            text = (
                f"Due {details.due}"
                + (" · Set by you" if details.due_by_owner else "")
                + (" · Overdue" if details.overdue else "")
            )
            due = wrap_label(text, tone="warning" if details.overdue else "secondary", px=9)
            due.setObjectName("actionDeadline")
            badge = QWidget()
            row = QHBoxLayout(badge)
            row.setContentsMargins(0, 4, 0, 6)
            row.addWidget(due)
            row.addStretch(1)
            widgets.append(badge)
        target = target_text(details)
        if target is not None:
            label = wrap_label(target, tone="secondary", px=10)
            label.setObjectName("actionTarget")
            widgets.append(label)
        if details.estimate is not None:
            label = wrap_label(details.estimate, tone="secondary", px=TEXT_PX)
            label.setObjectName("actionEstimate")
            widgets.append(label)
        for line, name in ((details.repeat, "actionRepeat"), (details.previous, "actionPrevious")):
            if line is not None:
                label = wrap_label(line, tone="secondary", px=TEXT_PX)
                label.setObjectName(name)
                widgets.append(label)
        return widgets

    def _release_focus(self) -> int | None:
        """The step whose box has the focus, or None. The pane holds the focus meanwhile, so
        replacing the boxes doesn't pass it along the Tab chain."""
        for row in self._step_rows:
            if row.box.hasFocus():
                self.setFocus(Qt.FocusReason.OtherFocusReason)
                return row.step_id
        return None

    def _show_plan(self, action: Action, details: ActionDetails) -> None:
        """The plan (B04): an open task's steps as check boxes, with the field that adds one
        while it has room; a closed task's as text, "✓" when done and "○" when not; nothing
        for a closed task without steps. The same steps shown again, as after a tick, are
        updated in place, so the focus and the Tab order stay where they are."""
        is_open = action.status is ActionStatus.OPEN
        steps = sorted(action.steps, key=lambda step: step.position)
        heading = f"Plan · {details.steps_done} of {details.steps} done" if steps else "Plan"
        same = [row.step_id for row in self._step_rows] == [step.step_id for step in steps]
        if is_open and steps and same and self._plan_heading is not None:
            self._plan_heading.setText(heading)
            for row, step in zip(self._step_rows, steps, strict=True):
                row.show_step(step)
        else:
            self._rebuild_plan(steps, heading if steps or is_open else None, is_open=is_open)
        self.new_step.setVisible(is_open and len(steps) < MAX_ACTION_STEPS)
        self._plan_box.setVisible(self._plan_heading is not None)

    def _rebuild_plan(self, steps: list[ActionStep], heading: str | None, *, is_open: bool) -> None:
        """Make the plan's widgets anew: under ``heading``, an open task's rows or a closed
        task's lines. A box that had the focus gives it to its step's new box."""
        focused = self._release_focus()
        widgets: list[QWidget | int] = []
        self._plan_heading = None if heading is None else _heading(heading)
        if self._plan_heading is not None:
            widgets = [_SECTION_GAP, self._plan_heading]
        if is_open:
            self._step_rows = [StepRow(step) for step in steps]
            for row in self._step_rows:
                row.box.clicked.connect(
                    lambda done, step_id=row.step_id: self.step_toggled.emit(step_id, done)
                )
            widgets += self._step_rows
        else:
            self._step_rows = []
            widgets += [
                wrap_label(
                    (DONE_MARK if step.done else OPEN_MARK) + step.text,
                    tone="muted" if step.done else None,
                    px=STEP_PX,
                )
                for step in steps
            ]
        self._plan_steps.set_widgets(widgets)
        self._link_below_footer()
        for row in self._step_rows:
            if row.step_id == focused:
                row.box.setFocus(Qt.FocusReason.OtherFocusReason)

    def _show_sources(self, action: Action, *, today: date, zone: ZoneInfo) -> None:
        """Each source; Open source follows the first that opens Gmail."""
        sources = action.sources
        link = gmail_source(action)
        split = next(
            (number + 1 for number, source in enumerate(sources) if str(source.web_link) == link),
            len(sources),
        )
        first: list[QWidget | int] = []
        if sources:
            first = [_SECTION_GAP, _heading("Source" if len(sources) == 1 else "Sources")]
        first += [
            widget for source in sources[:split] for widget in self._source(source, today, zone)
        ]
        self._sources.set_widgets(first)
        self._source_row.setVisible(link is not None)
        self._later_sources.set_widgets(
            [widget for source in sources[split:] for widget in self._source(source, today, zone)]
        )

    @staticmethod
    def _source(source: ActionSource, today: date, zone: ZoneInfo) -> list[QWidget | int]:
        subject = wrap_label(source.subject or NO_SUBJECT, px=TEXT_PX)
        subject.setObjectName("sourceSubject")
        meta = wrap_label(
            source_text(source, today=today, zone=zone), tone="secondary", px=SMALL_PX
        )
        meta.setObjectName("sourceMeta")
        return [subject, meta]


def _plain_edit(event: ActionActivity) -> bool:
    """Whether ``event`` is an ordinary edit, which may share a line with the edits beside it."""
    return (
        event.kind is ActionActivityKind.EDITED
        and event.undo_of_revision is None
        and not event.legacy
    )


def _sentence(text: str) -> str:
    """``text`` with a capital first letter, as a line of its own."""
    return text[:1].upper() + text[1:]
