"""Current Inbox metadata across all received dates; no bodies or AI calls.

**Should have been in my brief** marks a message the owner wanted (ADR 0020): ranking then
counts it for its sender. The mark is local; Gmail is never told.
"""

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

from mailbrief.domain.cached_mail import WANTED_WINDOW_DAYS, InboxPage
from mailbrief.ui.actions_view import ActionRow, ActionRowDelegate
from mailbrief.ui.brief_list import ROW_ROLE, Chip, ChipTone
from mailbrief.ui.deadline_text import moment_text
from mailbrief.ui.hairline import HairlineSplitter
from mailbrief.ui.labels import button_label, outline_button, wrap_label

WANT: Final = "Should have been in my brief"
UNWANT: Final = "Remove mark"
WANTED: Final = "Wanted in brief"
WANTED_NOTE: Final = (
    "You marked this as one that should have been in your brief. MailBrief now ranks this "
    "sender's mail higher, unless you've left out more of it than you wanted."
)


def wanted_line(count: int) -> str:
    """How many wanted messages the review didn't suggest lately, for the owner's journal."""
    return (
        f"Emails you wanted that weren't suggested, last {WANTED_WINDOW_DAYS} days: {count}. "
        "That counts ones you picked yourself in a review and ones marked here."
    )


class InboxPanel(QWidget):
    """``wanted_requested(message_id, wanted)`` asks to mark or unmark the selected message."""

    refresh_requested = Signal()
    page_requested = Signal(int)
    source_requested = Signal(str)
    wanted_requested = Signal(str, bool)

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
        self.wanted_status = wrap_label("", tone="secondary")
        self.wanted_status.setObjectName("inboxWantedStatus")
        self.wanted_status.setAccessibleName("Wanted emails the review didn't suggest")
        layout.addWidget(self.wanted_status)
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
        self.wanted = outline_button(WANT, "inboxWantedButton")
        for button in (self.refresh, self.previous, self.next, self.source, self.wanted):
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
        self.wanted.clicked.connect(self._toggle_wanted)
        self.clear()

    @property
    def page(self) -> InboxPage | None:
        """The page shown, if any."""
        return self._page

    def clear(self) -> None:
        self._page = None
        self.messages.clear()
        self.details.clear()
        self.status.setText("Sign in with Google to sync your full Inbox.")
        self.sync_status.clear()
        self.wanted_status.clear()
        self.set_busy(False)

    def set_busy(self, busy: bool) -> None:
        self.refresh.setEnabled(not busy)
        self.previous.setEnabled(not busy and self._page is not None and self._page.offset > 0)
        self.next.setEnabled(not busy and self._page is not None and self._page.has_more)
        self.source.setEnabled(not busy and self.messages.currentRow() >= 0)
        self.wanted.setEnabled(not busy and self.messages.currentRow() >= 0)

    def show_page(self, page: InboxPage, *, select: str | None = None) -> None:
        """Show ``page``, selecting the message ``select`` when it is there, else the first."""
        self._page = page
        self.messages.clear()
        for message in page.messages:
            wanted = message.provider_message_id in page.wanted_ids
            text = f"{message.sender.address} — {message.subject or '(no subject)'}"
            item = QListWidgetItem(f"{text} — {WANTED}" if wanted else text)
            chips = (Chip(WANTED, ChipTone.ACCENT),) if wanted else ()
            item.setData(
                ROW_ROLE,
                ActionRow(message.subject or "(no subject)", message.sender.address, chips, False),
            )
            self.messages.addItem(item)
        self.status.setText(
            f"{page.account_email} · Inbox across all dates · "
            f"{len(page.messages)} {'message' if len(page.messages) == 1 else 'messages'} "
            "on this page. Cached metadata; Refresh checks Gmail."
        )
        self.wanted_status.setText(wanted_line(page.wanted_recently))
        if page.messages:
            ids = [message.provider_message_id for message in page.messages]
            self.messages.setCurrentRow(ids.index(select) if select in ids else 0)
        else:
            self.details.setText("No messages in the cached Inbox.")
        self.set_busy(False)

    def set_zone(self, zone: ZoneInfo) -> None:
        """Redraw the current detail without changing the selected row or page."""
        self.zone = zone
        self._select(self.messages.currentRow())

    def _select(self, index: int) -> None:
        self.source.setEnabled(False)
        self.wanted.setEnabled(False)
        if self._page is None or not 0 <= index < len(self._page.messages):
            self.details.clear()
            return
        message = self._page.messages[index]
        received = moment_text(
            message.received_at_utc, self.zone, today=datetime.now(self.zone).date()
        )
        wanted = message.provider_message_id in self._page.wanted_ids
        self.details.setText(
            f"From: {message.sender.address}\nSubject: {message.subject}\nReceived: {received}\n\n"
            + (f"{WANTED_NOTE}\n\n" if wanted else "")
            + f"Saved preview:\n{message.body_preview}"
        )
        self.wanted.setText(button_label(UNWANT if wanted else WANT))
        self.wanted.setAccessibleName(
            "Remove the should-have-been-in-my-brief mark" if wanted else WANT
        )
        self.source.setEnabled(True)
        self.wanted.setEnabled(True)

    def _toggle_wanted(self) -> None:
        index = self.messages.currentRow()
        if self._page is not None and 0 <= index < len(self._page.messages):
            message_id = self._page.messages[index].provider_message_id
            self.wanted_requested.emit(message_id, message_id not in self._page.wanted_ids)

    def _open_source(self) -> None:
        index = self.messages.currentRow()
        if self._page is not None and 0 <= index < len(self._page.messages):
            self.source_requested.emit(str(self._page.messages[index].web_link))
