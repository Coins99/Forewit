"""The owner's drafts and notes, newest first, with New, Open and Delete.

Titles are the owner's text or, for replies, an email's subject, so rows are plain list-item
text and never tooltips, which Qt may render as rich text.
"""

from datetime import date
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.drafts import KIND_NAMES, Draft, DraftKind, DraftSummary
from mailbrief.ui.deadline_text import moment_text
from mailbrief.ui.hairline import HairlineSplitter
from mailbrief.ui.labels import outline_button, plain_label, wrap_label
from mailbrief.ui.list_rows import DISPLAY_ROW_ROLE, DisplayRow, DisplayRowDelegate
from mailbrief.ui.lists import ActivatingList
from mailbrief.ui.theme import TITLE_PX, ui_font

NEW = "new"
OPEN = "open"
DELETE = "delete"
NEW_KINDS = (DraftKind.EMAIL, DraftKind.NOTE, DraftKind.MESSAGE)


def describe(summary: DraftSummary, zone: ZoneInfo, today: date) -> str:
    """One plain line: kind, title, when it changed, what is left to fill, and its action."""
    updated = moment_text(summary.updated_at_utc, zone, today=today)
    parts = [f"{KIND_NAMES[summary.kind]} · {summary.display_title} — updated {updated}"]
    count = summary.placeholder_count
    if count:
        parts.append(f"{count} placeholder{'' if count == 1 else 's'}")
    if summary.action_title:
        parts.append(f"for “{summary.action_title}”")
    return " · ".join(parts)


