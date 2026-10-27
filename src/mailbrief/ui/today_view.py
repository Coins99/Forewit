"""The Today page's "Your day": what needs attention, today's agenda and new replies.

Three cards above the brief, read from local data only, so they work without Gmail or AI.
Each row names an existing action or calendar entry; activating it (a double-click, Return
or Enter, as in the app's other lists) opens that record on its own page. Titles, senders
and calendar names are the owner's or mail-derived text: rows are painted with ``drawText``
only, never as rich text or in a tooltip.
"""

from dataclasses import dataclass
from datetime import date
from typing import Final
from zoneinfo import ZoneInfo

from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPointF,
    QRect,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.actions import Action
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.events import ActionMarker, CalendarEvent
from mailbrief.domain.google_calendar import GoogleEvent
from mailbrief.domain.today import AttentionItem, AttentionReason, DaySummary
from mailbrief.ui.brief_list import ROW_PAD_H, ROW_ROLE, SINGLE_LINE, elided, paint_selection
from mailbrief.ui.calendar_view import day_rows, entry_key, event_when
from mailbrief.ui.deadline_text import day_text, deadline_text
from mailbrief.ui.hairline import HairlineFrame, paint_focus_ring
from mailbrief.ui.labels import plain_label, wrap_label
from mailbrief.ui.lists import ActivatingList, clear_list
from mailbrief.ui.theme import (
    SMALL_PX,
    TEXT_PX,
    current_tokens,
    ui_font,
    ui_metrics,
)
from mailbrief.ui.who_text import in_sentence, who_text

# A card shows this many rows before its list scrolls.
VISIBLE_ROWS: Final = 3
ATTENTION: Final = "Needs attention"
AGENDA: Final = "Today's agenda"
REPLIES: Final = "New replies"
NO_ATTENTION: Final = "Nothing due or planned for today."
NO_EVENTS: Final = "No events today."
NO_REPLIES: Final = "No new replies on your actions."
# A row's padding, the gap between its title and note, and the share of its width a long
# title keeps (logical px and a fraction). The text sits clear of the rounded selection.
_PAD_H: Final = ROW_PAD_H
_PAD_V: Final = 6
_GAP: Final = 12
_TITLE_SHARE: Final = 0.4
# A status dot before a due or newsy row's note, as a dashboard marks state: its diameter
# and the gap after it (logical px).
DOT_PX: Final = 6
_DOT_GAP: Final = 6
# A card's count, as a dashboard's figure (logical px, Semibold).
COUNT_PX: Final = 22
# What each row opens: an action (its ID, and whether it waits for others), or an event.
_TARGET: Final = Qt.ItemDataRole.UserRole

_Index = QModelIndex | QPersistentModelIndex


@dataclass(frozen=True)
class DayRow:
    """What a row paints: its title at the left and a short note at the right, in the
    warning colour when something is due or overdue. A due row (``warning``) or one with
    news (``accent``, a new reply) has a small dot of its colour before the note."""

    title: str
    note: str
    warning: bool = False
    accent: bool = False

    def text(self) -> str:
        """The row as one line, for screen readers."""
        return f"{self.title}, {self.note}" if self.note else self.title


def attention_row(item: AttentionItem, zone: ZoneInfo, today: date) -> DayRow:
    """The action's title, and "Overdue · Mon Oct 12", "Due 17:00", "Due today", "Today"
    or "Planned Fri Oct 9"; for one the owner waits for, whose target is its follow-up
    date (B08), "Follow up today" or "Follow-up date Fri Oct 9", with "waiting" or "waiting
    on Alex"; and "for Sam" with a person the owner's own task is for."""
    action = item.action
    waiting = action.ownership is ActionOwnership.WAITING_FOR
    if item.reason is AttentionReason.OVERDUE:
        due = deadline_text(action, zone, today=today)
        note = f"Overdue · {due}" if due else "Overdue"
    elif item.reason is AttentionReason.DUE_TODAY:
        local = action.deadline_at_utc.astimezone(zone) if action.deadline_at_utc else None
        note = f"Due {local:%H:%M}" if local is not None else "Due today"
    elif item.reason is AttentionReason.PLANNED_TODAY:
        note = "Follow up today" if waiting else "Today"
    else:
        assert action.target_date is not None
        day = day_text(action.target_date, today=today)
        note = f"Follow-up date {day}" if waiting else f"Planned {day}"
    if (who := who_text(waiting, action.person, unnamed="Waiting")) is not None:
        note += " · " + in_sentence(who)
    urgent = item.reason in (AttentionReason.OVERDUE, AttentionReason.DUE_TODAY)
    return DayRow(action.title, note, warning=urgent)


