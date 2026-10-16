"""Read-only cached mail browsing; no provider calls or body downloads."""

from collections.abc import Callable
from datetime import date, datetime
from zoneinfo import ZoneInfo

from PySide6.QtCore import QDate, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.cached_mail import CachedAccount, CachedMailPage
from mailbrief.ui.brief_list import sender_with_address
from mailbrief.ui.deadline_text import day_text, moment_text
from mailbrief.ui.hairline import HairlineSplitter
from mailbrief.ui.labels import outline_button, plain_label, wrap_label
from mailbrief.ui.list_rows import DISPLAY_ROW_ROLE, DisplayRow, DisplayRowDelegate
from mailbrief.ui.theme import current_tokens, icon_pixmap, ui_font
from mailbrief.ui.weak import weak_method


class CachedMailDialog(QDialog):
    page_requested = Signal(int, object, int)

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFont(ui_font(10))
        self.setObjectName("savedMailDialog")
        self.setAccessibleName("Saved mail metadata")
        self.setWindowTitle("Saved mail metadata")
        self.resize(850, 620)
        self._page: CachedMailPage | None = None
        # The owner's zone, for today's date and the last sync time; the window sets it.
        self.zone: ZoneInfo | None = None
        # Today's date in that zone, as the picker opens on it; a test injects a clock.
        self.today: Callable[[], date] = weak_method(self._local_today)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        header = QWidget()
        header.setObjectName("savedMailHeader")
        header.setAccessibleName("Saved mail")
        header.setFixedHeight(70)
        header_row = QHBoxLayout(header)
        header_row.setContentsMargins(18, 14, 18, 14)
        symbol = plain_label("", px=16)
        symbol.setObjectName("savedMailIcon")
        symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        symbol.setPixmap(icon_pixmap("bookmark", current_tokens().primary, 18, 2.0))
        symbol.setFixedSize(34, 34)
        header_row.addWidget(symbol)
        headings = QVBoxLayout()
        headings.setSpacing(3)
        headings.addWidget(plain_label("Saved mail", px=16))
        headings.addWidget(
            plain_label("Browse locally saved message previews and metadata.", tone="muted", px=9)
        )
        header_row.addLayout(headings, 1)
        dismiss = outline_button("×", "savedMailDismiss")
        dismiss.setAccessibleName("Close saved mail")
        dismiss.setProperty("variant", "ghost")
        dismiss.clicked.connect(self.reject)
        header_row.addWidget(dismiss)
        layout.addWidget(header)
        self.splitter = HairlineSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        list_pane = QWidget()
        list_pane.setObjectName("cachedListPane")
        list_pane.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        left = QVBoxLayout(list_pane)
        left.setContentsMargins(14, 9, 14, 9)
        left.setSpacing(10)
        self.search = QLineEdit()
        self.search.setObjectName("cachedMailSearch")
        self.search.setAccessibleName("Search saved mail on this page")
        self.search.setPlaceholderText("Search saved mail")
        self.search.textChanged.connect(self._filter)
        left.addWidget(self.search)
        filters = QHBoxLayout()
        self.accounts = QComboBox()
        self.accounts.setAccessibleName("Cached Gmail account")
        self.day = QDateEdit()
        self.day.setAccessibleName("Received date in your time zone")
        self.day.setCalendarPopup(True)
        self.day.setDisplayFormat("yyyy-MM-dd")
        filters.addWidget(self.accounts, 1)
        filters.addWidget(self.day)
        filters.insertStretch(2, 1)
        filters.addWidget(self.search, 1)
        filters.setContentsMargins(16, 10, 16, 10)
        layout.addLayout(filters)
        self.status = QLabel("Choose a cached account and date.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.status.setObjectName("cachedMailStatus")
        self.status.setProperty("tone", "muted")
        self.status.setFont(ui_font(10))
        left.addWidget(self.status)
        self.messages = QListWidget()
        self.messages.setAccessibleName("Cached message metadata")
        self.messages.setObjectName("cachedMailList")
        self.messages.setItemDelegate(DisplayRowDelegate(self.messages))
        self.messages.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left.addWidget(self.messages, 1)
        detail_pane = QWidget()
        detail_pane.setObjectName("cachedDetailPane")
        right = QVBoxLayout(detail_pane)
        right.setContentsMargins(24, 24, 24, 24)
        right.setSpacing(16)
        self.received = plain_label("", tone="muted", px=9)
        self.received.setObjectName("cachedReceived")
        right.addWidget(self.received)
        self.message_title = wrap_label("Select a message", px=18, medium=True)
        self.message_title.setObjectName("cachedMessageTitle")
        right.addWidget(self.message_title)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName("Selected cached message details")
        self.details.setObjectName("cachedMessageDetails")
        self.details.setFont(ui_font(11))
        self.details.setFixedHeight(78)
        self.saved_sender = wrap_label("", px=10, medium=True)
        self.saved_received = wrap_label("", px=10, medium=True)
        self.saved_inbox = plain_label("", px=10, medium=True)
        self.saved_sender.setObjectName("savedSender")
        self.saved_received.setObjectName("savedReceived")
        self.saved_inbox.setObjectName("savedInbox")
        metadata = QFormLayout()
        metadata.setVerticalSpacing(12)
        metadata.addRow(plain_label("From", tone="muted", px=9), self.saved_sender)
        metadata.addRow(plain_label("Received", tone="muted", px=9), self.saved_received)
        metadata.addRow(plain_label("Inbox at last sync", tone="muted", px=9), self.saved_inbox)
        right.addLayout(metadata)
        right.addWidget(self.details)
        self.details.setObjectName("savedPreviewText")
        right.addStretch(1)
        navigation = QHBoxLayout()
        self.previous = outline_button("&Previous", "cachedPrevious", mnemonic=True)
        self.next = outline_button("&Next", "cachedNext", mnemonic=True)
        self.source = outline_button(
            "Open in &Gmail", "cachedSource", mnemonic=True, primary=True, icon_name="external-link"
        )
        close = outline_button("&Done", "cachedClose", mnemonic=True, primary=True)
        for button in (self.previous, self.next):
            navigation.addWidget(button)
        left.addLayout(navigation)
        self.source.setProperty("variant", "primary")
        self.source.setText("Open source in &Gmail")
        self.source.setProperty("variant", "outline")
        list_pane.setMinimumWidth(260)
        detail_pane.setMinimumWidth(300)
        self.splitter.addWidget(list_pane)
        self.splitter.addWidget(detail_pane)
        self.splitter.setSizes([350, 525])
        layout.addWidget(self.splitter, 1)
        footer_widget = QWidget()
        footer_widget.setObjectName("savedMailFooter")
        footer_widget.setAccessibleName("Saved mail controls")
        footer_widget.setFixedHeight(58)
        footer = QHBoxLayout(footer_widget)
        footer.setContentsMargins(18, 9, 14, 9)
        footer.addWidget(
            plain_label("Saved previews may be incomplete or out of date.", tone="muted", px=10),
            1,
        )
        footer.addWidget(self.source)
        footer.addWidget(close)
        layout.addWidget(footer_widget)
        close.clicked.connect(self.reject)
        self.accounts.currentIndexChanged.connect(lambda: self.request_page(0))
        self.day.dateChanged.connect(lambda: self.request_page(0))
        self.previous.clicked.connect(
            lambda: self.request_page(max(0, self._page.offset - 100) if self._page else 0)
        )
        self.next.clicked.connect(
            lambda: self.request_page(self._page.offset + 100 if self._page else 0)
        )
        self.messages.currentRowChanged.connect(self._show_message)
        self.source.clicked.connect(self._open_source)
        self.set_busy(False)
        self.show_today()

    def _local_today(self) -> date:
        return datetime.now(self.zone).date()

    def show_today(self) -> None:
        """Set the picker to today in the owner's zone, whatever the computer's zone; the
        caller loads the page, so no page is requested here."""
        today = self.today()
        self.day.blockSignals(True)
        self.day.setDate(QDate(today.year, today.month, today.day))
        self.day.blockSignals(False)

    def configure(self, accounts: tuple[CachedAccount, ...]) -> None:
        self.accounts.blockSignals(True)
        self.accounts.clear()
        for account in accounts:
            self.accounts.addItem(account.email_address, account.account_id)
        self.accounts.blockSignals(False)
        self.begin_load()
        if not accounts:
            self.status.setText("No cached Gmail accounts yet. Connect and sync to save metadata.")

    @property
    def has_page(self) -> bool:
        return self._page is not None

    def selection(self) -> tuple[int, date] | None:
        identifier = self.accounts.currentData()
        if not isinstance(identifier, int):
            return None
        value = self.day.date()
        return identifier, date(value.year(), value.month(), value.day())

    def request_page(self, offset: int) -> None:
        selected = self.selection()
        if selected is not None:
            self.page_requested.emit(selected[0], selected[1], offset)

    def begin_load(self) -> None:
        self._page = None
        self.messages.clear()
        self.details.clear()
        self._clear_message()
        self.status.setText("Loading saved metadata…")
        self.source.setEnabled(False)

    def set_busy(self, busy: bool) -> None:
        self.accounts.setEnabled(not busy)
        self.day.setEnabled(not busy)
        self.previous.setEnabled(not busy and self._page is not None and self._page.offset > 0)
        self.next.setEnabled(not busy and self._page is not None and self._page.has_more)

    def show_page(self, page: CachedMailPage) -> None:
        self._page = page
        last = page.account.last_sync_at_utc
        today = self.today()
        zone = self.zone or ZoneInfo(page.timezone_name)
        stamp = moment_text(last, zone, today=today) if last else "none recorded"
        status = (
            f"{day_text(page.local_date, today=today)} ({page.timezone_name}) · "
            f"{len(page.messages)} saved messages on this page. "
            f"Account last complete sync: {stamp}. "
            "Cached metadata may be stale; browsing does not use the network."
        )
        cache_time = f"Cached at {stamp}" if last else "No complete sync recorded"
        self.status.setText(f"{len(page.messages)} messages saved · {cache_time}")
        self.status.setAccessibleName(status)
        self.messages.clear()
        for message in page.messages:
            item = QListWidgetItem(
                f"{message.subject}\n{message.sender.name or message.sender.address}\n"
                f"{message.body_preview}"
            )
            item.setData(
                DISPLAY_ROW_ROLE,
                DisplayRow(
                    message.subject or "(no subject)",
                    message.sender.name or message.sender.address,
                    "",
                    unread=False,
                ),
            )
            self.messages.addItem(item)
        if page.messages:
            self.messages.setCurrentRow(0)
        else:
            self.details.setPlainText(
                "No cached messages for this date. This is not a live Inbox check."
            )
        self.set_busy(False)
        self._filter(self.search.text())

    def _filter(self, text: str) -> None:
        """Search the loaded metadata page without a provider call or body download."""
        query = text.casefold().strip()
        first: int | None = None
        for row in range(self.messages.count()):
            item = self.messages.item(row)
            if item is not None:
                visible = query in item.text().casefold()
                item.setHidden(not visible)
                if visible and first is None:
                    first = row
        selected = self.messages.currentItem()
        if selected is None or selected.isHidden():
            self.messages.setCurrentRow(-1 if first is None else first)

    def _show_message(self, index: int) -> None:
        self.source.setEnabled(False)
        self._clear_message()
        if self._page is None or not 0 <= index < len(self._page.messages):
            self.details.clear()
            return
        message = self._page.messages[index]
        self.message_title.setText(message.subject or "(no subject)")
        received = moment_text(
            message.received_at_utc, ZoneInfo(self._page.timezone_name), today=self.today()
        )
        self.received.setText("SAVED PREVIEW")
        self.saved_sender.setText(sender_with_address(message.sender))
        self.saved_received.setText(received)
        self.saved_inbox.setText("Yes" if message.is_in_inbox else "No")
        self.details.setPlainText(message.body_preview or "No saved preview for this message.")
        self.source.setEnabled(
            message.web_link.scheme == "https" and message.web_link.host == "mail.google.com"
        )

    def _clear_message(self) -> None:
        self.message_title.setText("Select a message")
        self.received.clear()
        self.saved_sender.clear()
        self.saved_received.clear()
        self.saved_inbox.clear()

    def _open_source(self) -> None:
        index = self.messages.currentRow()
        if self._page is not None and 0 <= index < len(self._page.messages):
            link = self._page.messages[index].web_link
            if link.scheme == "https" and link.host == "mail.google.com":
                QDesktopServices.openUrl(QUrl(str(link)))
