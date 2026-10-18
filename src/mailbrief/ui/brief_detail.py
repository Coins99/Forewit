"""The selected email's summary, deadline, suggestions and proposals.

Buttons only emit requests, with the IDs and revisions shown; the window decides what
happens. Mail and AI text appears only in plain-text labels and escaped button text, never
in a tooltip, and the pane never opens a URL itself.
"""

from collections.abc import Callable, Sequence
from datetime import date
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QResizeEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mailbrief.branding import DISPLAY_NAME
from mailbrief.domain.actions import (
    TARGET_REASON_TEXT,
    ActionProposal,
    ProposalState,
    SuggestionState,
    SuggestionView,
    ThreadLink,
)
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.digests import DigestItem
from mailbrief.ui.brief_list import BRIEF_SECTION_TITLES, NO_SUBJECT
from mailbrief.ui.deadline_text import day_text, deadline_text
from mailbrief.ui.hairline import HairlineDivider, HairlineFrame
from mailbrief.ui.labels import (
    outline_button,
    plain_label,
    wrap_label,
)
from mailbrief.ui.proposals_view import effect_text
from mailbrief.ui.sender_identity import sender_identity
from mailbrief.ui.theme import (
    BODY_PX,
    TEXT_PX,
    current_tokens,
    follow_theme,
    icon_pixmap,
)

# What a suggestion or proposal request asks for.
ACCEPT = "accept"
DISMISS = "dismiss"
APPLY = "apply"
EMPTY_TEXT = "Select an email to see its summary and suggestions."
DETAIL_MAX_WIDTH = 720
_ICON_PX = 14


def item_deadline_text(item: DigestItem, zone: ZoneInfo, *, today: date) -> str | None:
    """The email's own deadline in the brief's zone, "Due Thu Oct 8, 17:00", or None."""
    due = deadline_text(item, zone, today=today)
    return None if due is None else f"Due {due}"


def suggestion_meta(view: SuggestionView, zone: ZoneInfo, *, today: date) -> str:
    """Whose it is, then its target, else its deadline, else that none was stated."""
    suggestion = view.suggestion
    text = "Yours." if suggestion.ownership is ActionOwnership.MINE else "Waiting for someone."
    target, reason = suggestion.suggested_target_date, suggestion.target_reason
    due = deadline_text(suggestion, zone, today=today)
    if target is not None and reason is not None:
        return f"{text} Target {day_text(target, today=today)}, {TARGET_REASON_TEXT[reason]}."
    if due is not None:
        return f"{text} Due {due}."
    return f"{text} No deadline stated."


def is_gmail_link(url: str) -> bool:
    """Only https links on mail.google.com are ever offered for opening."""
    parts = urlsplit(url)
    return parts.scheme == "https" and parts.hostname == "mail.google.com"