def agenda_row(event: CalendarEvent | GoogleEvent, zone: ZoneInfo, today: date) -> DayRow:
    """ "10:00–10:15 · Stand-up", with a Google event's calendar as its note."""
    note = event.calendar_name if isinstance(event, GoogleEvent) else ""
    return DayRow(f"{event_when(event, today, zone, today)} · {event.title}", note)


def reply_row(action: Action) -> DayRow:
    """The action's title, and "2 new · Priya Shah"."""
    thread = action.thread
    assert thread is not None
    sender = f" · {thread.latest_sender}" if thread.latest_sender else ""
    return DayRow(action.title, f"{thread.new_messages} new{sender}", accent=True)


class DayRowDelegate(QStyledItemDelegate):
    """Paints a ``DayRow`` on one line with ``drawText`` only: the title at the left, the
    note at the right. The title gives way first, down to _TITLE_SHARE of the row."""

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: _Index) -> None:
        row = index.data(ROW_ROLE)
        if not isinstance(row, DayRow):
            super().paint(painter, option, index)
            return
        tokens = current_tokens()
        rect: QRect = option.rect
        state: QStyle.StateFlag = option.state
        painter.save()
        painter.setClipRect(rect)
        paint_selection(painter, rect, state, tokens)
        metrics = ui_metrics(TEXT_PX)
        inner = rect.adjusted(_PAD_H, 0, -_PAD_H, 0)
        # The note takes the room the title doesn't need, but leaves the title at least
        # _TITLE_SHARE of the row when the title is longer than that.
        dot = DOT_PX + _DOT_GAP if row.note and (row.warning or row.accent) else 0
        kept = min(metrics.horizontalAdvance(row.title), int(inner.width() * _TITLE_SHARE))
        room = max(0, inner.width() - kept - _GAP - dot)
        note = elided(row.note, metrics, room) if row.note else ""
        note_width = metrics.horizontalAdvance(note)
        title_width = max(0, inner.width() - note_width - dot - (_GAP if note else 0))
        painter.setFont(ui_font(TEXT_PX))
        painter.setPen(QColor(tokens.text))
        painter.drawText(
            QRect(inner.left(), inner.top(), title_width, inner.height()),
            SINGLE_LINE,
            elided(row.title, metrics, title_width),
        )
        if note:
            painter.setPen(QColor(tokens.warning_fg if row.warning else tokens.text_secondary))
            painter.drawText(
                QRect(inner.right() - note_width + 1, inner.top(), note_width, inner.height()),
                SINGLE_LINE,
                note,
            )
        if note and dot:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens.warning_fg if row.warning else tokens.accent_fg))
            right = inner.right() - note_width - _DOT_GAP + 1
            centre = QPointF(right - DOT_PX / 2, inner.center().y() + 0.5)
            painter.drawEllipse(centre, DOT_PX / 2, DOT_PX / 2)
        paint_focus_ring(painter, rect, state, tokens.accent_fg)
        painter.restore()

    def sizeHint(self, option: QStyleOptionViewItem, index: _Index) -> QSize:
        # No width of its own: a row takes the viewport's width, so it never widens the card.
        return QSize(0, ui_metrics(TEXT_PX).height() + 2 * _PAD_V)


