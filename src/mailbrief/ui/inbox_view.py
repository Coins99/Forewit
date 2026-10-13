"""Current Inbox metadata across all received dates; no bodies or AI calls."""

from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.cached_mail import InboxPage
from mailbrief.ui.actions_view import ActionRow, ActionRowDelegate
from mailbrief.ui.brief_list import ROW_ROLE
from mailbrief.ui.deadline_text import moment_text
from mailbrief.ui.hairline import HairlineSplitter
from mailbrief.ui.labels import outline_button, wrap_label

# Messages per Inbox page; Previous and Next move by one page.
INBOX_PAGE_SIZE: Final = 100


class InboxPanel(QWidget):
    refresh_requested = Signal()
    page_requested = Signal(int)
    source_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("inboxPanel")
        self.setAccessibleName("Gmail Inbox")
        self.zone = ZoneInfo("UTC")
        self._page: InboxPage | None = None
        layout = QVBoxLayout(self)
        self.status = wrap_label("Sign in with Google to sync your full Inbox.")
        self.status.setObjectName("inboxStatus")
        layout.addWidget(self.status)
        self.sync_status = wrap_label("", tone="secondary")
        self.sync_status.setObjectName("inboxSyncStatus")
        layout.addWidget(self.sync_status)
        split = HairlineSplitter()
        self.messages = QListWidget()
        self.messages.setObjectName("inboxMessages")
        self.messages.setAccessibleName("Inbox message metadata")
        self.messages.setMinimumWidth(220)
        self.messages.setFrameShape(QFrame.Shape.NoFrame)
        self.messages.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.messages.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.messages.setItemDelegate(ActionRowDelegate(self.messages))
        self.details = wrap_label("")
        self.details.setObjectName("inboxDetails")
        self.details.setAccessibleName("Selected Inbox message details")
        split.addWidget(self.messages)
        detail_scroll = QScrollArea()
        detail_scroll.setObjectName("inboxDetailScroll")
        detail_scroll.setAccessibleName("Inbox message details")
        detail_scroll.setMinimumWidth(280)
        detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        detail_scroll.setWidgetResizable(True)
        content = QWidget()
        details_layout = QVBoxLayout(content)
        details_layout.addWidget(self.details)
        details_layout.addStretch(1)
        detail_scroll.setWidget(content)
        split.addWidget(detail_scroll)
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 5)
        split.setSizes([400, 500])
        layout.addWidget(split, 1)
        buttons = QHBoxLayout()
        self.refresh = outline_button("Refresh Inbox", "refreshInboxButton")
        self.previous = outline_button("Previous 100", "previousInboxButton")
        self.next = outline_button("Next 100", "nextInboxButton")
        self.source = outline_button("Open in Gmail", "inboxSourceButton")
        for button in (self.refresh, self.previous, self.next, self.source):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.refresh.clicked.connect(self.refresh_requested.emit)
        self.previous.clicked.connect(
            lambda: self.page_requested.emit(max(0, self._page.offset - 100) if self._page else 0)
        )
        self.next.clicked.connect(
            lambda: self.page_requested.emit(self._page.offset + 100 if self._page else 0)
        )
        self.messages.currentRowChanged.connect(self._select)
        self.source.clicked.connect(self._open_source)
        self.clear()

    def clear(self) -> None:
        self._page = None
        self.messages.clear()
        self.details.clear()
        self.status.setText("Sign in with Google to sync your full Inbox.")
        self.sync_status.clear()
        self.set_busy(False)

    def set_busy(self, busy: bool) -> None:
        self.refresh.setEnabled(not busy)
        self.previous.setEnabled(not busy and self._page is not None and self._page.offset > 0)
        self.next.setEnabled(not busy and self._page is not None and self._page.has_more)
        self.source.setEnabled(not busy and self.messages.currentRow() >= 0)

    def show_page(self, page: InboxPage) -> None:
        self._page = page
        self.messages.clear()
        for message in page.messages:
            item = QListWidgetItem(
                f"{message.sender.address} — {message.subject or '(no subject)'}"
            )
            item.setData(
                ROW_ROLE,
                ActionRow(message.subject or "(no subject)", message.sender.address, (), False),
            )
            self.messages.addItem(item)
        self.status.setText(
            f"{page.account_email} · Inbox across all dates · "
            f"{len(page.messages)} {'message' if len(page.messages) == 1 else 'messages'} "
            "on this page. Cached metadata; Refresh checks Gmail."
        )
        if page.messages:
            self.messages.setCurrentRow(0)
        else:
            self.details.setText("No messages in the cached Inbox.")
        self.set_busy(False)

    def select_message(self, provider_message_id: str) -> bool:
        """Select the message on the page shown; False when it isn't there."""
        if self._page is None:
            return False
        for index, message in enumerate(self._page.messages):
            if message.provider_message_id == provider_message_id:
                self.messages.setCurrentRow(index)
                return True
        return False

    def set_zone(self, zone: ZoneInfo) -> None:
        """Redraw the current detail without changing the selected row or page."""
        self.zone = zone
        self._select(self.messages.currentRow())

    def _select(self, index: int) -> None:
        self.source.setEnabled(False)
        if self._page is None or not 0 <= index < len(self._page.messages):
            self.details.clear()
            return
        message = self._page.messages[index]
        received = moment_text(
            message.received_at_utc, self.zone, today=datetime.now(self.zone).date()
        )
        self.details.setText(
            f"From: {message.sender.address}\nSubject: {message.subject}\nReceived: {received}\n\n"
            f"Saved preview:\n{message.body_preview}"
        )
        self.source.setEnabled(True)

    def _open_source(self) -> None:
        index = self.messages.currentRow()
        if self._page is not None and 0 <= index < len(self._page.messages):
            self.source_requested.emit(str(self._page.messages[index].web_link))
