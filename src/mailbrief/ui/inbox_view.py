"""The Inbox page: the cached Inbox's metadata across all received dates, with no bodies or
AI calls.

It starts as a list across the page, as Gmail shows mail. Opening a message (a click, or
Return) splits the page as Outlook does: the list narrows to two-line rows at the left and
the reading pane shows the message, following the list's current row. Close or Escape goes
back to the list. When the page is too narrow for both, the reading pane takes it.

**Should have been in my brief** marks a message the owner wanted (ADR 0020): ranking then
counts it for its sender. The mark is local; Gmail is never told.
"""

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Final
from zoneinfo import ZoneInfo

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QKeyEvent,
    QKeySequence,
    QPainter,
    QPaintEvent,
    QPixmap,
    QResizeEvent,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.cached_mail import WANTED_WINDOW_DAYS, InboxPage
from mailbrief.domain.messages import NormalizedMessage
from mailbrief.ui.hairline import HairlineSplitter
from mailbrief.ui.inbox_list import WANTED, Density, InboxListView, build_inbox_rows
from mailbrief.ui.inbox_reader import UNWANT, WANT, WANTED_NOTE, InboxReader
from mailbrief.ui.labels import ElidedLabel, outline_button, plain_label
from mailbrief.ui.theme import (
    HEADING_PX,
    SMALL_PX,
    TEXT_PX,
    current_tokens,
    follow_theme,
    icon_pixmap,
    ui_font,
    ui_metrics,
)

__all__ = [
    "INBOX_PAGE_SIZE",
    "UNWANT",
    "WANT",
    "WANTED",
    "WANTED_NOTE",
    "InboxPanel",
    "range_text",
    "wanted_line",
    "wanted_short",
]

SIGN_IN: Final = "Sign in with Google to sync your full Inbox."
NO_MESSAGES: Final = "No messages in your cached Inbox. Refresh checks Gmail."
INCOMPLETE: Final = "Inbox sync incomplete"

# Messages per Inbox page; Previous and Next move by one page.
INBOX_PAGE_SIZE: Final = 100
# The list's width beside the reading pane: this share of the page, within these bounds,
# unless the owner drags the divider. Narrower than both minimums, the reader takes the page.
LIST_SHARE: Final = 0.38
LIST_MIN: Final = 320
LIST_MAX: Final = 400
READER_MIN: Final = 360
# The list's header and footer bars (logical px).
BAR_HEIGHT: Final = 40
_TITLE_GAP: Final = 8
_EMPTY_ICON_PX: Final = 48
_EMPTY_ICON_OPACITY: Final = 0.45
_FLAG_PX: Final = 12


def wanted_line(count: int) -> str:
    """How many wanted messages the review didn't suggest lately, for the owner's journal."""
    return (
        f"Emails you wanted that weren't suggested, last {WANTED_WINDOW_DAYS} days: {count}. "
        "That counts ones you picked yourself in a review and ones marked here."
    )


def wanted_short(count: int) -> str:
    """The same count in a few words, for the list's footer; the number comes first, so it
    stays when the words are cut short."""
    return f"{count} missed by the review in {WANTED_WINDOW_DAYS} days"


def range_text(page: InboxPage) -> str:
    """Which messages the page shows of how many: "1–100 of 342"; empty with none."""
    if not page.messages:
        return ""
    last = page.offset + len(page.messages)
    total = max(page.total, last)
    return f"{page.offset + 1:,}–{last:,} of {total:,}"


