"""My focus: explicit daily selection, current task state and keyboard ordering."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from PySide6.QtCore import QDate, Qt, QTimer, Signal
from PySide6.QtGui import QHideEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDateEdit,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLayout,
    QLineEdit,
    QListWidgetItem,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.actions import Action, ActionStatus
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.day_plans import DayPlan, DayPlanItem
from mailbrief.domain.events import ActionMarker
from mailbrief.domain.today import AttentionItem, DaySummary
from mailbrief.services.today import attention_reason
from mailbrief.ui.brief_list import ROW_ROLE
from mailbrief.ui.calendar_view import day_rows
from mailbrief.ui.date_picker import calendar_popup
from mailbrief.ui.dialog_chrome import dialog_heading
from mailbrief.ui.hairline import HairlineFrame
from mailbrief.ui.labels import ElidedLabel, outline_button, wrap_label
from mailbrief.ui.lists import ActivatingList, clear_list
from mailbrief.ui.theme import CARD_RADIUS
from mailbrief.ui.today_view import DayRow, DayRowDelegate, agenda_row, attention_row

_ITEM = Qt.ItemDataRole.UserRole


class FocusPanel(HairlineFrame):
    change_requested = Signal(str, object, object)
    open_requested = Signal(object)
    plan_requested = Signal()
    agenda_requested = Signal()
    attention_requested = Signal()

    def __init__(self, *, compact: bool = True, flush: bool = False) -> None:
        # A ``flush`` panel fills the top of the page sheet edge to edge (Today), above a
        # straight edge: it is a plain band, and the sheet's mask rounds its top corners
        # exactly as it rounds every page's, so they follow the sheet's outer edge.
        super().__init__(radius=0 if flush else CARD_RADIUS)
        self.setObjectName("focusPreview" if compact else "focusFull")
        self.setAccessibleName("My focus")
        self.compact = compact
        self.plan: DayPlan | None = None
        self._busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(6)
        header = QHBoxLayout()
        self.heading = wrap_label("My focus", px=17, medium=True)
        self.heading.setObjectName("focusHeading")
        self.heading.setAccessibleName("My focus")
        header.addWidget(self.heading, 1)
        self.plan_button = outline_button("Plan today", "focusPlanToday")
        self.plan_button.setAccessibleName("Choose and organize today's focus")
        header.addWidget(self.plan_button)
        self.plan_button.setVisible(compact)
        layout.addLayout(header)
        self.workload = wrap_label(
            "Choose work for today. Task dates stay unchanged.", tone="muted"
        )
        self.workload.setObjectName("focusWorkload")
        self.workload.setAccessibleName("Known selected workload")
        layout.addWidget(self.workload)
        # Today's free time from now on, and how far over the day is (ADR 0030).
        self.capacity = wrap_label("", tone="muted")
        self.capacity.setObjectName("focusCapacity")
        self.capacity.setAccessibleName("Free time today")
        self.capacity.hide()
        layout.addWidget(self.capacity)
        self.list = ActivatingList()
        self.list.setObjectName("focusList" if compact else "focusFullList")
        self.list.setAccessibleName("Selected work, in order")
        self.list.setItemDelegate(DayRowDelegate(self.list))
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setUniformItemSizes(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        if compact:
            self.list.setMaximumHeight(60)
        layout.addWidget(self.list, 1)
        buttons = QHBoxLayout()
        self.open_button = outline_button("Open", "focusOpen")
        self.complete_button = outline_button("Complete", "focusComplete")
        self.remove_button = outline_button("Remove", "focusRemove")
        self.remove_button.setToolTip("Remove this selection; keep the task and its dates.")
        self.up_button = outline_button("Move up", "focusUp")
        self.down_button = outline_button("Move down", "focusDown")
        for button in (self.open_button, self.complete_button, self.remove_button):
            button.setAccessibleName(button.text())
            buttons.addWidget(button)
        layout.addLayout(buttons)
        if not compact:
            order = QHBoxLayout()
            for button in (self.up_button, self.down_button):
                button.setAccessibleName(button.text())
                order.addWidget(button)
            layout.addLayout(order)
        else:
            # Hidden controls still own the same actions for keyboard shortcuts.
            for button in (self.up_button, self.down_button):
                button.setParent(self)
                button.setAccessibleName(button.text())
                button.hide()
        self.agenda_preview = QWidget()
        self.agenda_preview.setObjectName("focusAgendaPreview")
        self.agenda_preview.setAccessibleName("Today's agenda preview")
        agenda_layout = QHBoxLayout(self.agenda_preview)
        agenda_layout.setContentsMargins(0, 0, 0, 0)
        agenda_layout.setSpacing(6)
        agenda_rows = QVBoxLayout()
        agenda_rows.setSpacing(2)
        self.agenda = ElidedLabel()
        self.agenda.setObjectName("focusAgenda")
        self.agenda_next = ElidedLabel()
        self.agenda_next.setObjectName("focusAgendaNext")
        for label in (self.agenda, self.agenda_next):
            agenda_rows.addWidget(label)
        agenda_layout.addLayout(agenda_rows, 1)
        self.agenda_button = outline_button("Agenda", "focusAgendaOpen")
        self.agenda_button.setAccessibleName("Open today's full agenda")
        agenda_layout.addWidget(self.agenda_button)
        self.agenda_preview.setVisible(compact)
        layout.addWidget(self.agenda_preview)
        self.attention_button = outline_button("Other commitments", "focusAttention")
        self.attention_button.setAccessibleName("Other commitments needing attention")
        self.attention_button.setVisible(compact)
        layout.addWidget(self.attention_button)
        self.status = wrap_label("", tone="muted")
        self.status.setObjectName("focusStatus")
        layout.addWidget(self.status)
        self.status.hide()
        self.plan_button.clicked.connect(self.plan_requested)
        self.attention_button.clicked.connect(self.attention_requested)
        self.agenda_button.clicked.connect(self.agenda_requested)
        self.list.currentRowChanged.connect(self._buttons)
        self.list.itemActivated.connect(lambda _item: self._open())
        self.open_button.clicked.connect(self._open)
        self.complete_button.clicked.connect(self._complete)
        self.remove_button.clicked.connect(lambda: self._change("remove"))
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self._shortcuts: list[QShortcut] = []
        for key, delta in (("Alt+Up", -1), ("Alt+Down", 1)):
            shortcut = QShortcut(QKeySequence(key), self.list)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(lambda step=delta: self._move(step))
            self._shortcuts.append(shortcut)
        self._buttons()

    def selected(self) -> DayPlanItem | None:
        item = self.list.currentItem()
        value = None if item is None else item.data(_ITEM)
        return value if isinstance(value, DayPlanItem) else None

    def show_plan(self, plan: DayPlan, zone: ZoneInfo, now: datetime) -> None:
        kept = self.selected()
        self.plan = plan
        clear_list(self.list)
        for item in plan.items:
            action = item.action
            if action is None:
                row = DayRow("Task unavailable", "Restore it, or remove this selection")
            else:
                note = []
                if action.status is not ActionStatus.OPEN:
                    note.append("Done" if action.status is ActionStatus.COMPLETED else "Skipped")
                elif action.ownership is ActionOwnership.WAITING_FOR:
                    note.append("Waiting follow-up")
                else:
                    note.append(
                        "Estimate unknown"
                        if action.estimate_minutes is None
                        else f"Estimate {action.estimate_minutes} min"
                    )
                reason = attention_reason(action, today=plan.day, zone=zone, now=now)
                urgent = False
                if reason is not None:
                    notice = attention_row(
                        AttentionItem(action=action, reason=reason), zone, plan.day
                    )
                    note.append(notice.note)
                    urgent = notice.warning
                row = DayRow(action.title, " · ".join(note), warning=urgent)
            widget_item = QListWidgetItem(row.text())
            widget_item.setData(_ITEM, item)
            widget_item.setData(ROW_ROLE, row)
            self.list.addItem(widget_item)
            if kept is not None and kept.public_id == item.public_id:
                self.list.setCurrentItem(widget_item)
        if self.list.currentRow() < 0 and plan.items:
            self.list.setCurrentRow(0)
        self.list.setVisible(bool(plan.items))
        self.workload.setText(
            f"{plan.known_minutes} min estimated · {plan.unknown_estimates} without estimates"
            f" · {plan.waiting_count} waiting follow-ups"
            if plan.items
            else "Choose work for today. Task dates stay unchanged."
        )
        self._buttons()

    def show_overview(self, summary: DaySummary, zone: ZoneInfo) -> None:
        events = [
            event
            for event in day_rows(summary.calendar, summary.day, zone)
            if not isinstance(event, ActionMarker)
        ]
        rows = [agenda_row(event, zone, summary.day).text() for event in events[:2]]
        self.agenda.setText("Today's agenda · " + (rows[0] if rows else "No appointments"))
        self.agenda_next.setText(rows[1] if len(rows) > 1 else "")
        self.agenda_next.setVisible(len(rows) > 1)
        self.attention_button.setText(
            f"Other commitments needing attention · {len(summary.attention)}"
        )

    def show_capacity(self, text: str) -> None:
        """Today's free time and load, or nothing without working hours."""
        self.capacity.setText(text)
        self.capacity.setVisible(bool(text))

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._buttons()

    @property
    def busy(self) -> bool:
        return self._busy

    def _buttons(self) -> None:
        item = self.selected()
        action = None if item is None else item.action
        available = action is not None and not self._busy
        self.open_button.setEnabled(available)
        self.complete_button.setEnabled(
            available and action is not None and action.status is not ActionStatus.SKIPPED
        )
        label = (
            "Reopen"
            if action is not None and action.status is ActionStatus.COMPLETED
            else "Complete"
        )
        self.complete_button.setText(label)
        self.complete_button.setAccessibleName(label)
        self.remove_button.setEnabled(item is not None and not self._busy)
        row = self.list.currentRow()
        self.up_button.setEnabled(not self._busy and row > 0)
        self.down_button.setEnabled(not self._busy and 0 <= row < self.list.count() - 1)
        for button in (self.open_button, self.complete_button, self.remove_button):
            button.setVisible(item is not None)

    def _open(self) -> None:
        item = self.selected()
        if item is not None and item.action is not None:
            self.open_requested.emit(item.action)

    def _complete(self) -> None:
        item = self.selected()
        if item is not None and item.action is not None:
            self.change_requested.emit("status", item.action, self.plan)

    def _change(self, operation: str) -> None:
        item = self.selected()
        if item is not None and not self._busy:
            self.change_requested.emit(operation, item.public_id, self.plan)

    def _move(self, delta: int) -> None:
        row = self.list.currentRow()
        if (
            self._busy
            or self.plan is None
            or row < 0
            or not 0 <= row + delta < len(self.plan.items)
        ):
            return
        ids = [item.public_id for item in self.plan.items]
        ids[row], ids[row + delta] = ids[row + delta], ids[row]
        self.change_requested.emit("reorder", tuple(ids), self.plan)


