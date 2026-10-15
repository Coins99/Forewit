"""The Inbox's reading pane: one message's subject, sender and Gmail's short preview, with
its actions pinned at the bottom. No toolbar: the subject heads the pane, and Close (or
Escape) goes back to the list.

The content sits in one column at most ``COLUMN_MAX`` wide, centred, so a line never runs
the width of a wide window. Mail text reaches the screen only through ``wrap_label`` and
the monogram's ``drawText``: plain text, never a link or a tooltip.
"""

from datetime import date
from typing import Final
from zoneinfo import ZoneInfo

from PySide6.QtCore import QEvent, QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPaintEvent, QResizeEvent
from PySide6.QtWidgets import (
    QBoxLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.messages import EmailContact, NormalizedMessage
from mailbrief.ui.brief_list import NO_SUBJECT
from mailbrief.ui.deadline_text import moment_text
from mailbrief.ui.hairline import HairlineDivider, HairlineFrame
from mailbrief.ui.inbox_list import WANTED
from mailbrief.ui.labels import button_label, outline_button, plain_label, wrap_label
from mailbrief.ui.theme import (
    HEADING_PX,
    READING_PX,
    SMALL_PX,
    TEXT_PX,
    current_tokens,
    follow_theme,
    icon_pixmap,
    ui_font,
)

WANT: Final = "Should have been in my brief"
UNWANT: Final = "Remove mark"
WANTED_NOTE: Final = (
    "You marked this as one that should have been in your brief. MailBrief now ranks this "
    "sender's mail higher, unless you've left out more of it than you wanted."
)
PREVIEW_NOTE: Final = "Gmail's short preview. Open in Gmail to read the whole email."
NO_PREVIEW: Final = "Gmail has no preview of this email."

# The reading column's widest, and the room round it (logical px).
COLUMN_MAX: Final = 640
SIDE_MARGIN: Final = 24
TOP_MARGIN: Final = 14
# The subject's first line sits this far down, so it centres on the Close circle beside it.
SUBJECT_DROP: Final = 6
MONOGRAM_PX: Final = 36
# Reading text's line height, a multiple of its size.
READING_LINE: Final = 1.5
_FLAG_PX: Final = 16


def shown_name(contact: EmailContact) -> str | None:
    """The sender's name to show beside the address, or None when there is none worth
    showing: empty, the address itself, or holding "@" (a name that looks like an address
    is how a sender poses as another, so the address is shown alone)."""
    name = " ".join((contact.name or "").split())
    if not name or name.casefold() == contact.address.casefold() or "@" in name:
        return None
    return name


def initials(text: str) -> str:
    """Up to two capitals for a monogram: the first letter or digit of each of a name's
    first two words ("Alex Chen" is "AC"), or of an address's local part ("A"); empty when
    there is none."""
    if "@" in text:
        text = text.split("@", 1)[0]
    letters = [next((char for char in word if char.isalnum()), "") for word in text.split()]
    return "".join(letter for letter in letters if letter)[:2].upper()[:2]


class Monogram(QWidget):
    """A sender's initials on a grey circle, as Apple Mail shows a sender without a photo.
    The initials are drawn with ``drawText``."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("inboxMonogram")
        self.setFixedSize(MONOGRAM_PX, MONOGRAM_PX)
        self._initials = ""

    def initials(self) -> str:
        return self._initials

    def set_initials(self, text: str) -> None:
        self._initials = text
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        tokens = current_tokens()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(tokens.selection))
        painter.drawEllipse(QRectF(self.rect()))
        painter.setFont(ui_font(TEXT_PX, semibold=True))
        painter.setPen(QColor(tokens.text_secondary))
        painter.drawText(self.rect(), int(Qt.AlignmentFlag.AlignCenter), self._initials)
        painter.end()


class InboxReader(QWidget):
    """``close_requested`` from Close; ``source_requested(link)`` from Open in Gmail;
    ``wanted_requested(message_id, wanted)`` from the mark button. It never opens a link
    itself."""

    close_requested = Signal()
    source_requested = Signal(str)
    wanted_requested = Signal(str, bool)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("inboxReader")
        self.setAccessibleName("Open message")
        self._message: NormalizedMessage | None = None
        self._wanted = False
        self._busy = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        # Its least width is minimumSizeHint's, not the actions' side by side: they stack
        # when the pane is narrower (_fit_actions).
        outer.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.body = QScrollArea()
        self.body.setObjectName("inboxReaderScroll")
        self.body.setAccessibleName("Message")
        self.body.setFrameShape(QFrame.Shape.NoFrame)
        self.body.setWidgetResizable(True)
        self.body.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Tab goes from the list to the buttons; a click still lets the arrows scroll.
        self.body.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        content = QWidget()
        content.setObjectName("inboxReaderContent")
        self._content = QVBoxLayout(content)
        self._content.setSpacing(0)
        top = QHBoxLayout()
        top.setSpacing(12)
        self.subject = wrap_label(px=HEADING_PX, semibold=True)
        self.subject.setObjectName("inboxSubject")
        self.subject.setContentsMargins(0, SUBJECT_DROP, 0, 0)
        top.addWidget(self.subject, 1, Qt.AlignmentFlag.AlignTop)
        self.close_button = outline_button(
            "", "inboxCloseButton", icon_name="x", accessible_name="Close message", glass=True
        )
        self.close_button.setToolTip("Close (Esc)")
        top.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignTop)
        self._content.addLayout(top)
        self._content.addSpacing(14)
        sender = QHBoxLayout()
        sender.setSpacing(12)
        self.monogram = Monogram()
        sender.addWidget(self.monogram, 0, Qt.AlignmentFlag.AlignTop)
        names = QVBoxLayout()
        names.setSpacing(2)
        self.from_line = wrap_label(px=TEXT_PX, medium=True)
        self.from_line.setObjectName("inboxSender")
        names.addWidget(self.from_line)
        self.meta = wrap_label(tone="secondary", px=SMALL_PX)
        self.meta.setObjectName("inboxMeta")
        names.addWidget(self.meta)
        sender.addLayout(names, 1)
        self._content.addLayout(sender)
        self._content.addSpacing(20)
        self.preview = wrap_label(px=READING_PX, line_height=READING_LINE)
        self.preview.setObjectName("inboxPreview")
        self._content.addWidget(self.preview)
        self._content.addSpacing(8)
        self.preview_note = plain_label(PREVIEW_NOTE, tone="secondary", px=SMALL_PX)
        self.preview_note.setObjectName("inboxPreviewNote")
        self._content.addWidget(self.preview_note)
        self._content.addSpacing(20)
        self.wanted_card = self._wanted_card()
        self._content.addWidget(self.wanted_card)
        self._content.addStretch(1)
        self.body.setWidget(content)
        # The column re-centres when the scroll bar comes or goes, too.
        self.body.viewport().installEventFilter(self)
        outer.addWidget(self.body, 1)
        footer = QWidget()
        footer.setObjectName("inboxReaderFooter")
        self._footer = QVBoxLayout(footer)
        self._footer.setSpacing(10)
        self._footer.addWidget(HairlineDivider())
        self._actions = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self._actions.setSpacing(8)
        self.source = outline_button(
            "Open in Gmail", "inboxSourceButton", icon_name="external-link", glass=True
        )
        self.wanted = outline_button(WANT, "inboxWantedButton", glass=True)
        # Left-aligned, so stacked they keep their size rather than fill the column.
        self._actions.addWidget(self.source, 0, Qt.AlignmentFlag.AlignLeft)
        self._actions.addWidget(self.wanted, 0, Qt.AlignmentFlag.AlignLeft)
        self._actions.addStretch(1)
        self._footer.addLayout(self._actions)
        outer.addWidget(footer)
        self.close_button.clicked.connect(self.close_requested)
        self.source.clicked.connect(self._open_source)
        self.wanted.clicked.connect(self._toggle_wanted)
        self._place_column()
        self.show_message(None)

    def _wanted_card(self) -> HairlineFrame:
        card = HairlineFrame()
        card.setObjectName("inboxWantedCard")
        card.setAccessibleName(WANTED)
        row = QHBoxLayout(card)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(10)
        flag = QLabel()
        flag.setObjectName("inboxWantedFlag")
        follow_theme(
            flag,
            lambda label: label.setPixmap(
                icon_pixmap("flag", current_tokens().warning_fg, _FLAG_PX, 2.0)
            ),
        )
        row.addWidget(flag, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(4)
        title = plain_label(WANTED, px=TEXT_PX, medium=True)
        title.setObjectName("inboxWantedTitle")
        text.addWidget(title)
        self.wanted_note = plain_label(WANTED_NOTE, tone="secondary", px=SMALL_PX)
        self.wanted_note.setObjectName("inboxWantedNote")
        text.addWidget(self.wanted_note)
        row.addLayout(text, 1)
        return card

    def message(self) -> NormalizedMessage | None:
        return self._message

    def show_message(
        self,
        message: NormalizedMessage | None,
        *,
        wanted: bool = False,
        zone: ZoneInfo | None = None,
        today: date | None = None,
    ) -> None:
        """Show ``message`` (None clears the pane), its time in ``zone`` as seen on
        ``today``. Another message starts at the top."""
        changed = (
            message is None
            or self._message is None
            or message.provider_message_id != self._message.provider_message_id
        )
        self._message = message
        self._wanted = wanted
        if message is None:
            for label in (self.subject, self.from_line, self.meta, self.preview):
                label.setText("")
            self.monogram.set_initials("")
            self.wanted_card.hide()
        else:
            zone = zone or ZoneInfo("UTC")
            when = moment_text(message.received_at_utc, zone, today=today or date.today())
            self.subject.setText(message.subject or NO_SUBJECT)
            name = shown_name(message.sender)
            address = message.sender.address
            self.from_line.setText(name or address)
            self.meta.setText(when if name is None else f"{address} · {when}")
            self.monogram.set_initials(initials(name or address))
            preview = " ".join(message.body_preview.split())
            self.preview.setText(preview or NO_PREVIEW)
            self.preview.setProperty("tone", None if preview else "secondary")
            self.preview.style().unpolish(self.preview)
            self.preview.style().polish(self.preview)
            self.wanted_card.setVisible(wanted)
        self.wanted.setText(button_label(UNWANT if wanted else WANT))
        self.wanted.setAccessibleName(
            "Remove the should-have-been-in-my-brief mark" if wanted else WANT
        )
        self._update_buttons()
        self._fit_actions()
        if changed:
            self.body.verticalScrollBar().setValue(0)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_buttons()

    def _update_buttons(self) -> None:
        ready = self._message is not None and not self._busy
        self.source.setEnabled(ready)
        self.wanted.setEnabled(ready)

    def _open_source(self) -> None:
        if self._message is not None:
            self.source_requested.emit(str(self._message.web_link))

    def _toggle_wanted(self) -> None:
        if self._message is not None:
            self.wanted_requested.emit(self._message.provider_message_id, not self._wanted)

    def _column_margin(self, width: int) -> int:
        """The margin each side that centres a column at most COLUMN_MAX wide."""
        return max(SIDE_MARGIN, (width - COLUMN_MAX) // 2)

    def _place_column(self) -> None:
        """Centre the content and the footer's column in the pane's width."""
        content_width = self.body.viewport().width()
        side = self._column_margin(content_width)
        self._content.setContentsMargins(side, TOP_MARGIN, side, 20)
        side = self._column_margin(self.width())
        self._footer.setContentsMargins(side, 0, side, 14)

    def _fit_actions(self) -> None:
        """The actions side by side when they fit the column, else one above the other."""
        margins = self._footer.contentsMargins()
        room = self.width() - margins.left() - margins.right()
        needed = (
            self.source.sizeHint().width()
            + self._actions.spacing()
            + self.wanted.sizeHint().width()
        )
        direction = (
            QBoxLayout.Direction.LeftToRight if needed <= room else QBoxLayout.Direction.TopToBottom
        )
        if self._actions.direction() is not direction:
            self._actions.setDirection(direction)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._place_column()
        self._fit_actions()

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.body.viewport() and event.type() == QEvent.Type.Resize:
            self._place_column()
        return False

    def minimumSizeHint(self) -> QSize:
        """Its column shrinks and its actions stack, so it needs only the wider action
        and the margins."""
        least = max(self.source.sizeHint().width(), self.wanted.sizeHint().width())
        return QSize(least + 2 * SIDE_MARGIN, super().minimumSizeHint().height())

    def buttons(self) -> list[QPushButton]:
        """Its buttons in display order, for the Tab order."""
        return [self.close_button, self.source, self.wanted]