class InboxTitle(QWidget):
    """ "Inbox" and how many messages the cached Inbox holds, on one baseline."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("inboxTitle")
        self._count: int | None = None
        self.set_count(None)

    def count(self) -> int | None:
        return self._count

    def set_count(self, count: int | None) -> None:
        self._count = count
        self.setAccessibleName("Inbox" if count is None else f"Inbox, {count:,} messages")
        self.updateGeometry()
        self.update()

    def _count_text(self) -> str:
        return "" if self._count is None else f"{self._count:,}"

    def sizeHint(self) -> QSize:
        title = ui_metrics(HEADING_PX, semibold=True)
        width = title.horizontalAdvance("Inbox")
        if self._count is not None:
            count = ui_metrics(SMALL_PX, tabular=True)
            width += _TITLE_GAP + count.horizontalAdvance(self._count_text())
        return QSize(width + 1, title.height())

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def paintEvent(self, event: QPaintEvent) -> None:
        tokens = current_tokens()
        title = ui_metrics(HEADING_PX, semibold=True)
        baseline = (self.height() + title.ascent() - title.descent()) / 2
        painter = QPainter(self)
        painter.setFont(ui_font(HEADING_PX, semibold=True))
        painter.setPen(QColor(tokens.text))
        painter.drawText(QPointF(0, baseline), "Inbox")
        if self._count is not None:
            painter.setFont(ui_font(SMALL_PX, tabular=True))
            painter.setPen(QColor(tokens.text_secondary))
            left = title.horizontalAdvance("Inbox") + _TITLE_GAP
            painter.drawText(QPointF(left, baseline), self._count_text())
        painter.end()


class WantedCount(QWidget):
    """The footer's flag and how many wanted messages the review missed lately; the whole
    sentence is its tooltip and accessible description."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("inboxWantedStatus")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self.flag = QLabel()
        self.flag.setObjectName("inboxWantedStatusFlag")
        follow_theme(
            self.flag,
            lambda: self.flag.setPixmap(
                icon_pixmap("flag", current_tokens().warning_fg, _FLAG_PX, 2.0)
            ),
        )
        layout.addWidget(self.flag)
        self.label = ElidedLabel(tone="secondary", px=SMALL_PX)
        self.label.setObjectName("inboxWantedStatusText")
        layout.addWidget(self.label, 1)
        self.set_count(None)

    def text(self) -> str:
        """The whole sentence, as the journal reads it; empty when hidden."""
        return self.accessibleDescription()

    def set_count(self, count: int | None) -> None:
        whole = "" if count is None else wanted_line(count)
        self.label.setText("" if count is None else wanted_short(count), whole)
        self.setToolTip(whole)
        self.setAccessibleName("" if count is None else wanted_short(count))
        self.setAccessibleDescription(whole)
        self.setVisible(count is not None)