class _Card(HairlineFrame):
    """A titled card holding a list of at most VISIBLE_ROWS visible rows, or an empty
    line."""

    def __init__(self, title: str, name: str, empty: str) -> None:
        super().__init__()
        self.setObjectName(name)
        self.setAccessibleName(title)
        self._title = title
        self._visible_rows = VISIBLE_ROWS
        layout = QVBoxLayout(self)
        # The layout's margins are the card's only padding: the rows' selection reaches
        # near the card's edge, and their text lines up with the heading, inside the
        # card's rounded corner.
        self.setContentsMargins(0, 0, 0, 0)
        layout.setContentsMargins(4, 12, 4, 8)
        layout.setSpacing(4)
        # A dashboard's figure: the card's title at the left, its count large at the right.
        top = QHBoxLayout()
        top.setContentsMargins(_PAD_H, 0, _PAD_H, 0)  # In line with the rows' text.
        top.setSpacing(8)
        self.heading = plain_label(title, tone="secondary", px=SMALL_PX, semibold=True)
        self.heading.setObjectName(f"{name}Heading")
        self.heading.setWordWrap(False)
        top.addWidget(self.heading, 1, Qt.AlignmentFlag.AlignBottom)
        self.count = plain_label("0", tone="muted", px=COUNT_PX, semibold=True)
        self.count.setObjectName(f"{name}Count")
        self.count.setWordWrap(False)
        self.count.setAccessibleName(f"{title}: 0")
        top.addWidget(self.count, 0, Qt.AlignmentFlag.AlignBottom)
        layout.addLayout(top)
        self.list = ActivatingList()
        self.list.setObjectName(f"{name}List")
        self.list.setAccessibleName(title)
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerItem)
        self.list.setUniformItemSizes(True)
        self.list.setItemDelegate(DayRowDelegate(self.list))
        layout.addWidget(self.list)
        self.empty = wrap_label(empty, tone="muted", px=SMALL_PX)
        self.empty.setObjectName(f"{name}Empty")
        self.empty.setContentsMargins(_PAD_H, 0, _PAD_H, 0)
        layout.addWidget(self.empty)
        layout.addStretch(1)

    def show_rows(self, rows: list[tuple[DayRow, object]]) -> None:
        """Each row and what it opens; the heading counts them."""
        clear_list(self.list)
        for row, target in rows:
            item = QListWidgetItem(row.text())
            item.setData(ROW_ROLE, row)
            item.setData(_TARGET, target)
            self.list.addItem(item)
        self.count.setText(str(len(rows)))
        self.count.setAccessibleName(f"{self._title}: {len(rows)}")
        # A count of none is quiet; one with something in it reads at full strength.
        self.count.setProperty("tone", None if rows else "muted")
        self.count.style().unpolish(self.count)
        self.count.style().polish(self.count)
        self.list.setVisible(bool(rows))
        self.empty.setVisible(not rows)
        if rows:
            self.set_visible_rows(self._visible_rows)

    def list_height(self, rows: int) -> int:
        shown = min(self.list.count(), rows)
        return sum(self.list.sizeHintForRow(number) for number in range(shown)) + (
            2 * self.list.frameWidth()
        )

    def set_visible_rows(self, rows: int) -> None:
        self._visible_rows = rows
        if self.list.count():
            height = self.list_height(rows)
            if self.list.minimumHeight() != height or self.list.maximumHeight() != height:
                self.list.setFixedHeight(height)

    def preview_height(self, rows: int, width: int) -> int:
        """Measure the card without changing any geometry while choosing a row budget."""
        layout = self.layout()
        assert layout is not None
        height = (
            layout.totalHeightForWidth(width)
            if layout.hasHeightForWidth()
            else layout.totalSizeHint().height()
        )
        if self.list.count():
            height += self.list_height(rows) - self.list.minimumHeight()
        return height