class FocusDialog(QDialog):
    search_requested = Signal(str)
    select_requested = Signal(object)
    date_requested = Signal(object)
    today_requested = Signal()
    transfer_requested = Signal(bool, object)
    apply_transfer_requested = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setWindowTitle("Plan today")
        self.setObjectName("focusDialog")
        self.setAccessibleName("Plan today's focus")
        self.resize(560, 580)
        root = QVBoxLayout(self)
        scroll = QScrollArea()
        scroll.setObjectName("dayPlanScroll")
        scroll.setAccessibleName("Day plan controls")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 0, 0)
        scroll.setWidget(content)
        root.addWidget(scroll)
        dialog_heading(
            self, layout, "Plan today", "Selection changes no task dates. Task state is current."
        )
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize)
        dates = QHBoxLayout()
        self.day = QDateEdit()
        calendar_popup(self.day)
        self.day.setObjectName("focusDate")
        self.day.setAccessibleName("Day plan date")
        self.day.setDisplayFormat("yyyy-MM-dd")
        dates.addWidget(self.day)
        self.go_today = outline_button("Go to today", "focusGoToday")
        self.go_today.setAccessibleName("Return to today's day plan")
        self.go_today.clicked.connect(self.today_requested)
        dates.addWidget(self.go_today)
        layout.addLayout(dates)
        self.day.dateChanged.connect(lambda value: self.date_requested.emit(value.toPython()))
        self.panel = FocusPanel(compact=False)
        self.panel.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.panel.list.setMaximumHeight(160)
        layout.addWidget(self.panel)
        self.history_note = wrap_label(
            "Selected tasks with current state. This is not a historical completion report."
        )
        self.history_note.setObjectName("pastPlanExplanation")
        layout.addWidget(self.history_note)
        self.transfer_toggle = outline_button("Carry or move selections", "dayPlanTransferOptions")
        self.transfer_toggle.setAccessibleName("Show carry-forward and move options")
        self.transfer_toggle.setCheckable(True)
        layout.addWidget(self.transfer_toggle)
        self.transfer_area = QWidget()
        self.transfer_area.setObjectName("dayPlanTransferControls")
        self.transfer_area.setAccessibleName("Day plan transfer controls")
        transfer = QVBoxLayout(self.transfer_area)
        transfer.setContentsMargins(0, 0, 0, 0)
        self.destination = QDateEdit()
        calendar_popup(self.destination)
        self.destination.setDisplayFormat("yyyy-MM-dd")
        self.destination.setObjectName("focusDestination")
        self.destination.setAccessibleName("Destination plan date")
        transfer.addWidget(self.destination)
        self.carry = outline_button("Bring unfinished work forward", "focusCarry")
        self.carry.setAccessibleName("Preview copying unfinished selections to the destination")
        self.move_button = outline_button("Move selection to date", "focusMove")
        self.move_button.setAccessibleName(
            "Preview moving unfinished selections to the destination"
        )
        transfer.addWidget(self.carry)
        transfer.addWidget(self.move_button)
        layout.addWidget(self.transfer_area)
        self.transfer_area.hide()
        self.transfer_toggle.toggled.connect(self.transfer_area.setVisible)
        self.carry.clicked.connect(
            lambda: self.transfer_requested.emit(False, self.destination.date().toPython())
        )
        self.move_button.clicked.connect(
            lambda: self.transfer_requested.emit(True, self.destination.date().toPython())
        )
        self.transfer_preview = wrap_label("")
        self.transfer_preview.setObjectName("planTransferPreview")
        self.transfer_preview.setAccessibleName("Plan transfer preview")
        transfer.addWidget(self.transfer_preview)
        self.apply_transfer = outline_button("Apply preview", "applyPlanTransfer")
        self.apply_transfer.setAccessibleName("Apply the plan transfer preview")
        self.apply_transfer.setEnabled(False)
        self.apply_transfer.clicked.connect(self.apply_transfer_requested)
        transfer.addWidget(self.apply_transfer)
        self.query = QLineEdit()
        self.query.setObjectName("focusTaskSearch")
        self.query.setAccessibleName("Search open task titles")
        self.query.setPlaceholderText("Search task titles (two or more letters)")
        layout.addWidget(self.query)
        self.candidates = ActivatingList()
        self.candidates.setObjectName("focusCandidates")
        self.candidates.setAccessibleName("Up to 50 matching open tasks")
        self.candidates.setMaximumHeight(110)
        self.candidates.setItemDelegate(DayRowDelegate(self.candidates))
        layout.addWidget(self.candidates, 1)
        layout.addWidget(wrap_label("Up to 50 open tasks. Waiting tasks are follow-ups."))
        buttons = QHBoxLayout()
        self.add_button = outline_button("Add to today", "focusAdd")
        self.close_button = outline_button("Done", "focusDone")
        for button in (self.add_button, self.close_button):
            button.setAccessibleName(button.text())
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.query.setMaxLength(200)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(200)
        self._timer.timeout.connect(lambda: self.search_requested.emit(self.query.text()))
        self.query.textChanged.connect(self._typed)
        self.candidates.itemActivated.connect(lambda _item: self._add())
        self.add_button.clicked.connect(self._add)
        self.close_button.clicked.connect(self.reject)

    def show_date(self, day: date, today: date) -> None:
        self.day.blockSignals(True)
        self.day.setDate(QDate(day.year, day.month, day.day))
        self.day.blockSignals(False)
        self.history_note.setVisible(day < today)
        self.add_button.setText("Add to selected day")
        self.destination.setDate(QDate(today.year, today.month, today.day))
        self.setWindowTitle(f"Day plan · {day.isoformat()}")

    def show_candidates(self, actions: tuple[Action, ...]) -> None:
        clear_list(self.candidates)
        selected = (
            set()
            if self.panel.plan is None
            else {item.task_public_id for item in self.panel.plan.items}
        )
        for action in actions:
            if action.public_id in selected:
                continue
            row = DayRow(
                action.title, "Waiting" if action.ownership is ActionOwnership.WAITING_FOR else ""
            )
            item = QListWidgetItem(row.text())
            item.setData(_ITEM, action)
            item.setData(ROW_ROLE, row)
            self.candidates.addItem(item)
        if actions:
            self.candidates.setCurrentRow(0)
        self.add_button.setEnabled(bool(self.candidates.count()) and not self.panel.busy)

    def _typed(self) -> None:
        clear_list(self.candidates)
        self.add_button.setEnabled(False)
        self._timer.start()

    def hideEvent(self, event: QHideEvent) -> None:
        self._timer.stop()
        super().hideEvent(event)

    def _add(self) -> None:
        item = self.candidates.currentItem()
        value = None if item is None else item.data(_ITEM)
        if isinstance(value, Action):
            self.select_requested.emit(value)
