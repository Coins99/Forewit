"""The Inbox as a painted list by day: a heading whenever the day's section changes (Today,
Yesterday, Previous 7 days, Previous 30 days, then months and years), then one row per
message.

Rows come in two densities, as Gmail and Outlook show mail. ``WIDE`` rows, the page's
starting state, are one line across the page: the sender, the subject with Gmail's preview,
a tray of marks and the date. ``COMPACT`` rows, beside the reading pane, are two lines: the
sender and the date, then the subject and the marks. An unread message has a dot in the
gutter and a Semibold subject.

Mail text is drawn with ``drawText`` only, so it can never become rich text or a link, and
it never reaches a tooltip.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QPersistentModelIndex,
    QPointF,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFontMetrics, QKeyEvent, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QWidget,
)

from mailbrief.domain.messages import NormalizedMessage
from mailbrief.ui.brief_list import (
    KIND_ROLE,
    NO_SUBJECT,
    ROW_PAD_H,
    ROW_ROLE,
    SINGLE_LINE,
    flat,
    header_height,
    paint_header,
    paint_hover,
    paint_selection,
    paint_separator,
    sender_text,
)
from mailbrief.ui.deadline_text import MONTH_NAMES, month_day, weekday
from mailbrief.ui.hairline import paint_focus_ring
from mailbrief.ui.theme import (
    SMALL_PX,
    TEXT_PX,
    Tokens,
    current_tokens,
    icon_pixmap,
    ui_font,
    ui_metrics,
)

WANTED: Final = "Wanted in brief"
TODAY: Final = "Today"
YESTERDAY: Final = "Yesterday"
PREVIOUS_WEEK: Final = "Previous 7 days"
PREVIOUS_MONTH: Final = "Previous 30 days"
# The last day of each relative section, counted back from today.
_WEEK_DAYS: Final = 6
_MONTH_DAYS: Final = 29

# Layout (logical px). The gutter holds the unread dot; text starts after it.
GUTTER: Final = 14
DOT_PX: Final = 6
WIDE_PAD_V: Final = 12
COMPACT_PAD_V: Final = 8
COMPACT_LINE_GAP: Final = 3
COLUMN_GAP: Final = 16
# The wide row's sender column: this share of the row, within these bounds.
SENDER_SHARE: Final = 0.2
SENDER_MIN: Final = 120
SENDER_MAX: Final = 200
# The tray of marks: a slot each for the wanted flag, an attachment and a star, kept even
# on rows without them, so each mark lines up down the list.
GLYPH_PX: Final = 14
GLYPH_GAP: Final = 4
TRAY_SLOTS: Final = 3
TRAY_W: Final = TRAY_SLOTS * GLYPH_PX + (TRAY_SLOTS - 1) * GLYPH_GAP
TRAY_GAP: Final = 12
# Dates are measured against these, the widest each format gets, so the wide row's date
# column (and the tray beside it) never moves.
_WIDEST_DATES: Final = ("May 30, 2025", "Wed 23:59", "23:59")
_PREVIEW_JOIN: Final = " – "


class Density(StrEnum):
    WIDE = "wide"  # One line across the page.
    COMPACT = "compact"  # Two lines, beside the reading pane.


def section_title(day: date, today: date) -> str:
    """The heading for mail received on ``day`` (in the owner's zone), seen on ``today``:
    Today; Yesterday; Previous 7 days (the five days before, so the week ends today);
    Previous 30 days; then the month this year ("September") and the year before that
    ("2025"). A day after ``today``, from a clock running fast, counts as Today."""
    if day >= today:
        return TODAY
    if day == today - timedelta(days=1):
        return YESTERDAY
    if day >= today - timedelta(days=_WEEK_DAYS):
        return PREVIOUS_WEEK
    if day >= today - timedelta(days=_MONTH_DAYS):
        return PREVIOUS_MONTH
    if day.year == today.year:
        return MONTH_NAMES[day.month - 1]
    return str(day.year)


def list_time(moment: datetime, zone: ZoneInfo, today: date) -> str:
    """When a message came, for the list, in ``zone``: "14:30" today and yesterday (the
    heading names the day), "Sat 14:30" earlier in the week, else "Oct 3", with the year
    when it isn't ``today``'s ("Sep 30, 2025")."""
    local = moment.astimezone(zone)
    day = local.date()
    if day >= today - timedelta(days=1):
        return f"{local:%H:%M}"
    if day >= today - timedelta(days=_WEEK_DAYS):
        return f"{weekday(day)} {local:%H:%M}"
    return month_day(day, today=today)


@dataclass(frozen=True)
class InboxRow:
    kind: Literal["header", "message"]
    title: str  # The heading, or the subject ("(no subject)" when empty).
    message: NormalizedMessage | None = None
    sender: str = ""
    when: str = ""
    wanted: bool = False


def build_inbox_rows(
    messages: Sequence[NormalizedMessage],
    wanted_ids: frozenset[str],
    zone: ZoneInfo,
    today: date,
) -> list[InboxRow]:
    """A heading whenever the section changes, then each message in the page's order
    (newest first), its day and time in ``zone`` seen on ``today``."""
    rows: list[InboxRow] = []
    section: str | None = None
    for message in messages:
        title = section_title(message.received_at_utc.astimezone(zone).date(), today)
        if title != section:
            section = title
            rows.append(InboxRow("header", title))
        rows.append(
            InboxRow(
                "message",
                message.subject or NO_SUBJECT,
                message,
                sender_text(message.sender),
                list_time(message.received_at_utc, zone, today),
                message.provider_message_id in wanted_ids,
            )
        )
    return rows


def accessible_text(row: InboxRow) -> str:
    """What a screen reader says for a row: a heading's title, or the subject, sender and
    time, then each mark."""
    message = row.message
    if row.kind == "header" or message is None:
        return row.title
    parts = [f"{row.title}, from {row.sender}, {row.when}"]
    if not message.is_read:
        parts.append("unread")
    if row.wanted:
        parts.append(WANTED)
    if message.has_attachments:
        parts.append("has attachments")
    if message.is_starred:
        parts.append("starred")
    return ", ".join(parts)


_Index = QModelIndex | QPersistentModelIndex
_ROOT: Final = QModelIndex()


class InboxListModel(QAbstractListModel):
    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._rows: list[InboxRow] = []

    def set_rows(self, rows: Sequence[InboxRow]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rows(self) -> tuple[InboxRow, ...]:
        return tuple(self._rows)

    def row_of(self, message_id: str) -> int | None:
        """The row of the message ``message_id``, or None when the page doesn't hold it."""
        return next(
            (
                number
                for number, row in enumerate(self._rows)
                if row.message is not None and row.message.provider_message_id == message_id
            ),
            None,
        )

    def rowCount(self, parent: _Index = _ROOT) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: _Index, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        row = self._rows[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return row.title
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return accessible_text(row)
        if role == KIND_ROLE:
            return row.kind
        if role == ROW_ROLE:
            return row
        return None

    def flags(self, index: _Index) -> Qt.ItemFlag:
        if not index.isValid() or self._rows[index.row()].kind == "header":
            # Not even enabled: an enabled, unselectable row would still take focus.
            return Qt.ItemFlag.NoItemFlags
        return Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable


def date_width() -> int:
    """The wide row's date column: the widest date the list writes, in tabular figures."""
    metrics = ui_metrics(SMALL_PX, tabular=True)
    return max(metrics.horizontalAdvance(text) for text in _WIDEST_DATES)


def wide_height() -> int:
    return WIDE_PAD_V + ui_metrics(TEXT_PX, semibold=True).height() + WIDE_PAD_V


def compact_height() -> int:
    return (
        COMPACT_PAD_V
        + ui_metrics(SMALL_PX).height()
        + COMPACT_LINE_GAP
        + ui_metrics(TEXT_PX, semibold=True).height()
        + COMPACT_PAD_V
    )


def _elided(text: str, metrics: QFontMetrics, width: int) -> str:
    return metrics.elidedText(flat(text), Qt.TextElideMode.ElideRight, max(0, width))


def paint_dot(painter: QPainter, left: int, middle: float, tokens: Tokens) -> None:
    """The unread dot, in the accent colour, centred on ``middle``."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(tokens.accent_fg))
    painter.drawEllipse(QPointF(left + DOT_PX / 2, middle), DOT_PX / 2, DOT_PX / 2)
    painter.restore()


def paint_tray(painter: QPainter, row: InboxRow, left: int, middle: float, tokens: Tokens) -> None:
    """The marks in their slots from ``left``: the wanted flag (warning), an attachment and
    a star (secondary)."""
    message = row.message
    if message is None:
        return
    marks = (
        ("flag", tokens.warning_fg) if row.wanted else None,
        ("paperclip", tokens.text_secondary) if message.has_attachments else None,
        ("star", tokens.text_secondary) if message.is_starred else None,
    )
    ratio = painter.device().devicePixelRatioF()
    top = round(middle - GLYPH_PX / 2)
    for slot, mark in enumerate(marks):
        if mark is not None:
            x = left + slot * (GLYPH_PX + GLYPH_GAP)
            painter.drawPixmap(x, top, icon_pixmap(mark[0], mark[1], GLYPH_PX, ratio))


def paint_subject(
    painter: QPainter,
    rect: QRect,
    subject: str,
    preview: str,
    *,
    unread: bool,
    tokens: Tokens,
) -> None:
    """The subject, Semibold when unread, then " – " and the preview in the secondary
    colour in whatever room is left, on one line in ``rect``."""
    subject_metrics = ui_metrics(TEXT_PX, semibold=unread)
    subject_text = _elided(subject, subject_metrics, rect.width())
    painter.setFont(ui_font(TEXT_PX, semibold=unread))
    painter.setPen(QColor(tokens.text))
    painter.drawText(rect, SINGLE_LINE, subject_text)
    used = subject_metrics.horizontalAdvance(subject_text)
    rest = rect.width() - used
    shown = flat(preview)
    preview_metrics = ui_metrics(TEXT_PX)
    # Only when the whole subject fits and a few of the preview's letters would too.
    least = preview_metrics.horizontalAdvance(f"{_PREVIEW_JOIN}abc…")
    if not shown or subject_text != flat(subject) or rest < least:
        return
    # Elided as is: flat() would strip the join's leading space.
    tail = preview_metrics.elidedText(_PREVIEW_JOIN + shown, Qt.TextElideMode.ElideRight, rest)
    painter.setFont(ui_font(TEXT_PX))
    painter.setPen(QColor(tokens.text_secondary))
    painter.drawText(QRect(rect.left() + used, rect.top(), rest, rect.height()), SINGLE_LINE, tail)


class InboxRowDelegate(QStyledItemDelegate):
    """Paints headings and message rows with ``drawText`` only, at ``density``."""

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.density = Density.WIDE

    def sizeHint(self, option: QStyleOptionViewItem, index: _Index) -> QSize:
        row = index.data(ROW_ROLE)
        width: int = option.rect.width()
        if not isinstance(row, InboxRow):
            return QSize(width, 0)
        if row.kind == "header":
            return QSize(width, header_height())
        return QSize(width, wide_height() if self.density is Density.WIDE else compact_height())

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: _Index) -> None:
        row = index.data(ROW_ROLE)
        if not isinstance(row, InboxRow):
            return
        tokens = current_tokens()
        rect: QRect = option.rect
        state: QStyle.StateFlag = option.state
        painter.save()
        if row.kind == "header":
            paint_header(painter, rect, row.title, tokens)
            painter.restore()
            return
        paint_hover(painter, rect, state, tokens)
        paint_selection(painter, rect, state, tokens)
        if self.density is Density.WIDE:
            self._paint_wide(painter, rect, row, tokens)
        else:
            self._paint_compact(painter, rect, row, tokens)
        paint_separator(painter, rect, tokens)
        paint_focus_ring(painter, rect, state, tokens.accent_fg)
        painter.restore()

    def _paint_wide(self, painter: QPainter, rect: QRect, row: InboxRow, tokens: Tokens) -> None:
        """Sender, subject and preview, tray, date: one line."""
        message = row.message
        if message is None:
            return
        unread = not message.is_read
        left = rect.left() + ROW_PAD_H
        right = rect.left() + rect.width() - ROW_PAD_H
        middle = rect.top() + rect.height() / 2
        if unread:
            paint_dot(painter, left, middle, tokens)
        top, height = rect.top(), rect.height()
        date_w = date_width()
        date_left = right - date_w
        painter.setFont(ui_font(SMALL_PX, tabular=True))
        painter.setPen(QColor(tokens.text_secondary))
        painter.drawText(
            QRect(date_left, top, date_w, height),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            | int(Qt.TextFlag.TextSingleLine),
            row.when,
        )
        tray_left = date_left - TRAY_GAP - TRAY_W
        paint_tray(painter, row, tray_left, middle, tokens)
        text_left = left + GUTTER
        sender_w = min(SENDER_MAX, max(SENDER_MIN, int(rect.width() * SENDER_SHARE)))
        sender_w = max(0, min(sender_w, tray_left - COLUMN_GAP - text_left))
        sender_metrics = ui_metrics(TEXT_PX, semibold=unread)
        painter.setFont(ui_font(TEXT_PX, semibold=unread))
        painter.setPen(QColor(tokens.text))
        painter.drawText(
            QRect(text_left, top, sender_w, height),
            SINGLE_LINE,
            _elided(row.sender, sender_metrics, sender_w),
        )
        subject_left = text_left + sender_w + COLUMN_GAP
        subject_w = tray_left - COLUMN_GAP - subject_left
        if subject_w > 0:
            paint_subject(
                painter,
                QRect(subject_left, top, subject_w, height),
                row.title,
                message.body_preview,
                unread=unread,
                tokens=tokens,
            )

    def _paint_compact(self, painter: QPainter, rect: QRect, row: InboxRow, tokens: Tokens) -> None:
        """The sender and date, then the subject and the tray: two lines."""
        message = row.message
        if message is None:
            return
        unread = not message.is_read
        left = rect.left() + ROW_PAD_H
        right = rect.left() + rect.width() - ROW_PAD_H
        text_left = left + GUTTER
        first = QRect(text_left, rect.top() + COMPACT_PAD_V, 0, ui_metrics(SMALL_PX).height())
        first.setRight(right - 1)
        date_metrics = ui_metrics(SMALL_PX, tabular=True)
        date_w = date_metrics.horizontalAdvance(row.when)
        painter.setFont(ui_font(SMALL_PX, tabular=True))
        painter.setPen(QColor(tokens.text_secondary))
        painter.drawText(
            first,
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            | int(Qt.TextFlag.TextSingleLine),
            row.when,
        )
        sender_w = first.width() - date_w - COLUMN_GAP // 2
        painter.setFont(ui_font(SMALL_PX))
        painter.drawText(
            QRect(first.left(), first.top(), max(0, sender_w), first.height()),
            SINGLE_LINE,
            _elided(row.sender, ui_metrics(SMALL_PX), sender_w),
        )
        second_top = first.bottom() + 1 + COMPACT_LINE_GAP
        second_h = ui_metrics(TEXT_PX, semibold=True).height()
        middle = second_top + second_h / 2
        if unread:
            paint_dot(painter, left, middle, tokens)
        tray_left = right - TRAY_W
        paint_tray(painter, row, tray_left, middle, tokens)
        subject_w = tray_left - TRAY_GAP - text_left
        if subject_w > 0:
            paint_subject(
                painter,
                QRect(text_left, second_top, subject_w, second_h),
                row.title,
                "",
                unread=unread,
                tokens=tokens,
            )


class InboxListView(QListView):
    """``opened(NormalizedMessage)`` when a message is clicked, or Return or Enter is
    pressed on it (once, consumed); ``current_message_changed(NormalizedMessage)`` follows
    the current row, by keyboard or mouse. Escape is left to the page."""

    opened = Signal(object)
    current_message_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("inboxList")
        self.setAccessibleName("Inbox messages")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setUniformItemSizes(False)
        self.setMouseTracking(True)  # For the hover fill.
        self.inbox_model = InboxListModel(self)
        self.setModel(self.inbox_model)
        self.row_delegate = InboxRowDelegate(self)
        self.setItemDelegate(self.row_delegate)
        self.selectionModel().currentChanged.connect(self._current_changed)
        self.clicked.connect(self._clicked)

    def density(self) -> Density:
        return self.row_delegate.density

    def set_density(self, density: Density) -> None:
        """Paint rows at ``density``, keeping the current row in view."""
        if density is self.row_delegate.density:
            return
        self.row_delegate.density = density
        self.doItemsLayout()  # The rows change height.
        current = self.currentIndex()
        if current.isValid():
            self.scrollTo(current)

    def show_rows(self, rows: Sequence[InboxRow], *, current: str | None = None) -> None:
        """Show ``rows``, with the message ``current`` as the current row when the page
        holds it, else no current row."""
        self.inbox_model.set_rows(rows)
        if current is not None:
            self.set_current(current)

    def set_current(self, message_id: str) -> bool:
        """Make the message ``message_id`` the current row; False when the page lacks it."""
        number = self.inbox_model.row_of(message_id)
        if number is None:
            return False
        index = self.inbox_model.index(number)
        self.setCurrentIndex(index)
        self.scrollTo(index)
        return True

    def current_message(self) -> NormalizedMessage | None:
        row = self.currentIndex().data(ROW_ROLE)
        return row.message if isinstance(row, InboxRow) else None

    def message_count(self) -> int:
        return sum(1 for row in self.inbox_model.rows() if row.kind == "message")

    def _current_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        row = current.data(ROW_ROLE)
        if isinstance(row, InboxRow) and row.message is not None:
            self.current_message_changed.emit(row.message)

    def _clicked(self, index: QModelIndex) -> None:
        row = index.data(ROW_ROLE)
        if isinstance(row, InboxRow) and row.message is not None:
            self.opened.emit(row.message)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            event.ignore()  # The page closes the open message.
            return
        message = self.current_message()
        if message is not None and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # Open once and consume the key, as ActivatingList does (ui/lists.py).
            self.opened.emit(message)
            event.accept()
            return
        super().keyPressEvent(event)