class BriefDetailPane(QScrollArea):
    """``suggestion_requested(kind, suggestion_id)`` with ACCEPT or DISMISS,
    ``accept_into_requested(suggestion_id, public_id, revision)``,
    ``proposal_requested(kind, proposal_id, action_revision)`` with APPLY or DISMISS,
    ``reply_requested(account_email, message_key)`` and ``source_requested(url)``."""

    suggestion_requested = Signal(str, int)
    accept_into_requested = Signal(int, str, int)
    proposal_requested = Signal(str, int, int)
    reply_requested = Signal(str, str)
    source_requested = Signal(str)
    rebuilt = Signal()  # The content was replaced, with new buttons.

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("briefDetail")
        self.setAccessibleName("Selected email")
        # Tab goes from the brief list straight to the buttons; a click on the pane still
        # lets the arrow keys scroll it.
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Reserve the scrollbar gutter so replacing content cannot change its wrapping
        # width and invalidate a saved scroll position during a brief refresh.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.title: QLabel | None = None
        self.reply_host = QWidget(self)
        self.reply_host.setObjectName("inlineReplyCard")
        self.reply_host.setAccessibleName("Reply draft")
        self.reply_host.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.reply_host.hide()
        self.reply_layout = QVBoxLayout(self.reply_host)
        self.reply_layout.setContentsMargins(1, 1, 1, 1)
        self.reply_key: str | None = None
        self.shown_key: str | None = None
        self.show_empty()

    def _fresh(self) -> QVBoxLayout:
        self.reply_host.setParent(self)
        self.reply_host.hide()
        content = QWidget()
        content.setObjectName("briefDetailContent")
        # A readable measure on wide windows; the scroll area keeps it at the left.
        content.setMaximumWidth(DETAIL_MAX_WIDTH)
        layout = QVBoxLayout(content)
        margin = max(16, min(44, self.viewport().width() * 8 // 100 - 8))
        layout.setContentsMargins(margin, 25, margin, 36)
        layout.setSpacing(16)
        self.setWidget(content)  # Deletes the previous content.
        return layout

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if (content := self.widget()) is not None and (layout := content.layout()) is not None:
            margin = max(16, min(44, self.viewport().width() * 8 // 100 - 8))
            layout.setContentsMargins(margin, 25, margin, 36)

    def show_empty(self, text: str = EMPTY_TEXT) -> None:
        self.shown_key = None
        layout = self._fresh()
        self.title = None
        layout.addWidget(plain_label(text, tone="muted", px=TEXT_PX))
        layout.addStretch(1)
        self.rebuilt.emit()

    def buttons(self) -> list[QPushButton]:
        """The content's buttons, in display order."""
        content = self.widget()
        return [] if content is None else content.findChildren(QPushButton)

    def show_item(
        self,
        item: DigestItem,
        *,
        account_email: str,
        timezone_name: str,
        today: date,
        links: Sequence[ThreadLink] = (),
        proposals: Sequence[ActionProposal] = (),
    ) -> None:
        zone = ZoneInfo(timezone_name)
        self.shown_key = item.message_key
        layout = self._fresh()
        layout.setSpacing(0)
        kicker = plain_label(
            BRIEF_SECTION_TITLES[item.section].upper(), tone="muted", px=9, medium=True
        )
        kicker.setObjectName("detailKicker")
        layout.addWidget(kicker)
        layout.addSpacing(8)
        self.title = wrap_label(item.subject or NO_SUBJECT, px=21)
        self.title.setObjectName("detailTitle")
        layout.addWidget(self.title)
        layout.addSpacing(18)
        layout.addWidget(sender_identity(item.sender, name="detailSender"))
        layout.addWidget(HairlineDivider())
        layout.addSpacing(20)
        summary = QWidget()
        summary.setObjectName("summaryCard")
        summary.setAccessibleName("Forewit summary")
        summary.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        summary_layout = QVBoxLayout(summary)
        summary_layout.setContentsMargins(16, 14, 16, 14)
        summary_layout.setSpacing(8)
        summary_header = QHBoxLayout()
        spark = plain_label("")
        spark.setObjectName("summaryIcon")
        spark.setAccessibleName("Forewit summary")
        spark.setPixmap(icon_pixmap("sparkles", current_tokens().primary, 15, 2.0))
        summary_header.addWidget(spark)
        summary_heading = plain_label(f"{DISPLAY_NAME.upper()} SUMMARY", px=9, medium=True)
        summary_heading.setObjectName("summaryHeading")
        summary_header.addWidget(summary_heading, 1)
        summary_layout.addLayout(summary_header)
        summary_layout.addWidget(wrap_label(item.summary, px=BODY_PX))
        source = str(item.source_url)
        if is_gmail_link(source):
            view_email = outline_button(
                "View complete email", "viewEmailButton", icon_name="external-link", px=9
            )
            view_email.setProperty("variant", "link")
            view_email.clicked.connect(
                lambda _checked=False, url=source: self.source_requested.emit(url)
            )
            summary_layout.addWidget(view_email, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(summary)
        layout.addSpacing(18)
        if item.evidence:
            evidence = QWidget()
            evidence.setObjectName("emailExcerpt")
            evidence.setAccessibleName("From the email")
            excerpt_layout = QVBoxLayout(evidence)
            excerpt_layout.setContentsMargins(4, 2, 4, 2)
            excerpt_layout.setSpacing(7)
            excerpt_layout.addWidget(plain_label("FROM THE EMAIL", tone="muted", px=9))
            excerpt_layout.addWidget(wrap_label(item.evidence, tone="secondary", px=BODY_PX))
            layout.addWidget(evidence)
            layout.addSpacing(12)
        if item.action_text and item.action_text != item.summary:
            layout.addWidget(wrap_label(item.action_text, tone="secondary", px=11))
            layout.addSpacing(12)
        due = item_deadline_text(item, zone, today=today)
        if due is not None:
            layout.addLayout(self._deadline_row(due))
            layout.addSpacing(18)
        for link in links:
            if not link.is_source:
                layout.addWidget(wrap_label(f"Continues: “{link.title}”", tone="secondary", px=11))
                layout.addSpacing(16)
        for view in item.suggestions:
            if view.state is SuggestionState.PENDING:
                layout.addWidget(self._suggestion_card(view, links, zone, today))
                layout.addSpacing(16)
            elif view.state is SuggestionState.ACCEPTED:
                layout.addWidget(
                    wrap_label(f"Accepted: {view.suggestion.title}", tone="secondary", px=TEXT_PX)
                )
        for proposal in proposals:
            if proposal.state is ProposalState.PENDING:
                layout.addWidget(self._proposal_card(proposal, zone, today))
        layout.addLayout(self._footer(item, account_email))
        layout.addSpacing(16)
        if self.reply_key == self.shown_key:
            layout.addWidget(self.reply_host)
            self.reply_host.show()
        layout.addStretch(1)
        self.rebuilt.emit()

    def show_reply_editor(self, editor: QWidget) -> None:
        self.reply_key = self.shown_key
        self.reply_layout.addWidget(editor)
        content = self.widget()
        if content is not None and isinstance(layout := content.layout(), QVBoxLayout):
            layout.insertWidget(max(0, layout.count() - 1), self.reply_host)
        self.reply_host.show()
        editor.show()
        QTimer.singleShot(0, self._scroll_to_reply)

    def _scroll_to_reply(self) -> None:
        if self.reply_host.isVisible():
            self.ensureWidgetVisible(self.reply_host, 0, 12)

    def _deadline_row(self, text: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        clock = QLabel()
        follow_theme(
            clock,
            lambda label: label.setPixmap(
                icon_pixmap("clock", current_tokens().warning_fg, _ICON_PX, 2.0)
            ),
        )
        clock.setAccessibleName("Deadline")
        row.addWidget(clock)
        row.addWidget(wrap_label(text, tone="warning", px=10), 1)
        return row

    def _suggestion_card(
        self, view: SuggestionView, links: Sequence[ThreadLink], zone: ZoneInfo, today: date
    ) -> HairlineFrame:
        card = HairlineFrame()
        card.setObjectName("suggestionCard")
        card.setAccessibleName("Suggested action")
        outer = QHBoxLayout(card)
        outer.setContentsMargins(5, 7, 5, 7)
        outer.setSpacing(4)
        symbol = plain_label("")
        symbol.setPixmap(icon_pixmap("sparkles", current_tokens().primary, 22, 2.0))
        symbol.setObjectName("suggestionIcon")
        symbol.setAccessibleName("Suggested action")
        symbol.setFixedSize(28, 28)
        symbol.setAlignment(Qt.AlignmentFlag.AlignCenter)
        outer.addWidget(symbol, 0, Qt.AlignmentFlag.AlignTop)
        layout = QVBoxLayout()
        outer.addLayout(layout, 1)
        layout.setSpacing(4)
        layout.addWidget(plain_label("SUGGESTED ACTION", tone="muted", px=9, medium=True))
        layout.addWidget(wrap_label(view.suggestion.title, px=13))
        layout.addWidget(
            wrap_label(suggestion_meta(view, zone, today=today), tone="secondary", px=10)
        )
        for step in view.suggestion.steps:  # The suggested plan, in order.
            layout.addWidget(wrap_label(f"· {step}", tone="secondary", px=10))
        facts = QHBoxLayout()
        target = view.suggestion.suggested_target_date
        for caption, value in (
            (
                "Owner",
                "You"
                if view.suggestion.ownership is ActionOwnership.MINE
                else "Waiting for someone",
            ),
            (
                "Suggested due date",
                day_text(target, today=today) if target is not None else "Not set",
            ),
        ):
            column = QVBoxLayout()
            column.setSpacing(4)
            column.addWidget(plain_label(caption, tone="muted", px=8))
            column.addWidget(plain_label(value, px=9, medium=True))
            facts.addLayout(column)
        facts.addStretch(1)
        layout.addSpacing(8)
        layout.addLayout(facts)
        layout.addSpacing(4)
        decisions = QHBoxLayout()
        decisions.setSpacing(8)
        accept = outline_button("Add action", "acceptButton", primary=True, px=10)
        accept.clicked.connect(
            lambda _checked=False, sid=view.suggestion_id: self.suggestion_requested.emit(
                ACCEPT, sid
            )
        )
        dismiss = outline_button("Dismiss", "dismissButton", glass=True, px=10)
        dismiss.clicked.connect(
            lambda _checked=False, sid=view.suggestion_id: self.suggestion_requested.emit(
                DISMISS, sid
            )
        )
        decisions.addWidget(accept)
        decisions.addWidget(dismiss)
        decisions.addStretch(1)
        layout.addLayout(decisions)
        for link in links:
            # One row each, so a long title never widens the pane.
            row = QHBoxLayout()
            button = outline_button(
                f"Add to “{link.title}”", "addToButton", shorten=True, glass=True, px=9
            )
            button.clicked.connect(self._into(view.suggestion_id, link))
            row.addWidget(button)
            row.addStretch(1)
            layout.addSpacing(4)
            layout.addLayout(row)
        return card

    def _into(self, suggestion_id: int, link: ThreadLink) -> Callable[[], None]:
        """An "Add to" handler bound to this suggestion and action, not the loop's last."""
        return lambda: self.accept_into_requested.emit(suggestion_id, link.public_id, link.revision)

    def _proposal_card(
        self, proposal: ActionProposal, zone: ZoneInfo, today: date
    ) -> HairlineFrame:
        card = HairlineFrame()
        card.setObjectName("proposalCard")
        card.setAccessibleName("Proposed update")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        layout.addWidget(
            wrap_label(f"Proposes for “{proposal.action_title}”: “{proposal.evidence}”", px=TEXT_PX)
        )
        row = QHBoxLayout()
        row.setSpacing(8)
        apply = outline_button(
            effect_text(proposal, zone, today=today),
            "applyProposalButton",
            shorten=True,
            glass=True,
        )
        apply.clicked.connect(
            lambda _checked=False, pid=proposal.id, revision=proposal.action_revision: (
                self.proposal_requested.emit(APPLY, pid, revision)
            )
        )
        dismiss = outline_button("Dismiss", "dismissProposalButton", glass=True)
        dismiss.clicked.connect(
            lambda _checked=False, pid=proposal.id, revision=proposal.action_revision: (
                self.proposal_requested.emit(DISMISS, pid, revision)
            )
        )
        row.addWidget(apply)
        row.addWidget(dismiss)
        row.addStretch(1)
        layout.addLayout(row)
        return card

    def _footer(self, item: DigestItem, account_email: str) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        reply = outline_button("Draft a reply", "replyButton", icon_name="pencil", glass=True, px=9)
        reply.clicked.connect(
            lambda _checked=False, key=item.message_key: self.reply_requested.emit(
                account_email, key
            )
        )
        row.addWidget(reply)
        source = str(item.source_url)
        if is_gmail_link(source):
            open_gmail = outline_button(
                "Open in Gmail",
                "openInGmailButton",
                icon_name="external-link",
                glass=True,
                px=9,
            )
            open_gmail.clicked.connect(
                lambda _checked=False, url=source: self.source_requested.emit(url)
            )
            row.addWidget(open_gmail)
        row.addStretch(1)
        return row