class EmptyInbox(QWidget):
    """A faint mail glyph above a line of app text, centred: shown instead of the list."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("inboxEmpty")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)
        layout.addStretch(1)
        self.glyph = QLabel()
        self.glyph.setObjectName("inboxEmptyGlyph")
        self.glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        follow_theme(self.glyph, self._paint_glyph)
        layout.addWidget(self.glyph)
        self.message = plain_label("", tone="secondary", px=TEXT_PX)
        self.message.setObjectName("inboxEmptyMessage")
        self.message.setAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.message)
        layout.addStretch(2)

    def _paint_glyph(self) -> None:
        ratio = 2.0
        source = icon_pixmap("mail", current_tokens().text_muted, _EMPTY_ICON_PX, ratio)
        faint = QPixmap(source.size())
        faint.setDevicePixelRatio(ratio)
        faint.fill(Qt.GlobalColor.transparent)
        painter = QPainter(faint)
        painter.setOpacity(_EMPTY_ICON_OPACITY)
        painter.drawPixmap(0, 0, source)
        painter.end()
        self.glyph.setPixmap(faint)

    def set_message(self, text: str) -> None:
        self.message.setText(text)
        self.setAccessibleName(text)


class InboxPanel(QWidget):
    """``wanted_requested(message_id, wanted)`` asks to mark or unmark the open message;
    ``search_requested`` asks for the command palette's search."""

    refresh_requested = Signal()
    page_requested = Signal(int)
    source_requested = Signal(str)
    wanted_requested = Signal(str, bool)
    search_requested = Signal()

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        super().__init__()
        self.setObjectName("inboxPanel")
        self.setAccessibleName("Gmail Inbox")
        self.zone = ZoneInfo("UTC")
        self.clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self._page: InboxPage | None = None
        self._open: str | None = None  # The message in the reading pane.
        self._list_width: int | None = None  # Once the owner drags the divider.
        self._busy = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        # The page's least width is minimumSizeHint's, not the list's and the reading
        # pane's side by side: narrower than both, the reading pane takes the page.
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.split = HairlineSplitter()
        self.split.setObjectName("inboxSplit")
        self.split.setChildrenCollapsible(False)
        self.list_column = QWidget()
        self.list_column.setObjectName("inboxListColumn")
        self.list_column.setMinimumWidth(LIST_MIN)
        column = QVBoxLayout(self.list_column)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._header())
        self.stack = QStackedWidget()
        self.stack.setObjectName("inboxStack")
        self.messages = InboxListView()
        self.empty = EmptyInbox()
        self.stack.addWidget(self.messages)
        self.stack.addWidget(self.empty)
        column.addWidget(self.stack, 1)
        self.footer = self._footer()
        column.addWidget(self.footer)
        self.split.addWidget(self.list_column)
        self.reader = InboxReader()
        self.reader.setMinimumWidth(READER_MIN)
        self.split.addWidget(self.reader)
        self.split.setStretchFactor(0, 0)
        self.split.setStretchFactor(1, 1)
        layout.addWidget(self.split)
        # The reader's buttons, by the names the window and the tests use.
        self.source = self.reader.source
        self.wanted = self.reader.wanted
        self.refresh.clicked.connect(self.refresh_requested)
        self.search.clicked.connect(self.search_requested)
        self.previous.clicked.connect(self._previous_page)
        self.next.clicked.connect(self._next_page)
        self.messages.opened.connect(self._open_row)
        self.messages.current_message_changed.connect(self._follow_current)
        self.reader.close_requested.connect(self.close_message)
        self.reader.source_requested.connect(self.source_requested)
        self.reader.wanted_requested.connect(self.wanted_requested)
        self.split.splitterMoved.connect(self._divider_moved)
        self.clear()

    def _header(self) -> QWidget:
        header = QWidget()
        header.setObjectName("inboxHeader")
        header.setFixedHeight(BAR_HEIGHT)
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 0, 10, 0)
        row.setSpacing(2)
        self.title = InboxTitle()
        row.addWidget(self.title)
        row.addStretch(1)
        shortcut = QKeySequence("Ctrl+K").toString(QKeySequence.SequenceFormat.NativeText)
        self.search = outline_button(
            "", "inboxSearchButton", icon_name="search", accessible_name="Search", glass=True
        )
        self.search.setToolTip(f"Search your mail, actions and notes ({shortcut})")
        row.addWidget(self.search)
        self.refresh = outline_button(
            "",
            "refreshInboxButton",
            icon_name="refresh",
            accessible_name="Refresh Inbox",
            glass=True,
        )
        self.refresh.setToolTip("Check Gmail for changes to your Inbox")
        row.addWidget(self.refresh)
        return header

    def _footer(self) -> QWidget:
        footer = QWidget()
        footer.setObjectName("inboxFooter")
        footer.setFixedHeight(BAR_HEIGHT)
        row = QHBoxLayout(footer)
        row.setContentsMargins(16, 0, 10, 0)
        row.setSpacing(12)
        self.range = QLabel()
        self.range.setObjectName("inboxRange")
        self.range.setProperty("tone", "secondary")
        self.range.setFont(ui_font(SMALL_PX, tabular=True))
        row.addWidget(self.range)
        self.wanted_status = WantedCount()
        row.addWidget(self.wanted_status, 1)
        pager = QHBoxLayout()
        pager.setSpacing(2)
        self.previous = outline_button(
            "",
            "previousInboxButton",
            icon_name="chevron-left",
            accessible_name="Previous 100",
            glass=True,
        )
        self.previous.setToolTip("Newer")
        self.next = outline_button(
            "",
            "nextInboxButton",
            icon_name="chevron-right",
            accessible_name="Next 100",
            glass=True,
        )
        self.next.setToolTip("Older")
        pager.addWidget(self.previous)
        pager.addWidget(self.next)
        row.addLayout(pager)
        return footer

    @property
    def page(self) -> InboxPage | None:
        """The page shown, if any."""
        return self._page

    def open_id(self) -> str | None:
        """The message in the reading pane, or None while the list has the page."""
        return self._open

    def mode(self) -> str:
        """The page's layout: "list" (its starting state), "split" (the list and the reading
        pane) or "reader" (the reading pane alone, on a page too narrow for both)."""
        if self._open is None:
            return "list"
        return "reader" if self._stacked() else "split"

    def _today(self) -> date:
        return self.clock().astimezone(self.zone).date()

    def clear(self, message: str = SIGN_IN) -> None:
        """Show no page, only ``message``."""
        self._page = None
        self._open = None
        self.messages.show_rows([])
        self.reader.show_message(None)
        self.title.set_count(None)
        self.range.clear()
        self.wanted_status.set_count(None)
        self.empty.set_message(message)
        self.stack.setCurrentWidget(self.empty)
        self.footer.hide()
        self._arrange()
        self.set_busy(False)

    def empty_message(self) -> str:
        """The text shown instead of the list, or empty while the list shows."""
        return self.empty.message.text() if self.stack.currentWidget() is self.empty else ""

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        page = self._page
        self.refresh.setEnabled(not busy)
        self.previous.setEnabled(not busy and page is not None and page.offset > 0)
        self.next.setEnabled(not busy and page is not None and page.has_more)
        self.reader.set_busy(busy)

    def show_page(self, page: InboxPage, *, select: str | None = None) -> None:
        """Show ``page``. With ``select``, a message the page holds, open it; otherwise the
        open message stays open while the page holds it, and the list has the page when it
        doesn't. Reloading the same page keeps the list where it was scrolled."""
        previous = self._page
        same = (
            previous is not None
            and previous.offset == page.offset
            and previous.account_email == page.account_email
        )
        scroll = self.messages.verticalScrollBar().value() if same else 0
        keep = self._open if select is None else select
        current = self.messages.current_message()
        ids = {message.provider_message_id for message in page.messages}
        self._page = page
        self._open = keep if keep in ids else None
        focus: str | None = self._open
        if focus is None and current is not None and same:
            focus = current.provider_message_id  # The list's place, without opening it.
        rows = build_inbox_rows(page.messages, page.wanted_ids, self.zone, self._today())
        self.messages.show_rows(rows, current=focus if focus in ids else None)
        self.title.set_count(max(page.total, page.offset + len(page.messages)))
        self.range.setText(range_text(page))
        self.wanted_status.set_count(page.wanted_recently)
        if page.messages:
            self.stack.setCurrentWidget(self.messages)
        else:
            self.empty.set_message(NO_MESSAGES)
            self.stack.setCurrentWidget(self.empty)
        self.footer.setVisible(bool(page.messages) or page.offset > 0)
        self._show_open()
        self._arrange()
        if same and scroll and self._open is None:
            self.messages.doItemsLayout()
            self.messages.verticalScrollBar().setValue(scroll)
        self.set_busy(self._busy)

    def set_zone(self, zone: ZoneInfo) -> None:
        """Show the page again in ``zone``: its sections and times move, and the open
        message and the list's place stay."""
        self.zone = zone
        if self._page is not None:
            self.show_page(self._page)

    def open_message(self, message_id: str) -> bool:
        """Open the message ``message_id`` in the reading pane; False when the page doesn't
        hold it."""
        if self._message(message_id) is None:
            return False
        self._open = message_id
        self.messages.set_current(message_id)
        self._show_open()
        self._arrange()
        return True

    def close_message(self) -> None:
        """Back to the list, with the message that was open still its current row."""
        if self._open is None:
            return
        # The window's focus, even while it isn't the active window.
        focused = self.window().focusWidget()
        moved = self.mode() == "reader" or (
            focused is not None and self.reader.isAncestorOf(focused)
        )
        self._open = None
        self.reader.show_message(None)
        self._arrange()
        if moved:  # Focus was in the pane that went: the list takes it.
            self.messages.setFocus(Qt.FocusReason.OtherFocusReason)

    def _message(self, message_id: str | None) -> NormalizedMessage | None:
        if self._page is None or message_id is None:
            return None
        return next((m for m in self._page.messages if m.provider_message_id == message_id), None)

    def _show_open(self) -> None:
        message = self._message(self._open)
        if message is None or self._page is None:
            self.reader.show_message(None)
            return
        wanted = message.provider_message_id in self._page.wanted_ids
        self.reader.show_message(message, wanted=wanted, zone=self.zone, today=self._today())

    def _open_row(self, message: NormalizedMessage) -> None:
        self.open_message(message.provider_message_id)

    def _follow_current(self, message: NormalizedMessage) -> None:
        """While a message is open, the reading pane follows the list's current row."""
        if self._open is not None and message.provider_message_id != self._open:
            self._open = message.provider_message_id
            self._show_open()

    def _stacked(self) -> bool:
        """Whether the page is too narrow for the list and the reading pane side by side."""
        return self.width() < LIST_MIN + READER_MIN + self.split.handleWidth()

    def _arrange(self) -> None:
        """Lay the page out for its mode: the list across it, the list beside the reading
        pane, or the reading pane alone. A splitter keeps a hidden pane's room, so every
        mode gives the room out itself."""
        mode = self.mode()
        self.list_column.setVisible(mode != "reader")
        self.reader.setVisible(mode != "list")
        self.messages.set_density(Density.COMPACT if mode == "split" else Density.WIDE)
        whole = self.width()
        if mode == "list":
            self.split.setSizes([whole, 0])
        elif mode == "reader":
            self.split.setSizes([0, whole])
        else:
            available = whole - self.split.handleWidth()
            width = self._list_width or round(whole * LIST_SHARE)
            width = max(LIST_MIN, min(width, LIST_MAX if self._list_width is None else width))
            width = min(width, available - READER_MIN)
            self.split.setSizes([width, available - width])

    def minimumSizeHint(self) -> QSize:
        """As narrow as the reading pane alone, which takes the page when it's narrower than
        the list and the pane side by side."""
        return QSize(READER_MIN, super().minimumSizeHint().height())

    def _divider_moved(self, position: int, index: int) -> None:
        if self.mode() == "split":
            self._list_width = self.split.sizes()[0]

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._arrange()  # The new width may change the mode, and the room is given out again.

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape and self._open is not None:
            self.close_message()
            event.accept()
            return
        super().keyPressEvent(event)

    def _previous_page(self) -> None:
        if self._page is not None:
            self.page_requested.emit(max(0, self._page.offset - INBOX_PAGE_SIZE))

    def _next_page(self) -> None:
        if self._page is not None:
            self.page_requested.emit(self._page.offset + INBOX_PAGE_SIZE)
