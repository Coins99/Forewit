"""The owner's writing: the Notes view and the Drafts view, newest first, with New and Open.

One page serves both sidebar rows, as Actions serves Waiting: Notes lists the owner's notes,
with a project filter (ADR 0027), and Drafts lists replies, emails and messages. The page
filters the whole, uncapped list it was given, so switching views or projects is immediate.

Titles are the owner's text or, for replies, an email's subject, so rows are plain list-item
text and never tooltips, which Qt may render as rich text.
"""

from dataclasses import dataclass
from datetime import date
from typing import Final, cast
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
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

from mailbrief.domain.drafts import KIND_NAMES, Draft, DraftKind, DraftSummary, WritingView
from mailbrief.domain.projects import Project
from mailbrief.ui.deadline_text import moment_text
from mailbrief.ui.hairline import HairlineSplitter
from mailbrief.ui.labels import outline_button, plain_label, wrap_label
from mailbrief.ui.layouts import take_widget
from mailbrief.ui.list_rows import DISPLAY_ROW_ROLE, DisplayRow, DisplayRowDelegate
from mailbrief.ui.lists import ActivatingList, clear_list
from mailbrief.ui.theme import TITLE_PX, ui_font

NEW = "new"
OPEN = "open"
DELETE = "delete"
# The Drafts view's New menu; notes start from the Notes view's own New note.
NEW_KINDS = (DraftKind.EMAIL, DraftKind.MESSAGE)
ALL_NOTES: Final = "all"
NO_PROJECT: Final = "unassigned"


@dataclass(frozen=True, slots=True)
class _ViewText:
    heading: str
    search: str
    new: str
    empty: str
    noun: str
    select: str
    stamp: str


_TEXT: Final = {
    WritingView.NOTES: _ViewText(
        heading="Your notes",
        search="Search notes",
        new="Ne&w note",
        empty="No notes yet. Start one with New note, or capture one from any page.",
        noun="notes",
        select="Select a note",
        stamp="LOCAL NOTE",
    ),
    WritingView.DRAFTS: _ViewText(
        heading="Your drafts",
        search="Search drafts",
        new="Ne&w draft",
        empty="No drafts yet. Start one with New, or from a brief item or action.",
        noun="drafts",
        select="Select a draft",
        stamp="LOCAL DRAFT · NOT SENT",
    ),
}


def describe(summary: DraftSummary, zone: ZoneInfo, today: date) -> str:
    """One plain line: kind, title, when it changed, what is left to fill, its project and
    its action."""
    updated = moment_text(summary.updated_at_utc, zone, today=today)
    parts = [f"{KIND_NAMES[summary.kind]} · {summary.display_title} — updated {updated}"]
    count = summary.placeholder_count
    if count:
        parts.append(f"{count} placeholder{'' if count == 1 else 's'}")
    if summary.project_name:
        parts.append(f"in {summary.project_name}")
    if summary.action_title:
        parts.append(f"for “{summary.action_title}”")
    return " · ".join(parts)


def _links_line(summary: DraftSummary) -> str:
    """A row's third line: a note's project and task; a draft's email or action."""
    if summary.kind is DraftKind.NOTE:
        return " · ".join(
            part
            for part in (
                summary.project_name,
                None if summary.action_title is None else f"For “{summary.action_title}”",
            )
            if part
        )
    return summary.source_subject or summary.action_title or ""


