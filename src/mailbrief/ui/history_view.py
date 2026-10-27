"""The Briefs page: saved briefs by day, and missed days to brief (M8 Part 3).

The panel only emits requests; the window loads and briefs through the backend. Rows are
plain text. Briefing a past day is always the owner's explicit choice, one day at a time,
and replacing a saved brief is confirmed first.
"""

from datetime import date, timedelta

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.digests import DailyDigest, DigestSection, SavedBriefSummary
from mailbrief.services.history import CATCH_UP_DAYS
from mailbrief.ui.brief_detail import is_gmail_link
from mailbrief.ui.deadline_text import MONTHS, day_text, full_day_text
from mailbrief.ui.hairline import HairlineDivider, HairlineSplitter
from mailbrief.ui.labels import outline_button, plain_label, wrap_label
from mailbrief.ui.list_rows import DISPLAY_ROW_ROLE, DisplayRow, DisplayRowDelegate
from mailbrief.ui.lists import ActivatingList, clear_list
from mailbrief.ui.theme import ui_font

NOT_CONNECTED = "Connect Gmail to brief missed days."
NONE_MISSED = "No missed days in the last 7 days."
NEEDS_CONNECTION = "Connect Gmail to brief a past day."
OTHER_ACCOUNT = "That brief is for another account; connect it to brief this day again."


