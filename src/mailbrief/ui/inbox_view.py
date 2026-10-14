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
    QLineEdit,
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


# Cached page size used when the command palette locates an older saved message.
INBOX_PAGE_SIZE: Final = 100


class InboxPanel(QWidget):
    """``wanted_requested(message_id, wanted)`` asks to mark or unmark the selected message."""

    refresh_requested = Signal()
    load_more_requested = Signal()
    search_requested = Signal(str)
    page_requested = Signal(int)
    source_requested = Signal(str)
    wanted_requested = Signal(str, bool)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("inboxPanel")
        self.setAccessibleName("Gmail Inbox")
        self.zone = ZoneInfo("UTC")
        self._page: InboxPage | None = None
        self._busy = False
        layout = QVBoxLayout(self)
        search_row = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setObjectName("inboxSearch")
        self.search.setAccessibleName("Search all Gmail mail")
        self.search.setPlaceholderText("Search all mail")
        self.search.setMaxLength(1000)
        self.search_button = outline_button("Search", "searchInboxButton")
        self.search_button.setAccessibleName("Search all mail in Gmail")
        self.clear_search = outline_button("Back to Inbox", "clearInboxSearchButton")
        self.clear_search.setAccessibleName("Clear mail search and return to Inbox")
        search_row.addWidget(self.search, 1)
        search_row.addWidget(self.search_button)
        search_row.addWidget(self.clear_search)
        layout.addLayout(search_row)
        self.status = wrap_label("Sign in with Google to load your Inbox.")
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
        self.previous.hide()  # Retained attribute for existing workspace integrations.
        self.next = outline_button("Load 100 more", "nextInboxButton")
        self.next.setAccessibleName("Load up to 100 more messages")
        self.source = outline_button("Open in Gmail", "inboxSourceButton")
        self.wanted = outline_button(WANT, "inboxWantedButton")
        for button in (self.refresh, self.next, self.source):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        mark_row = QHBoxLayout()
        mark_row.addWidget(self.wanted)
        mark_row.addStretch(1)
        layout.addLayout(mark_row)
        self.refresh.clicked.connect(self.refresh_requested.emit)
        self.next.clicked.connect(self.load_more_requested.emit)
        self.search.returnPressed.connect(self._search)
        self.search_button.clicked.connect(self._search)
        self.clear_search.clicked.connect(self._clear_search)
        self.search.textChanged.connect(self._update_search_controls)
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
        self.status.setText("Sign in with Google to load your Inbox.")
        self.search.clear()
        self.sync_status.clear()
        self.wanted_status.clear()
        self.refresh.setText(button_label("Refresh Inbox"))
        self.next.setText(button_label("Load 100 more"))
        self.next.setAccessibleName("Load up to 100 more messages")
        self.set_busy(False)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.refresh.setEnabled(not busy)
        self.next.setEnabled(not busy and self._page is not None and self._page.has_more)
        self.search.setEnabled(not busy)
        self.search_button.setEnabled(not busy)
        self.clear_search.setEnabled(not busy)
        self._update_search_controls()
        self.source.setEnabled(self.messages.currentRow() >= 0)
        self.wanted.setEnabled(not busy and self.messages.currentRow() >= 0)

    def show_page(self, page: InboxPage, *, select: str | None = None) -> None:
        """Show ``page``, selecting the message ``select`` when it is there, else the first."""
        if select is None and self._page is not None:
            index = self.messages.currentRow()
            if 0 <= index < len(self._page.messages):
                select = self._page.messages[index].provider_message_id
        scroll = self.messages.verticalScrollBar().value()
        busy = self._busy
        previous_query = self._page.query if self._page is not None else ""
        self._page = page
        if previous_query != page.query:
            self.search.setText(page.query)
        self.messages.blockSignals(True)
        self.messages.clear()
        for message in page.messages:
            wanted = message.provider_message_id in page.wanted_ids
            text = f"{message.sender.address} — {message.subject or '(no subject)'}"
            item = QListWidgetItem(f"{text} — {WANTED}" if wanted else text)
            chips: tuple[Chip, ...] = (Chip(WANTED, ChipTone.ACCENT),) if wanted else ()
            if page.query and not message.is_in_inbox:
                chips += (Chip("Outside Inbox", ChipTone.ACCENT),)
            item.setData(
                ROW_ROLE,
                ActionRow(message.subject or "(no subject)", message.sender.address, chips, False),
            )
            self.messages.addItem(item)
        self.status.setText(
            f"{page.account_email} · {'All-mail search' if page.query else 'Inbox'} · "
            f"{len(page.messages)} {'message' if len(page.messages) == 1 else 'messages'} loaded. "
            + (
                "Showing saved metadata. Refresh checks Gmail."
                if page.cached
                else "Older saved entries may be stale."
                if page.has_more
                else "End of available results; saved entries may be stale."
            )
        )
        self.refresh.setText(button_label("Refresh search" if page.query else "Refresh Inbox"))
        self.next.setText(button_label("Retry page" if page.error_code else "Load 100 more"))
        self.next.setAccessibleName(
            "Retry the current message page" if page.error_code else "Load up to 100 more messages"
        )
        self.wanted_status.setText(wanted_line(page.wanted_recently))
        if page.messages:
            ids = [message.provider_message_id for message in page.messages]
            self.messages.setCurrentRow(ids.index(select) if select in ids else 0)
        else:
            self.details.setText(
                "No matching messages." if page.query else "No Inbox messages loaded."
            )
        self.messages.blockSignals(False)
        if page.messages:
            self._select(self.messages.currentRow())
        self.messages.verticalScrollBar().setValue(scroll)
        self.set_busy(busy)

    def _search(self) -> None:
        self.search_requested.emit(self.search.text().strip())

    def _update_search_controls(self) -> None:
        self.clear_search.setVisible(
            bool(self.search.text().strip()) or (self._page is not None and bool(self._page.query))
        )

    def _clear_search(self) -> None:
        self.search.clear()
        self.search_requested.emit("")

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
        self.wanted.setEnabled(not self._busy)

    def _toggle_wanted(self) -> None:
        index = self.messages.currentRow()
        if self._page is not None and 0 <= index < len(self._page.messages):
            message_id = self._page.messages[index].provider_message_id
            self.wanted_requested.emit(message_id, message_id not in self._page.wanted_ids)

    def _open_source(self) -> None:
        index = self.messages.currentRow()
        if self._page is not None and 0 <= index < len(self._page.messages):
            self.source_requested.emit(str(self._page.messages[index].web_link))