class DraftsPanel(QWidget):
    """Lists drafts and asks the window to act; it changes nothing itself.

    ``draft_requested(kind, value)`` carries NEW with a DraftKind, or OPEN or DELETE with
    the selected DraftSummary.
    """

    draft_requested = Signal(str, object)
    preview_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("draftsPanel")
        self.setAccessibleName("Drafts and notes")
        self._drafts: tuple[DraftSummary, ...] = ()
        self._busy = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.splitter = HairlineSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        list_pane = QWidget()
        list_pane.setObjectName("draftsListPane")
        list_pane.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        left = QVBoxLayout(list_pane)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(0)
        heading = QLabel("Your drafts and notes", list_pane)
        heading.setTextFormat(Qt.TextFormat.PlainText)
        heading.setFont(ui_font(TITLE_PX, medium=True))
        heading.hide()
        self.search = QLineEdit()
        self.search.setObjectName("draftSearch")
        self.search.setAccessibleName("Search drafts and notes")
        self.search.setPlaceholderText("Search drafts")
        self.search.textChanged.connect(self._filter)
        toolbar = QWidget()
        toolbar.setObjectName("draftsToolbar")
        toolbar.setFixedHeight(52)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(14, 9, 14, 9)
        toolbar_layout.addWidget(self.search, 1)
        self.draft_count = plain_label("0 drafts", tone="muted", px=9)
        toolbar_layout.addWidget(self.draft_count)
        left.addWidget(toolbar)
        self.search.setFont(ui_font(9))
        self.list = ActivatingList()
        self.list.setAccessibleName("Drafts and notes")
        self.list.setObjectName("draftList")
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setItemDelegate(DisplayRowDelegate(self.list))
        self.list.currentRowChanged.connect(self._selection_changed)
        self.list.itemActivated.connect(lambda _item: self._request(OPEN))
        left.addWidget(self.list, 1)
        # With no drafts, a centred message fills the page in place of the list.
        self.empty = QLabel("No drafts yet. Start one with New, or from a brief item or action.")
        self.empty.setObjectName("draftsEmpty")
        self.empty.setTextFormat(Qt.TextFormat.PlainText)
        self.empty.setWordWrap(True)
        self.empty.setProperty("tone", "muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        left.addWidget(self.empty, 1)
        self.list.hide()
        buttons = QHBoxLayout()
        self._list_buttons = buttons
        self.new_button = outline_button(
            "Ne&w draft", "newDraftButton", mnemonic=True, primary=True, icon_name="plus"
        )
        self.new_menu = QMenu(self.new_button)
        for kind in NEW_KINDS:
            menu_action = self.new_menu.addAction(KIND_NAMES[kind])
            menu_action.triggered.connect(
                lambda _checked=False, chosen=kind: self._request_new(chosen)
            )
        self.new_button.setMenu(self.new_menu)
        self.open_button = outline_button("&Open", "openDraftButton", mnemonic=True)
        self.delete_button = outline_button("Delete draf&t", "deleteDraftButton", mnemonic=True)
        for button in (self.new_button, self.open_button, self.delete_button):
            buttons.addWidget(button)
        buttons.addStretch(1)  # Buttons keep their own width.
        left.addLayout(buttons)
        preview = QWidget()
        preview.setObjectName("draftPreview")
        right = QVBoxLayout(preview)
        right.setContentsMargins(25, 25, 25, 25)
        right.setSpacing(16)
        right.addWidget(plain_label("LOCAL DRAFT · NOT SENT", tone="muted", px=10))
        self.preview_title = wrap_label("Select a draft", px=21, medium=True)
        self.preview_title.setObjectName("draftPreviewTitle")
        self.preview_meta = wrap_label(
            "Open a draft to edit, autosave, copy or export it.", tone="secondary"
        )
        self.preview_meta.setObjectName("draftPreviewMeta")
        self.preview_body = QPlainTextEdit()
        self.preview_body.setObjectName("draftPreviewBody")
        self.preview_body.setAccessibleName("Draft preview")
        self.preview_body.setReadOnly(True)
        right.addWidget(self.preview_title)
        right.addWidget(self.preview_meta)
        right.addWidget(self.preview_body, 1)
        right.addWidget(
            plain_label(
                "Your writing stays local. Open the editor for autosave and versions.",
                tone="muted",
                px=10,
            )
        )
        list_pane.setMinimumWidth(260)
        preview.setMinimumWidth(320)
        self.splitter.addWidget(list_pane)
        self.editor_host = QStackedWidget()
        self.editor_host.setObjectName("draftEditorHost")
        self.editor_host.setAccessibleName("Selected draft")
        self.editor_host.addWidget(preview)
        self.splitter.addWidget(self.editor_host)
        self.splitter.setSizes([350, 525])
        layout.addWidget(self.splitter)
        self.open_button.clicked.connect(lambda: self._request(OPEN))
        self.delete_button.clicked.connect(lambda: self._request(DELETE))
        self._update_buttons()

    def selected(self) -> DraftSummary | None:
        row = self.list.currentRow()
        return self._drafts[row] if 0 <= row < len(self._drafts) else None

    def select_draft(self, public_id: str) -> None:
        for row, summary in enumerate(self._drafts):
            if summary.public_id == public_id:
                self.list.setCurrentRow(row)
                return

    def restore_list_controls(self) -> None:
        """Return controls moved into the inline editor before it becomes a dialog."""
        self._list_buttons.insertWidget(1, self.delete_button)
        self.open_button.show()
        self.delete_button.show()

    def show_drafts(self, drafts: tuple[DraftSummary, ...], zone: ZoneInfo, today: date) -> None:
        """Replace the list, keeping the selection on the same draft when it remains."""
        previous = self.selected()
        row = self.list.currentRow()
        self._drafts = drafts
        self.draft_count.setText(f"{len(drafts)} drafts")
        self.list.clear()
        for summary in drafts:
            changed = moment_text(summary.updated_at_utc, zone, today=today)
            item = QListWidgetItem(describe(summary, zone, today))
            item.setData(
                DISPLAY_ROW_ROLE,
                DisplayRow(
                    summary.display_title,
                    f"{KIND_NAMES[summary.kind]} · {changed}",
                    summary.source_subject or summary.action_title or "",
                    glyph="pencil",
                ),
            )
            self.list.addItem(item)
        ids = [summary.public_id for summary in drafts]
        if previous is not None and previous.public_id in ids:
            self.list.setCurrentRow(ids.index(previous.public_id))
        elif drafts:
            self.list.setCurrentRow(min(max(row, 0), len(drafts) - 1))
        self.list.setVisible(bool(drafts))
        self.empty.setVisible(not drafts)
        self._update_buttons()
        self._filter(self.search.text())

    def _selection_changed(self, row: int) -> None:
        self._update_buttons()
        selected = self.selected()
        self.preview_title.setText("Select a draft" if selected is None else selected.display_title)
        self.preview_body.clear()
        self.preview_meta.clear()
        if selected is None:
            self.editor_host.setCurrentIndex(0)
        if selected is not None:
            self.preview_requested.emit(selected)

    def show_preview(self, draft: Draft) -> None:
        selected = self.selected()
        if selected is not None and selected.public_id == draft.public_id:
            self.preview_title.setText(draft.display_title)
            self.preview_meta.setText(
                "To: " + draft.to_text if draft.to_text else KIND_NAMES[draft.kind]
            )
            self.preview_body.setPlainText(draft.body)

    def _filter(self, text: str) -> None:
        query = text.casefold().strip()
        first: int | None = None
        for row in range(self.list.count()):
            item = self.list.item(row)
            if item is not None:
                visible = query in item.text().casefold()
                item.setHidden(not visible)
                if visible and first is None:
                    first = row
        selected = self.list.currentItem()
        if selected is None or selected.isHidden():
            self.list.setCurrentRow(-1 if first is None else first)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._update_buttons()

    def _update_buttons(self) -> None:
        chosen = self.selected() is not None and not self._busy
        self.new_button.setEnabled(not self._busy)
        self.open_button.setEnabled(chosen)
        self.delete_button.setEnabled(chosen)

    def _request_new(self, kind: DraftKind) -> None:
        if not self._busy:
            self.draft_requested.emit(NEW, kind)

    def _request(self, kind: str) -> None:
        summary = self.selected()
        if summary is not None and not self._busy:
            self.draft_requested.emit(kind, summary)