def _plain(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def summary_text(summary: SavedBriefSummary, today: date) -> str:
    noun = "item" if summary.item_count == 1 else "items"
    return (
        f"{day_text(summary.local_date, today=today)} · {summary.status.value.capitalize()} · "
        f"{summary.item_count} {noun} · {summary.account_email}"
    )


class BriefHistoryPanel(QWidget):
    """``open_requested(account_email, local_date)`` asks to show a saved brief;
    ``generate_requested(local_date)`` asks to brief a past day for the connected account."""

    open_requested = Signal(str, object)
    generate_requested = Signal(object)
    preview_requested = Signal(str, object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("briefHistory")
        self.setAccessibleName("Saved briefs and missed days")
        self._summaries: tuple[SavedBriefSummary, ...] = ()
        self._missed: tuple[date, ...] = ()
        self._account: str | None = None
        self._today = date.min
        self._busy = False
        self._preview: DailyDigest | None = None
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.splitter = HairlineSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        left = QWidget()
        left.setObjectName("historyListPane")
        left.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QVBoxLayout(left)
        layout.setContentsMargins(21, 21, 21, 16)
        layout.setSpacing(12)
        heading = QLabel("Saved briefs")
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setFont(ui_font(12, medium=True))
        layout.addWidget(heading)
        layout.addWidget(plain_label("Previous briefs, ready to revisit.", tone="muted", px=9))
        self.saved = ActivatingList()
        self.saved.setObjectName("savedBriefList")
        self.saved.setFrameShape(QFrame.Shape.NoFrame)
        self.saved.setItemDelegate(DisplayRowDelegate(self.saved))
        self.saved.setAccessibleName("Saved briefs")
        layout.addWidget(self.saved, 2)
        layout.addWidget(plain_label("Missed days", px=12, medium=True))
        self.missed = QListWidget()
        self.missed.setObjectName("missedDayList")
        self.missed.setFrameShape(QFrame.Shape.NoFrame)
        self.missed.setAccessibleName("Missed days in the last 7 days")
        layout.addWidget(self.missed, 1)
        self.missed_note = _plain()
        layout.addWidget(self.missed_note)
        buttons = QHBoxLayout()
        self.open_button = outline_button(
            "&Open brief", "openBriefButton", mnemonic=True, primary=True
        )
        self.brief_button = outline_button("&Brief this day…", "briefDayButton", mnemonic=True)
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.brief_button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        self.confirm_panel = QWidget()
        self.confirm_panel.setObjectName("replaceConfirmation")
        self.confirm_panel.setAccessibleName("Replace the saved brief")
        confirm = QVBoxLayout(self.confirm_panel)
        confirm.setContentsMargins(0, 0, 0, 0)
        self.confirm_label = _plain()
        confirm.addWidget(self.confirm_label)
        choices = QHBoxLayout()
        self.replace_button = outline_button("&Replace", "replaceBriefButton", mnemonic=True)
        self.keep_button = outline_button("&Keep it", "keepBriefButton", mnemonic=True)
        choices.addWidget(self.replace_button)
        choices.addWidget(self.keep_button)
        choices.addStretch(1)
        confirm.addLayout(choices)
        layout.addWidget(self.confirm_panel)
        self.confirm_panel.hide()
        layout.addWidget(
            plain_label(
                "A past day's brief covers only the messages still in your Inbox when it is "
                "made. Briefing sends messages to Groq only after you approve, as for today.",
                tone="muted",
                px=9,
            )
        )
        self.status = _plain()
        layout.addWidget(self.status)
        layout.addStretch(1)
        self.saved.currentRowChanged.connect(lambda row: self._chose(self.saved, row))
        self.missed.currentRowChanged.connect(lambda row: self._chose(self.missed, row))
        self.saved.itemActivated.connect(lambda _item: self._open())
        self.open_button.clicked.connect(self._open)
        self.brief_button.clicked.connect(self._brief)
        self.replace_button.clicked.connect(self._replace)
        self.keep_button.clicked.connect(self.confirm_panel.hide)
        preview = QWidget()
        preview.setObjectName("briefHistoryPreview")
        right = QVBoxLayout(preview)
        right.setContentsMargins(44, 30, 44, 25)
        right.setSpacing(20)
        right.addWidget(plain_label("SAVED DAILY BRIEF", tone="muted", px=10, medium=True))
        self.preview_title = wrap_label("Select a saved brief", px=24)
        self.preview_title.setObjectName("historyPreviewTitle")
        self.preview_meta = wrap_label(
            "Open a brief to review its summaries and suggestions.", tone="secondary", px=11
        )
        self.preview_meta.setObjectName("historyPreviewMeta")
        right.addWidget(self.preview_title)
        right.addWidget(self.preview_meta)
        self.preview_description = wrap_label("", tone="secondary", px=11)
        self.preview_description.setObjectName("historyPreviewDescription")
        right.addWidget(self.preview_description)
        metrics = QWidget()
        metrics.setObjectName("historyMetrics")
        metrics.setAccessibleName("Saved brief overview")
        metric_frame = QVBoxLayout(metrics)
        metric_frame.setContentsMargins(0, 0, 0, 0)
        metric_frame.setSpacing(18)
        metric_frame.addWidget(HairlineDivider())
        metric_layout = QHBoxLayout()
        metric_frame.addLayout(metric_layout)
        self.item_count = plain_label("", px=21, medium=True)
        self.item_count.setObjectName("historyItemCount")
        self.reply_count = plain_label("", px=21, medium=True)
        self.reply_count.setObjectName("historyReplyCount")
        self.highlight_count = plain_label("", px=21, medium=True)
        self.highlight_count.setObjectName("historyHighlightCount")
        for label, value in (
            ("Needs attention", self.item_count),
            ("New replies", self.reply_count),
            ("Highlights", self.highlight_count),
        ):
            column = QVBoxLayout()
            column.addWidget(value)
            column.addWidget(plain_label(label, tone="muted", px=8))
            metric_layout.addLayout(column, 1)
        metric_frame.addWidget(HairlineDivider())
        right.addWidget(metrics)
        choices = QHBoxLayout()
        choices.addWidget(self.open_button)
        self._source_url: str | None = None
        self.source_button = outline_button(
            "Open in Gmail", "historySource", icon_name="external-link"
        )
        self.source_button.clicked.connect(self._open_source)
        choices.addWidget(self.source_button)
        choices.addWidget(self.brief_button)
        choices.addStretch(1)
        right.addLayout(choices)
        right.addStretch(1)
        left.setMinimumWidth(260)
        preview.setMinimumWidth(320)
        self.splitter.addWidget(left)
        self.splitter.addWidget(preview)
        self.splitter.setSizes([350, 525])
        outer.addWidget(self.splitter)
        self._update_buttons()

    def show_preview(self, digest: DailyDigest) -> None:
        selected = self._chosen_summary()
        if selected is None or (selected.account_email, selected.local_date) != (
            digest.account_id,
            digest.local_date,
        ):
            return
        self._preview = digest
        counts = {
            section: sum(item.section is section for item in digest.items)
            for section in DigestSection
        }
        self.item_count.setText(
            str(
                counts[DigestSection.ACTIONS]
                + counts[DigestSection.DECISIONS]
                + counts[DigestSection.DEADLINES]
            )
        )
        self.reply_count.setText(str(counts[DigestSection.FOLLOW_UPS]))
        self.highlight_count.setText(str(counts[DigestSection.HIGHLIGHTS]))
        self.preview_description.setText(
            f"{self.item_count.text()} items needed attention, {self.reply_count.text()} replies "
            f"arrived, and {self.highlight_count.text()} messages were saved as highlights."
        )
        self._source_url = next(
            (str(item.source_url) for item in digest.items if is_gmail_link(str(item.source_url))),
            None,
        )
        self.source_button.setVisible(self._source_url is not None)

    def _open_source(self) -> None:
        if self._source_url is not None:
            QDesktopServices.openUrl(QUrl(self._source_url))

    def configure(
        self,
        summaries: tuple[SavedBriefSummary, ...],
        missed: tuple[date, ...],
        account_email: str | None,
        today: date,
    ) -> None:
        """Show saved briefs and, for the connected account, its missed days."""
        self._summaries = summaries
        self._account, self._today = account_email, today
        self.confirm_panel.hide()
        self.status.clear()
        self.saved.blockSignals(True)
        clear_list(self.saved)
        for summary in summaries:
            item = QListWidgetItem(summary_text(summary, self._today))
            item.setData(
                DISPLAY_ROW_ROLE,
                DisplayRow(
                    full_day_text(summary.local_date, today=self._today),
                    f"{summary.status.value.capitalize()} · {summary.item_count} items",
                    "",
                    date_tile=(
                        f"{MONTHS[summary.local_date.month - 1].upper()}\n{summary.local_date.day}"
                    ),
                ),
            )
            self.saved.addItem(item)
        self.saved.blockSignals(False)
        self.saved.setMaximumHeight(max(70, min(320, len(summaries) * 70)))
        self._show_missed(missed)
        if not summaries:
            self.status.setText("No saved briefs yet.")
        if summaries:
            self.saved.setCurrentRow(0)
        self._update_buttons()

    def set_account(self, account_email: str | None) -> None:
        """Follow the connected account without reading storage. Missed days belong to an
        account, so another account's are dropped (the window reloads them); without one,
        the page says to connect. A replacement being confirmed is withdrawn."""
        if account_email == self._account:
            return
        self._account = account_email
        self._show_missed(None)
        self.confirm_panel.hide()
        if self.status.text() in (NEEDS_CONNECTION, OTHER_ACCOUNT):
            self.status.clear()  # It was about the account that changed.
        self._update_buttons()

    def _show_missed(self, missed: tuple[date, ...] | None) -> None:
        """The missed-days list and its note, for ``self._account``. ``missed`` is None
        until that account's days are loaded; the note then claims nothing about them."""
        self._missed = missed or ()
        self.missed.blockSignals(True)
        clear_list(self.missed)
        for day in self._missed:
            self.missed.addItem(QListWidgetItem(f"{day_text(day, today=self._today)} · no brief"))
        self.missed.blockSignals(False)
        if self._account is None:
            note = NOT_CONNECTED
        elif missed is None or missed:
            note = ""
        else:
            note = NONE_MISSED
        self.missed_note.setText(note)
        self.missed_note.setVisible(bool(note))
        self.missed.setVisible(bool(self._missed))  # The note says why there are none.

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_buttons()

    def _chose(self, widget: QListWidget, row: int) -> None:
        """Choosing in one list clears the other, so one day is chosen at a time."""
        if row >= 0:
            other = self.missed if widget is self.saved else self.saved
            other.blockSignals(True)
            other.setCurrentRow(-1)
            other.clearSelection()
            other.blockSignals(False)
        self.confirm_panel.hide()
        self._update_buttons()
        if (summary := self._chosen_summary()) is not None:
            self.preview_requested.emit(summary.account_email, summary.local_date)

    def _chosen_summary(self) -> SavedBriefSummary | None:
        row = self.saved.currentRow()
        return self._summaries[row] if 0 <= row < len(self._summaries) else None

    def _chosen_missed(self) -> date | None:
        row = self.missed.currentRow()
        return self._missed[row] if 0 <= row < len(self._missed) else None

    def _chosen_day(self) -> date | None:
        summary = self._chosen_summary()
        return summary.local_date if summary is not None else self._chosen_missed()

    def _can_brief(self, day: date | None) -> bool:
        """A past day within the catch-up window; today is briefed with Sync and review."""
        return day is not None and (
            self._today - timedelta(days=CATCH_UP_DAYS) <= day < self._today
        )

    def _update_buttons(self) -> None:
        self.saved.setEnabled(not self._busy)
        self.missed.setEnabled(not self._busy)
        self.open_button.setEnabled(not self._busy and self._chosen_summary() is not None)
        self.brief_button.setEnabled(not self._busy and self._can_brief(self._chosen_day()))
        self.replace_button.setEnabled(not self._busy)
        summary = self._chosen_summary()
        day = self._chosen_missed()
        self.source_button.hide()
        self.preview_description.clear()
        if summary is not None:
            self.preview_title.setText(full_day_text(summary.local_date, today=self._today))
            self.preview_meta.setText(f"Review {summary.status.value} · {summary.account_email}")
            self.item_count.setText("—")
            self.reply_count.setText(str(summary.follow_up_count))
            self.highlight_count.setText("—")
        else:
            self.preview_title.setText(
                "Select a saved brief" if day is None else day_text(day, today=self._today)
            )
            self.preview_meta.setText(
                "Open a brief to review its summaries and suggestions."
                if day is None
                else (
                    "No saved brief for this day. Review mail still in your Inbox, "
                    "then approve any AI analysis."
                )
            )
            self.item_count.clear()
            self.reply_count.clear()
            self.highlight_count.clear()
        if self._preview is not None:
            self.show_preview(self._preview)

    def _open(self) -> None:
        summary = self._chosen_summary()
        if summary is not None and not self._busy:
            self.open_requested.emit(summary.account_email, summary.local_date)

    def _brief(self) -> None:
        day = self._chosen_day()
        if self._busy or not self._can_brief(day):
            return
        assert day is not None
        if self._account is None:
            self.status.setText(NEEDS_CONNECTION)
            return
        summary = self._chosen_summary()
        if summary is None:
            self.generate_requested.emit(day)
            return
        if summary.account_email != self._account:
            self.status.setText(OTHER_ACCOUNT)
            return
        self.confirm_label.setText(
            f"This replaces the saved brief for {day_text(day, today=self._today)}."
        )
        self.confirm_panel.show()
        self.keep_button.setFocus()

    def _replace(self) -> None:
        day = self._chosen_day()
        self.confirm_panel.hide()
        if day is not None and not self._busy:
            self.generate_requested.emit(day)