class DayPanel(QWidget):
    """``action_requested(public_id, waiting)`` opens an action on the Actions page (its
    Waiting view when ``waiting``); ``event_requested(key)`` opens a calendar entry, by its
    calendar_view.entry_key, on the Calendar page."""

    action_requested = Signal(str, bool)
    event_requested = Signal(object)
    content_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("dayPanel")
        self.setAccessibleName("Your day")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 6)
        layout.setSpacing(12)
        self.attention = _Card(ATTENTION, "dayAttention", NO_ATTENTION)
        self.agenda = _Card(AGENDA, "dayAgenda", NO_EVENTS)
        self.replies = _Card(REPLIES, "dayReplies", NO_REPLIES)
        for card in (self.attention, self.agenda, self.replies):
            layout.addWidget(card, 1)
            card.list.itemActivated.connect(self._activated)
        self.summary: DaySummary | None = None
        self.show_summary(None, ZoneInfo("UTC"))

    def show_summary(self, summary: DaySummary | None, zone: ZoneInfo) -> None:
        """Show ``summary`` read in ``zone``; None shows every card empty."""
        self.summary = summary
        if summary is None:
            for card in (self.attention, self.agenda, self.replies):
                card.show_rows([])
            self.content_changed.emit()
            return
        today = summary.day
        self.attention.show_rows(
            [
                (attention_row(item, zone, today), _action_target(item.action))
                for item in summary.attention
            ]
        )
        # The actions' dates are under Needs attention, so the agenda lists events only.
        events = [
            entry
            for entry in day_rows(summary.calendar, today, zone)
            if not isinstance(entry, ActionMarker)
        ]
        self.agenda.show_rows(
            [(agenda_row(event, zone, today), entry_key(event)) for event in events]
        )
        self.replies.show_rows(
            [(reply_row(action), _action_target(action)) for action in summary.replies]
        )
        self.content_changed.emit()

    def preview_height(self, rows: int, width: int) -> int:
        layout = self.layout()
        assert layout is not None
        margins = layout.contentsMargins()
        card_width = max(0, (width - margins.left() - margins.right() - 2 * layout.spacing()) // 3)
        return (
            margins.top()
            + margins.bottom()
            + max(
                card.preview_height(rows, card_width)
                for card in (self.attention, self.agenda, self.replies)
            )
        )

    def set_visible_rows(self, rows: int) -> None:
        for card in (self.attention, self.agenda, self.replies):
            card.set_visible_rows(rows)

    def _activated(self, item: QListWidgetItem) -> None:
        target = item.data(_TARGET)
        if not isinstance(target, tuple) or not target:
            return
        if target[0] == "action":
            self.action_requested.emit(str(target[1]), bool(target[2]))
        else:
            self.event_requested.emit(target)


class DayOverview(QWidget):
    """Fit complete preview rows above the brief, with scrolling only inside each card."""

    def __init__(self, panel: DayPanel, brief: QWidget) -> None:
        super().__init__()
        self.setObjectName("dayOverview")
        self.setAccessibleName("Your day")
        self.panel = panel
        self._brief = brief
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 12)  # Clear space above the brief's filter strip.
        layout.setSpacing(0)
        layout.addWidget(panel)
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._fit)
        panel.content_changed.connect(self._schedule_fit)
        panel.installEventFilter(self)

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        parent = self.parentWidget()
        if parent is not None:
            parent.installEventFilter(self)
        self._fit()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() in (
            QEvent.Type.Resize,
            QEvent.Type.LayoutRequest,
            QEvent.Type.FontChange,
            QEvent.Type.StyleChange,
        ):
            self._schedule_fit()
        return False

    def _schedule_fit(self) -> None:
        if self.isVisible():
            self._fit_timer.start(0)

    def _fit(self) -> None:
        parent = self.parentWidget()
        if parent is None or not self.isVisible():
            return
        layout = self.layout()
        assert layout is not None
        gap = layout.contentsMargins().bottom()
        # Leave most of the Today page for the filter strip and brief. On a short window,
        # show fewer complete rows instead of clipping the cards or adding an outer scroll.
        # These two heights share the room left after the filter strip and brief heading.
        # Keeping the preview within half of that room also preserves the brief's list.
        budget = min(parent.height() // 3, (self.height() + self._brief.height()) // 2)
        rows = VISIBLE_ROWS
        while rows > 1 and self.panel.preview_height(rows, self.width()) + gap > budget:
            rows -= 1
        height = self.panel.preview_height(rows, self.width()) + gap
        self.panel.set_visible_rows(rows)
        if self.minimumHeight() != height or self.maximumHeight() != height:
            self.setFixedHeight(height)


def _action_target(action: Action) -> tuple[str, str, bool]:
    return ("action", action.public_id, action.ownership is ActionOwnership.WAITING_FOR)