class DraftsPanel(QWidget):
    """Lists drafts or notes and asks the window to act; it changes nothing itself.

    ``draft_requested(kind, value)`` carries NEW with a DraftKind, or OPEN or DELETE with
    the selected DraftSummary.
    """

    draft_requested = Signal(str, object)
    preview_requested = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("draftsPanel")
        self.setAccessibleName("Drafts and notes")
        self._view = WritingView.DRAFTS
        self._all: tuple[DraftSummary, ...] = ()
        self._drafts: tuple[DraftSummary, ...] = ()  # The rows shown, in order.
        self._projects: tuple[Project, ...] = ()
        self._zone = ZoneInfo("UTC")
        self._today = date(2000, 1, 1)
        self._busy = False
        self.task_context: str | None = None
        self._zone = ZoneInfo("UTC")
        layout = QVBoxLayout(self)
        self.clear_context = outline_button("All notes", "clearNoteContext")
        self.clear_context.setAccessibleName("Clear linked-task note filter")
        self.clear_context.clicked.connect(lambda: self.set_task_context(None))
        layout.addWidget(self.clear_context)
        self.clear_context.hide()
        layout.setContentsMargins(0, 0, 0, 0)
        self.splitter = HairlineSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        list_pane = QWidget()
        list_pane.setObjectName("draftsListPane")
        list_pane.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        left = QVBoxLayout(list_pane)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(0)
        self.heading = QLabel("Your drafts", list_pane)
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setFont(ui_font(TITLE_PX, medium=True))
        self.heading.hide()
        self.search = QLineEdit()
        self.search.setObjectName("draftSearch")
        self.search.setAccessibleName("Search drafts")
        self.search.setPlaceholderText("Search drafts")
        self.search.textChanged.connect(self._filter)
        toolbar = QWidget()
        toolbar.setObjectName("draftsToolbar")
        toolbar.setFixedHeight(52)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(14, 9, 14, 9)
        toolbar_layout.addWidget(self.search, 1)
        self.project_filter = QComboBox()
        self.project_filter.setObjectName("noteProjectFilter")
        self.project_filter.setAccessibleName("Show notes in a project")
        self.project_filter.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.project_filter.setMinimumContentsLength(7)
        self.project_filter.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.project_filter.setFont(ui_font(9))
        self.project_filter.currentIndexChanged.connect(lambda _index: self._rebuild())
        self.project_filter.hide()
        toolbar_layout.addWidget(self.project_filter)
        self.draft_count = plain_label("0 drafts", tone="muted", px=9)
        toolbar_layout.addWidget(self.draft_count)
        left.addWidget(toolbar)
        self.search.setFont(ui_font(9))
        self.list = ActivatingList()
        self.list.setAccessibleName("Drafts")
        self.list.setObjectName("draftList")
        self.list.setFrameShape(QFrame.Shape.NoFrame)
        self.list.setItemDelegate(DisplayRowDelegate(self.list))
        self.list.currentRowChanged.connect(self._selection_changed)
        self.list.itemActivated.connect(lambda _item: self._request(OPEN))
        left.addWidget(self.list, 1)
        # With nothing to list, a centred message fills the page in place of the list.
        self.empty = QLabel(_TEXT[WritingView.DRAFTS].empty)
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
        # With no menu (the Notes view), a click starts a note.
        self.new_button.clicked.connect(self._new_clicked)
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
        self.preview_stamp = plain_label("LOCAL DRAFT · NOT SENT", tone="muted", px=10)
        right.addWidget(self.preview_stamp)
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
        self._fill_project_filter()
        self._update_buttons()

    # Views.

    def view(self) -> WritingView:
        return self._view

    def show_view(self, view: WritingView) -> None:
        """Show the notes or the drafts; the list keeps the same search text."""
        if view is self._view:
            return
        self._view = view
        text = _TEXT[view]
        notes = view is WritingView.NOTES
        self.clear_context.setVisible(notes and self.task_context is not None)
        self.heading.setText(text.heading)
        self.search.setPlaceholderText(text.search)
        self.search.setAccessibleName(text.search)
        self.list.setAccessibleName("Notes" if notes else "Drafts")
        self.empty.setText(text.empty)
        self.preview_stamp.setText(text.stamp)
        self.new_button.setText(text.new)
        self.new_button.setAccessibleName("New note" if notes else "New draft")
        # Qt takes no menu as null; the stubs only admit a QMenu.
        self.new_button.setMenu(cast(QMenu, None) if notes else self.new_menu)
        self.delete_button.setText("Delete no&te" if notes else "Delete draf&t")
        self.project_filter.setVisible(notes)
        self._rebuild(fresh=True)  # The new view opens on its newest item.

    # Projects.

    def show_projects(self, projects: tuple[Project, ...]) -> None:
        """The owner's projects, for the Notes view's filter and the rows' project names.

        Every refresh of the Actions page passes them; unchanged ones change nothing.
        """
        if projects == self._projects:
            return
        self._projects = projects
        self._fill_project_filter()
        self._rebuild()

    def _fill_project_filter(self) -> None:
        chosen = self.project_filter.currentData()
        self.project_filter.blockSignals(True)
        self.project_filter.clear()
        self.project_filter.addItem("All notes", ALL_NOTES)
        self.project_filter.addItem("No project", NO_PROJECT)
        for project in self._projects:
            suffix = " (archived)" if project.archived_at_utc else ""
            self.project_filter.addItem(f"Project: {project.name}{suffix}", project.public_id)
        self.project_filter.setCurrentIndex(max(0, self.project_filter.findData(chosen)))
        self.project_filter.blockSignals(False)

    def filter_project(self, choice: str) -> None:
        """Show all notes, those with no project, or one project's (by public ID)."""
        self.project_filter.setCurrentIndex(max(0, self.project_filter.findData(choice)))

    def project_for_new_note(self) -> str | None:
        """The project a new note starts in: the filter's, when it takes new notes. It
        changes nothing; when the new note doesn't match the filter (an archived project's),
        keep_shown() widens the filter once the note opens."""
        choice = self.project_filter.currentData()
        for project in self._projects:
            if project.public_id == choice and project.archived_at_utc is None:
                return project.public_id
        return None

    def keep_shown(self, draft: Draft) -> None:
        """Make sure the Notes filter shows ``draft``, as after moving it to another project:
        a filter that would hide the note being edited goes back to All notes."""
        choice = self.project_filter.currentData()
        if self._view is not WritingView.NOTES or draft.kind is not DraftKind.NOTE:
            return
        if self.task_context is not None and draft.action_public_id != self.task_context:
            self.set_task_context(None)
        if choice == ALL_NOTES:
            return
        wanted = None if choice == NO_PROJECT else choice
        if draft.project_public_id != wanted:
            self.filter_project(ALL_NOTES)

    # The list.

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
        take_widget(self.delete_button)
        self._list_buttons.insertWidget(1, self.delete_button)
        self.open_button.show()
        self.delete_button.show()

    def totals(self) -> dict[WritingView, int]:
        """How many live notes and drafts there are, whatever the filters show."""
        totals = dict.fromkeys(WritingView, 0)
        for summary in self._all:
            totals[WritingView.of(summary.kind)] += 1
        return totals

    def show_drafts(self, drafts: tuple[DraftSummary, ...], zone: ZoneInfo, today: date) -> None:
        """Replace the whole list (every kind); the view and filter choose what shows."""
        self._all = drafts
        self._zone = zone
        self._today = today
        self._rebuild()

    def _shown(self) -> tuple[DraftSummary, ...]:
        kinds = self._view.kinds
        rows = [summary for summary in self._all if summary.kind in kinds]
        choice = self.project_filter.currentData()
        if self._view is WritingView.NOTES and choice != ALL_NOTES:
            wanted = None if choice == NO_PROJECT else choice
            rows = [summary for summary in rows if summary.project_public_id == wanted]
        if self._view is WritingView.NOTES and self.task_context is not None:
            rows = [summary for summary in rows if summary.action_public_id == self.task_context]
        return tuple(rows)

    def _rebuild(self, *, fresh: bool = False) -> None:
        """Show the view's rows, keeping the selection on the same draft when it remains;
        ``fresh`` (a new view) starts on the first row instead.

        The list's signals are held while it refills, so keeping the selection never passes
        through none, which would hide the inline editor and take the keyboard's focus. The
        selection is announced once, at the end, when it moved to another draft or to none,
        or when the editor isn't showing it yet.
        """
        previous = None if fresh else self.selected()
        row = -1 if fresh else self.list.currentRow()
        drafts = self._shown()
        self._drafts = drafts
        self.draft_count.setText(f"{len(drafts)} {_TEXT[self._view].noun}")
        self.list.blockSignals(True)
        try:
            clear_list(self.list)
            for summary in drafts:
                changed = moment_text(summary.updated_at_utc, self._zone, today=self._today)
                item = QListWidgetItem(describe(summary, self._zone, self._today))
                item.setData(
                    DISPLAY_ROW_ROLE,
                    DisplayRow(
                        summary.display_title,
                        f"{KIND_NAMES[summary.kind]} · {changed}",
                        _links_line(summary),
                        glyph="pencil",
                    ),
                )
                self.list.addItem(item)
            ids = [summary.public_id for summary in drafts]
            if previous is not None and previous.public_id in ids:
                self.list.setCurrentRow(ids.index(previous.public_id))
            elif drafts:
                self.list.setCurrentRow(min(max(row, 0), len(drafts) - 1))
            self._filter(self.search.text())
        finally:
            self.list.blockSignals(False)
        self.list.setVisible(bool(drafts))
        self.empty.setVisible(not drafts)
        current = self.selected()
        kept = (
            previous is not None and current is not None and current.public_id == previous.public_id
        )
        if kept and current is not None and self.editor_host.currentIndex() != 0:
            self.preview_title.setText(current.display_title)
            self._update_buttons()
        else:
            self._selection_changed(self.list.currentRow())

    def _selection_changed(self, row: int) -> None:
        self._update_buttons()
        selected = self.selected()
        self.preview_title.setText(
            _TEXT[self._view].select if selected is None else selected.display_title
        )
        self.preview_body.clear()
        self.preview_meta.clear()
        if selected is None:
            self.editor_host.setCurrentIndex(0)
        if selected is not None:
            self.preview_requested.emit(selected)

    def set_task_context(self, public_id: str | None) -> None:
        self.task_context = public_id
        self.clear_context.setVisible(public_id is not None and self._view is WritingView.NOTES)
        self._rebuild()

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

    def _new_clicked(self) -> None:
        if self._view is WritingView.NOTES:
            self._request_new(DraftKind.NOTE)

    def _request_new(self, kind: DraftKind) -> None:
        if not self._busy:
            self.draft_requested.emit(NEW, kind)

    def _request(self, kind: str) -> None:
        summary = self.selected()
        if summary is not None and not self._busy:
            self.draft_requested.emit(kind, summary)
