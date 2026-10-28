"""Write one draft or note: autosave as you type, saved versions, copy, export and AI.

The editor never writes anything itself. It asks the window, which runs every draft write
through one serialized queue, and answers through the methods below. Source subjects and
senders come from email, so they are shown only in plain-text labels.
"""

from datetime import date
from zoneinfo import ZoneInfo

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.drafting import DraftContextPart, DraftingOptions, DraftingPreview
from mailbrief.domain.drafts import (
    DRAFT_BODY_MAX_CHARS,
    DRAFT_RECIPIENTS_MAX_CHARS,
    DRAFT_TITLE_MAX_CHARS,
    EMAIL_KINDS,
    KIND_NAMES,
    Draft,
    DraftEdit,
    DraftSource,
    DraftVersion,
    DraftVersionInfo,
    DraftVersionOrigin,
    export_filename,
    export_markdown,
    export_text,
    placeholders,
    recipient_warnings,
    still_to_fill,
)
from mailbrief.ui.deadline_text import moment_text
from mailbrief.ui.dialog_guard import DialogGuard
from mailbrief.ui.drafting_panel import DraftingPanel
from mailbrief.ui.labels import outline_button, plain_label
from mailbrief.ui.lists import SafeList, clear_list
from mailbrief.ui.theme import ui_font

DEBOUNCE_MS = 1_500
MAX_WAIT_MS = 10_000
RETRY_MS = 5_000
CONFLICT = "This draft changed elsewhere. Your text is still here."
NOT_SAVED = "Not saved; retrying"
_SAVING = "Saving…"
_TOO_LONG = (
    f"The body is over {DRAFT_BODY_MAX_CHARS:,} characters. Autosave is paused until it is shorter."
)
_DISCARD_HINT = "Press Close again to close without saving your latest changes."
_DISCARD_HINT_LOWER = "press Close again to close without saving your latest changes."
_ORIGINS = {
    DraftVersionOrigin.CREATED: "created",
    DraftVersionOrigin.EDITED: "saved",
    DraftVersionOrigin.RESTORED: "restored",
    DraftVersionOrigin.GENERATED: "generated",
}


def _plain(text: str = "") -> QLabel:
    """Draft and source text must never become rich text or a link."""
    label = QLabel(text)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def gmail_link(source: DraftSource) -> str | None:
    """The source's link when it opens Gmail; nothing else is ever opened."""
    link = source.web_link
    return str(link) if link.scheme == "https" and link.host == "mail.google.com" else None


class DraftEditor(QDialog):
    """Signals, each answered by the window through the methods named:

    - ``autosave_requested(edit)``: saved() or save_failed() or show_conflict();
    - ``checkpoint_requested()``: checkpointed();
    - ``versions_requested()`` and ``version_requested(number)``: show_versions() and
      show_version();
    - ``restore_requested(number)``: restored() or restore_failed();
    - ``save_as_new_requested(edit)``: continue_as();
    - ``export_requested(path, text)``: set_status();
    - ``close_requested(edit or None)``: finish_closed() or cancel_close();
    - ``ai_parts_requested()``: offer_ai(), or ai_failed();
    - ``ai_prepare_requested(options)``: show_ai_preview(), or ai_failed();
    - ``ai_generate_requested(agreed)``: ai_generated(), or ai_failed();
    - ``ai_cancel_requested()``: stops a generation, which then answers ai_failed().
    """

    autosave_requested = Signal(object)
    checkpoint_requested = Signal()
    versions_requested = Signal()
    version_requested = Signal(int)
    restore_requested = Signal(int)
    save_as_new_requested = Signal(object)
    export_requested = Signal(str, str)
    close_requested = Signal(object)
    ai_parts_requested = Signal()
    ai_prepare_requested = Signal(object)
    ai_generate_requested = Signal(bool)
    ai_cancel_requested = Signal()

    def __init__(
        self,
        parent: QWidget,
        *,
        debounce_ms: int = DEBOUNCE_MS,
        max_wait_ms: int = MAX_WAIT_MS,
        retry_ms: int = RETRY_MS,
    ) -> None:
        super().__init__(parent)
        # Its pickers and boxes open behind this cover, never modal, so quitting works.
        self.dialog_guard = DialogGuard(self)
        self.setWindowTitle("Draft")
        self.setObjectName("draftEditorDialog")
        self.setFont(ui_font(10))
        self.setAccessibleName("Draft editor")
        self.resize(720, 640)
        self.draft: Draft | None = None  # As last saved; its revision is what writes send.
        self.embedded = False
        self.reply_mode = False
        self._zone = ZoneInfo("UTC")
        self._loading = False
        self._dirty = False  # Changed since the last text sent to be saved.
        self._conflict = False
        self._closing = False
        self._locked = False  # A restore, close or AI generation is waiting for the window.
        self._ai_ready = False  # A Groq model and key are configured.
        self._ai_busy = False  # A preview is shown or Groq is writing.
        self._discard_armed = False
        self._versions: tuple[DraftVersionInfo, ...] = ()
        self._picker: QFileDialog | None = None
        root = QVBoxLayout(self)
        self._root = root
        root.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        content = QWidget()
        self.scroll_area.setWidget(content)
        root.addWidget(self.scroll_area)
        layout = QVBoxLayout(content)
        self._content_layout = layout
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        self.heading = plain_label("Draft editor", px=21, medium=True)
        self.heading.setObjectName("draftEditorHeading")
        layout.addWidget(self.heading)
        self.description = plain_label(
            "Write locally. Your changes save as you type.", tone="muted", px=12
        )
        layout.addWidget(self.description)
        context = QHBoxLayout()
        self.context = _plain()
        context.addWidget(self.context, 1)
        self.gmail_button = QPushButton("Open in &Gmail")
        self.gmail_button.clicked.connect(self._open_gmail)
        context.addWidget(self.gmail_button)
        layout.addLayout(context)
        self._fields = QWidget()
        form = QFormLayout(self._fields)
        form.setContentsMargins(0, 0, 0, 0)
        self._form = form
        self.to_edit = QLineEdit()
        self.to_edit.setMaxLength(DRAFT_RECIPIENTS_MAX_CHARS)
        self.to_edit.setAccessibleName("To")
        form.addRow("&To", self.to_edit)
        self.cc_edit = QLineEdit()
        self.cc_edit.setMaxLength(DRAFT_RECIPIENTS_MAX_CHARS)
        self.cc_edit.setAccessibleName("Cc")
        form.addRow("C&c", self.cc_edit)
        self.recipient_warning = _plain()
        form.addRow("", self.recipient_warning)
        self.title_label = QLabel("&Subject")
        self.title_edit = QLineEdit()
        self.title_edit.setObjectName("draftSubject")
        self.title_edit.setMaxLength(DRAFT_TITLE_MAX_CHARS)
        self.title_label.setBuddy(self.title_edit)
        form.addRow(self.title_label, self.title_edit)
        title_row = form.takeRow(self.title_edit)
        form.insertRow(0, self.title_label, self.title_edit)
        del title_row
        self.body = QPlainTextEdit()
        self.body.setObjectName("draftMessage")
        self.body.setMinimumHeight(160)
        self.body.setAccessibleName("Body")
        self.body.setTabChangesFocus(True)
        self.body_label = QLabel("&Body")
        self.body_label.setBuddy(self.body)
        self.counter = _plain()
        self.placeholder_line = _plain()
        self.missing_line = _plain()
        layout.addWidget(self._fields)
        self.reply_recipient = plain_label("", px=13)
        self.reply_recipient.setObjectName("replyRecipient")
        self.reply_recipient.hide()
        layout.addWidget(self.reply_recipient)
        layout.addWidget(self.body_label)
        layout.addWidget(self.body, 1)
        for line in (self.counter, self.placeholder_line, self.missing_line):
            layout.addWidget(line)
        buttons = QGridLayout()
        self._buttons = buttons
        self.copy_text_button = QPushButton("Copy te&xt")
        self.copy_subject_button = QPushButton("Copy s&ubject")
        self.export_button = QPushButton("&Export…")
        self.versions_button = QPushButton("&Versions…")
        self.checkpoint_button = QPushButton("Save versio&n")
        self.checkpoint_button.setProperty("variant", "primary")
        self.ai_button = QPushButton("Write with A&I…")
        self.close_button = QPushButton("C&lose")
        for number, button in enumerate(
            (
                self.copy_text_button,
                self.copy_subject_button,
                self.export_button,
                self.versions_button,
                self.checkpoint_button,
                self.ai_button,
                self.close_button,
            )
        ):
            buttons.addWidget(button, number // 3, number % 3)
        layout.addLayout(buttons)
        self.more_button = outline_button("More", "draftMoreButton")
        self.more_button.setAccessibleName("More draft tools")
        self.more_menu = QMenu(self.more_button)
        self._more_actions = []
        for button in (
            self.copy_text_button,
            self.copy_subject_button,
            self.export_button,
            self.versions_button,
            self.ai_button,
        ):
            action = self.more_menu.addAction(button.text().replace("&", ""))
            action.triggered.connect(button.click)
            self._more_actions.append((action, button))
        self.more_menu.aboutToShow.connect(self._update_more)
        self.more_button.setMenu(self.more_menu)
        self.more_button.hide()
        buttons.addWidget(self.more_button, 0, 0)
        self.save_as_new_button = QPushButton("Save as ne&w draft")
        self.save_as_new_button.hide()
        layout.addWidget(self.save_as_new_button)
        self.versions_panel = QWidget()
        versions = QVBoxLayout(self.versions_panel)
        versions.setContentsMargins(0, 0, 0, 0)
        versions.addWidget(_plain("Saved versions, newest first:"))
        self.versions_list = SafeList()
        self.versions_list.setAccessibleName("Saved versions")
        versions.addWidget(self.versions_list)
        self.version_preview = QPlainTextEdit()
        self.version_preview.setReadOnly(True)
        self.version_preview.setAccessibleName("Selected version")
        versions.addWidget(self.version_preview)
        self.restore_button = QPushButton("&Restore this version…")
        versions.addWidget(self.restore_button)
        self.confirm_panel = QWidget()
        confirm = QHBoxLayout(self.confirm_panel)
        confirm.setContentsMargins(0, 0, 0, 0)
        self.confirm_label = _plain()
        self.confirm_button = QPushButton("Restore")
        self.keep_button = QPushButton("Keep my text")
        confirm.addWidget(self.confirm_label, 1)
        confirm.addWidget(self.confirm_button)
        confirm.addWidget(self.keep_button)
        versions.addWidget(self.confirm_panel)
        self.confirm_panel.hide()
        self.versions_panel.hide()
        layout.addWidget(self.versions_panel, 1)
        self.ai_panel = DraftingPanel()
        self.ai_panel.hide()
        layout.addWidget(self.ai_panel)
        self.status = _plain()
        self.status.setFont(ui_font(9))
        layout.addWidget(self.status)
        self.editor_header = QWidget()
        self.editor_header.setObjectName("draftEditorHeader")
        header_layout = QVBoxLayout(self.editor_header)
        header_layout.setContentsMargins(23, 16, 23, 16)
        layout.removeWidget(self.heading)
        header_layout.addWidget(self.heading)
        root.insertWidget(0, self.editor_header)
        self.editor_footer = QWidget()
        self.editor_footer.setObjectName("draftEditorFooter")
        footer_layout = QVBoxLayout(self.editor_footer)
        footer_layout.setContentsMargins(23, 12, 23, 10)
        layout.removeItem(buttons)
        footer_layout.addLayout(buttons)
        layout.removeWidget(self.status)
        footer_layout.addWidget(self.status)
        root.addWidget(self.editor_footer)
        self.cc_options = self.more_menu.addAction("Show Cc")
        self.cc_options.triggered.connect(lambda: self._form.setRowVisible(self.cc_edit, True))
        source_action = self.more_menu.addAction("Open source in Gmail")
        source_action.triggered.connect(self.gmail_button.click)
        self._more_actions.append((source_action, self.gmail_button))
        self._debounce = self._timer(debounce_ms)
        self._max_wait = self._timer(max_wait_ms)
        self._retry = self._timer(retry_ms)
        for field in (self.to_edit, self.cc_edit, self.title_edit):
            field.textChanged.connect(self._changed)
        self.body.textChanged.connect(self._changed)
        self.copy_text_button.clicked.connect(self._copy_text)
        self.copy_subject_button.clicked.connect(self._copy_subject)
        self.export_button.clicked.connect(self._choose_export)
        self.versions_button.clicked.connect(self._toggle_versions)
        self.checkpoint_button.clicked.connect(self._checkpoint)
        self.close_button.clicked.connect(self.reject)
        self.save_as_new_button.clicked.connect(self._save_as_new)
        self.versions_list.currentRowChanged.connect(self._version_selected)
        self.restore_button.clicked.connect(self._ask_restore)
        self.confirm_button.clicked.connect(self._confirm_restore)
        self.keep_button.clicked.connect(self.confirm_panel.hide)
        self.ai_button.clicked.connect(self._toggle_ai)
        self.ai_panel.prepare_requested.connect(self._prepare_ai)
        self.ai_panel.send_requested.connect(self._send_ai)
        self.ai_panel.cancel_requested.connect(self._cancel_ai)

    def set_embedded(self, parent: QWidget | None, *, window: QWidget) -> None:
        """Use the same autosave/version editor in the Drafts pane or a guarded dialog."""
        self.embedded = parent is not None
        self.setProperty("inline", self.embedded)
        self.reply_mode = parent is not None and parent.objectName() == "inlineReplyCard"
        self.setProperty("reply", self.reply_mode)
        self.body.setMinimumHeight(160)
        self.body.setMaximumHeight(16777215)
        self.reply_recipient.setVisible(self.reply_mode)
        self.setMinimumHeight(0)
        self.setMaximumHeight(16777215)
        self.setParent(parent or window)
        self.setWindowFlags(Qt.WindowType.Widget if parent is not None else Qt.WindowType.Dialog)
        self.heading.setText("DRAFT · NOT SENT" if self.embedded else "Draft editor")
        self.heading.setFont(ui_font(9 if self.embedded else 21, medium=True))
        self.editor_header.setFixedHeight(49 if self.embedded else 68)
        self.editor_footer.setMinimumHeight(58)
        if (header_layout := self.editor_header.layout()) is not None:
            header_layout.setContentsMargins(23, 16, 23, 16)
        if (footer_layout := self.editor_footer.layout()) is not None:
            footer_layout.setContentsMargins(23, 12, 23, 10)
        self._content_layout.setContentsMargins(23, 5 if self.embedded else 16, 23, 12)
        self._content_layout.setSpacing(0 if self.embedded else 12)
        self.context.setVisible(not self.embedded)
        self.description.setVisible(not self.embedded)
        self.scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self._fields.setProperty("inline", self.embedded)
        self._fields.style().unpolish(self._fields)
        self._fields.style().polish(self._fields)
        self.title_edit.setFont(ui_font(19 if self.embedded else 13, medium=self.embedded))
        self.body.setFont(ui_font(11 if self.embedded else 13))
        self.body.setProperty("reply", self.reply_mode)
        self.title_label.setVisible(not self.embedded)
        self.body_label.setVisible(not self.embedded)
        self.more_button.setVisible(self.embedded)
        for button in (
            self.copy_text_button,
            self.copy_subject_button,
            self.export_button,
            self.versions_button,
            self.close_button,
        ):
            button.setVisible(not self.embedded)
        self.checkpoint_button.setText("Save draft" if self.embedded else "Save versio&n")
        for field in (self.title_edit, self.body):
            field.setProperty("inline", self.embedded)
            field.style().unpolish(field)
            field.style().polish(field)
        self.counter.setVisible(not self.embedded)
        if self.embedded:
            self._buttons.addWidget(self.checkpoint_button, 0, 2, Qt.AlignmentFlag.AlignRight)
            self._buttons.addWidget(self.more_button, 0, 1, Qt.AlignmentFlag.AlignRight)
            self.ai_button.hide()
            for number in range(3):
                self._buttons.setColumnStretch(number, 1 if number == 0 else 0)
            self._fields.setVisible(not self.reply_mode)
            if self.reply_mode:
                self.setFixedHeight(290)
                self.body.setFixedHeight(130)
                self.editor_header.setFixedHeight(40)
                for section in (self.editor_header, self.editor_footer):
                    if (section_layout := section.layout()) is not None:
                        section_layout.setContentsMargins(16, 10, 16, 10)
                self._content_layout.setContentsMargins(16, 0, 16, 0)
                self.close_button.setText("Cancel")
                self.close_button.show()
                self._buttons.addWidget(self.more_button, 0, 0, Qt.AlignmentFlag.AlignLeft)
                self._buttons.addWidget(self.close_button, 0, 1, Qt.AlignmentFlag.AlignRight)
        else:
            self._fields.show()
            self.close_button.setText("C&lose")
            for number, button in enumerate(
                (
                    self.copy_text_button,
                    self.copy_subject_button,
                    self.export_button,
                    self.versions_button,
                    self.checkpoint_button,
                    self.ai_button,
                    self.close_button,
                )
            ):
                self._buttons.addWidget(button, number // 3, number % 3)
            for column in range(3):
                self._buttons.setColumnStretch(column, 0)

    def _update_more(self) -> None:
        for action, button in self._more_actions:
            action.setEnabled(button.isEnabled())

    def _timer(self, interval_ms: int) -> QTimer:
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.setInterval(interval_ms)
        timer.timeout.connect(self._autosave_now)
        return timer

    def set_timing(self, *, debounce_ms: int, max_wait_ms: int, retry_ms: int) -> None:
        """Autosave 'debounce_ms' after the last change, at least every 'max_wait_ms' while
        changes continue, and 'retry_ms' after a failed save."""
        self._debounce.setInterval(debounce_ms)
        self._max_wait.setInterval(max_wait_ms)
        self._retry.setInterval(retry_ms)

    # State the window reads.

    @property
    def in_conflict(self) -> bool:
        return self._conflict

    @property
    def dirty(self) -> bool:
        return self._dirty

    def current_edit(self) -> DraftEdit | None:
        """The text as it stands, or None while the body is too long to save."""
        if len(self.body.toPlainText()) > DRAFT_BODY_MAX_CHARS:
            return None
        email = self.draft is not None and self.draft.kind in EMAIL_KINDS
        return DraftEdit(
            title=self.title_edit.text(),
            to_text=self.to_edit.text() if email else "",
            cc_text=self.cc_edit.text() if email else "",
            body=self.body.toPlainText(),
        )

    def final_edit(self) -> DraftEdit | None:
        """Unsaved text to save before quitting, if any can be saved."""
        if self.draft is None or self._conflict or not self._dirty:
            return None
        return self.current_edit()

    # Loading.

    def load(self, draft: Draft, zone: ZoneInfo) -> None:
        """Show a draft; nothing is kept from an earlier one."""
        self._stop_timers()
        self._zone = zone
        self.draft = draft
        self._dirty = self._conflict = self._closing = self._discard_armed = False
        self._set_locked(False)
        email = draft.kind in EMAIL_KINDS
        name = KIND_NAMES[draft.kind]
        self.setWindowTitle(f"{name} draft")
        for field in (self.to_edit, self.cc_edit, self.recipient_warning):
            self._form.setRowVisible(field, email)
        self.title_label.setText("&Subject" if email else "&Title")
        self.copy_subject_button.setVisible(email and not self.embedded)
        self._show_context(draft)
        self.context.setVisible(not self.embedded)
        self.gmail_button.setVisible(not self.embedded and bool(draft.sources))
        if self.embedded:
            self._form.setRowVisible(self.cc_edit, email and bool(draft.cc_text))
            self.heading.setText(
                "REPLY DRAFT · NOT SENT" if self.reply_mode else "DRAFT · NOT SENT"
            )
        self._set_text(draft.content())
        self.reply_recipient.setText("To " + draft.to_text)
        self.save_as_new_button.hide()
        self.versions_panel.hide()
        self.confirm_panel.hide()
        self._ai_busy = False
        self.ai_panel.reset()
        self.ai_panel.hide()
        self.missing_line.clear()
        self.missing_line.hide()
        self._update_buttons()
        self.status.setText(f"Saved {self._time(draft)}")
        if not self.embedded or self.reply_mode or not draft.title:
            (self.body if draft.title else self.title_edit).setFocus()

    def _show_context(self, draft: Draft) -> None:
        parts: list[str] = []
        source = draft.sources[0] if draft.sources else None
        if source is not None:
            parts.append(
                f"From email: {source.subject or '(no subject)'} — {source.sender_address}"
            )
            if not source.available:
                parts.append("source no longer in local mail")
        if draft.action_title:
            parts.append(f"For action: “{draft.action_title}”")
        self.context.setText(" · ".join(parts) or "Not linked to an email or action.")
        self.gmail_button.setVisible(source is not None and gmail_link(source) is not None)
        self.gmail_button.setEnabled(source is not None and gmail_link(source) is not None)

    def _set_text(self, content: DraftEdit) -> None:
        self._loading = True
        try:
            self.to_edit.setText(content.to_text)
            self.cc_edit.setText(content.cc_text)
            self.title_edit.setText(content.title)
            self.body.setPlainText(content.body)
        finally:
            self._loading = False
        self._update_live()

    def _time(self, draft: Draft) -> str:
        return f"{draft.updated_at_utc.astimezone(self._zone):%H:%M}"

    # Typing and autosave.

    def _changed(self) -> None:
        if self._loading:
            return
        self._update_live()
        if self.draft is None or self._conflict:
            return
        self._dirty = True
        self._discard_armed = False
        if self.current_edit() is None:
            self._stop_timers()
            self.status.setText(_TOO_LONG)
            return
        self.status.setText(_SAVING)
        self._debounce.start()
        if not self._max_wait.isActive():
            self._max_wait.start()

    def _update_live(self) -> None:
        body = self.body.toPlainText()
        self.counter.setText(f"{len(body):,} / {DRAFT_BODY_MAX_CHARS:,} characters")
        self.counter.setVisible(not self.embedded or len(body) > DRAFT_BODY_MAX_CHARS - 1000)
        warnings = recipient_warnings(f"{self.to_edit.text()},{self.cc_edit.text()}")
        self.recipient_warning.setText(
            "Check these recipients: " + "; ".join(warnings) if warnings else ""
        )
        self._form.setRowVisible(self.recipient_warning, bool(warnings))
        found = self._placeholders()
        self.placeholder_line.setText("Placeholders to fill: " + ", ".join(found) if found else "")
        self.placeholder_line.setVisible(bool(found))

    def _placeholders(self) -> tuple[str, ...]:
        email = self.draft is not None and self.draft.kind in EMAIL_KINDS
        fields = [self.title_edit.text(), self.body.toPlainText()]
        if email:
            fields[:0] = [self.to_edit.text(), self.cc_edit.text()]
        return placeholders("\n".join(fields))

    def _stop_timers(self) -> None:
        for timer in (self._debounce, self._max_wait, self._retry):
            timer.stop()

    def _autosave_now(self) -> None:
        """Send the current text to be saved, when there is something that can be saved."""
        self._stop_timers()
        if self.draft is None or self._conflict or self._closing or not self._dirty:
            return
        edit = self.current_edit()
        if edit is None:
            self.status.setText(_TOO_LONG)
            return
        self._dirty = False
        self.status.setText(_SAVING)
        self.autosave_requested.emit(edit)

    def saved(self, draft: Draft, edit: DraftEdit) -> None:
        """An autosave of ``edit`` succeeded."""
        self.draft = draft
        self._discard_armed = False
        if not self._dirty and not self._closing and self.current_edit() == edit:
            self.status.setText(f"Saved {self._time(draft)}")

    def save_failed(self) -> None:
        """An autosave failed: keep the text and retry on the next change or soon."""
        self._dirty = True
        self.status.setText(NOT_SAVED)
        if not self._closing:
            self._retry.start()

    def show_conflict(self) -> None:
        """The draft changed elsewhere: stop autosaving and offer to save as a new draft."""
        self._conflict = True
        self._stop_timers()
        self.status.setText(CONFLICT)
        self.save_as_new_button.show()
        self._update_buttons()

    def continue_as(self, draft: Draft) -> None:
        """Keep editing ``draft``, the new copy saved after a conflict."""
        self.draft = draft
        self._conflict = False
        self._discard_armed = False
        self.save_as_new_button.hide()
        self._update_buttons()
        self._dirty = self.current_edit() != draft.content()
        self.status.setText(f"Saved as a new draft at {self._time(draft)}.")
        if self._dirty:
            self._debounce.start()

    def _save_as_new(self) -> None:
        edit = self.current_edit()
        if edit is None:
            self.status.setText(f"Shorten the body to {DRAFT_BODY_MAX_CHARS:,} characters first.")
            return
        self.status.setText(_SAVING)
        self.save_as_new_requested.emit(edit)

    # Versions.

    def _update_buttons(self) -> None:
        live = not self._conflict and not self._locked
        for button in (self.versions_button, self.checkpoint_button, self.restore_button):
            button.setEnabled(live)
        self.confirm_button.setEnabled(live)
        self.ai_button.setEnabled(live and self._ai_ready)

    def _set_locked(self, locked: bool) -> None:
        self._locked = locked
        self._fields.setEnabled(not locked)
        self.body.setEnabled(not locked)
        self.close_button.setEnabled(not locked)
        self.save_as_new_button.setEnabled(not locked)
        self._update_buttons()

    def set_switching(self, switching: bool) -> None:
        """Freeze typing while the owner-requested draft switch saves and loads."""
        self._set_locked(switching)

    def _checkpoint(self) -> None:
        if self.current_edit() is None:
            self.status.setText(_TOO_LONG)
            return
        self._autosave_now()
        self.checkpoint_requested.emit()

    def checkpointed(self, info: DraftVersionInfo | None) -> None:
        if info is None:
            self.status.setText("No changes since the last saved version.")
        else:
            self.status.setText(f"Saved version {info.number}.")
        if not self.versions_panel.isHidden():
            self.versions_requested.emit()

    def _toggle_versions(self) -> None:
        if not self.versions_panel.isHidden():
            self.versions_panel.hide()
            return
        self.versions_requested.emit()

    def show_versions(self, versions: tuple[DraftVersionInfo, ...], today: date) -> None:
        self._versions = versions
        self.confirm_panel.hide()
        self.versions_list.blockSignals(True)
        clear_list(self.versions_list)
        for info in versions:
            when = moment_text(info.created_at_utc, self._zone, today=today)
            preview = info.preview or "(empty)"
            made = info.generation
            how = (
                "" if made is None else f" by {made.model} ({made.tone.value}, {made.length.value})"
            )
            self.versions_list.addItem(
                QListWidgetItem(
                    f"Version {info.number} · {_ORIGINS[info.origin]}{how} {when} · "
                    f"{info.length:,} characters · {preview}"
                )
            )
        self.versions_list.blockSignals(False)
        self.version_preview.clear()
        self.versions_panel.show()
        if versions:
            self.versions_list.setCurrentRow(0)
        self.restore_button.setEnabled(bool(versions) and not self._conflict and not self._locked)

    def _selected_version(self) -> DraftVersionInfo | None:
        row = self.versions_list.currentRow()
        return self._versions[row] if 0 <= row < len(self._versions) else None

    def _version_selected(self, _row: int) -> None:
        self.confirm_panel.hide()
        info = self._selected_version()
        if info is not None:
            self.version_requested.emit(info.number)

    def show_version(self, version: DraftVersion) -> None:
        info = self._selected_version()
        if info is not None and info.number == version.number:
            self.version_preview.setPlainText(version.body)

    def _ask_restore(self) -> None:
        info = self._selected_version()
        if info is None:
            return
        self.confirm_label.setText(
            f"Replace your text with version {info.number}? "
            "Your current text is saved as a version first."
        )
        self.confirm_button.setText(f"Restore version {info.number}")
        self.confirm_panel.show()

    def _confirm_restore(self) -> None:
        info = self._selected_version()
        if info is None or self._conflict or self._locked:
            return
        if self.current_edit() is None:
            self.status.setText(_TOO_LONG)
            return
        self.confirm_panel.hide()
        self._autosave_now()
        self._set_locked(True)
        self.status.setText(f"Restoring version {info.number}…")
        self.restore_requested.emit(info.number)

    def restored(self, draft: Draft, number: int) -> None:
        """The restore succeeded: show the restored text."""
        self.draft = draft
        self._set_locked(False)
        self._set_text(draft.content())
        self._dirty = False
        self.status.setText(f"Restored version {number}. Your earlier text was saved as a version.")
        self.versions_requested.emit()

    def restore_failed(self, message: str) -> None:
        self._set_locked(False)
        if not self._conflict:
            self.status.setText(message)

    # Writing with AI (ADR 0013). The text is locked from Continue until the generation ends,
    # so what the preview shows is exactly what is sent.

    def set_ai_ready(self, ready: bool) -> None:
        """Whether a Groq model and key are configured; without them the button is off."""
        self._ai_ready = ready
        if not ready and not self._ai_busy:
            self.ai_panel.hide()
        self._update_buttons()

    def _toggle_ai(self) -> None:
        if not self.ai_panel.isHidden():
            self.ai_panel.hide()
            return
        self.ai_parts_requested.emit()

    def offer_ai(self, parts: frozenset[DraftContextPart]) -> None:
        """Show the panel with the parts this draft can send."""
        if self.draft is None:
            return
        self.ai_panel.offer(parts, self.draft.kind)
        self.ai_panel.show()

    def _prepare_ai(self, options: DraftingOptions) -> None:
        if self.draft is None or self._conflict or self._locked or self.current_edit() is None:
            self.ai_panel.reset()
            if self.current_edit() is None:
                self.status.setText(_TOO_LONG)
            return
        self._autosave_now()  # The preview is built from the saved text.
        self._ai_busy = True
        self._set_locked(True)
        self.status.setText("Preparing what will be sent…")
        self.ai_prepare_requested.emit(options)

    def show_ai_preview(self, preview: DraftingPreview, lines: tuple[str, ...]) -> None:
        self.ai_panel.show_preview(preview, lines)
        self.status.setText(
            "Review what will be sent to Groq, then send it or cancel. Nothing has been sent."
        )

    def _send_ai(self, agreed: bool) -> None:
        if not self._ai_busy:
            return
        self.ai_panel.generating()
        self.status.setText("Writing with Groq…")
        self.ai_generate_requested.emit(agreed)

    def _cancel_ai(self) -> None:
        if not self._ai_busy:
            self.ai_panel.reset()
            return
        self.ai_cancel_requested.emit()
        self._finish_ai("Cancelled. Your text is unchanged.")

    def _finish_ai(self, message: str) -> None:
        self._ai_busy = False
        self._set_locked(False)
        self.ai_panel.reset()
        if message and not self._conflict:
            self.status.setText(message)

    def ai_generated(
        self, draft: Draft, number: int, previous: int | None, missing: tuple[str, ...]
    ) -> None:
        """Show Groq's new version; the owner's text is version ``previous``."""
        self.draft = draft
        self._set_text(draft.content())
        self._dirty = False
        self.missing_line.setText(
            "Missing context to check: " + "; ".join(missing) if missing else ""
        )
        self.missing_line.setVisible(bool(missing))
        before = f"; your previous text is version v{previous}" if previous is not None else ""
        self._finish_ai(f"New version v{number} from Groq{before}.")
        if not self.versions_panel.isHidden():
            self.versions_requested.emit()

    def ai_failed(self, message: str) -> None:
        """The generation didn't happen or didn't finish: the text is untouched."""
        if self._ai_busy or not self.ai_panel.isHidden():
            self._finish_ai(message)
        else:
            self.status.setText(message)

    def ai_active(self) -> bool:
        """Whether a preview is shown or Groq is writing (a method: it changes as we wait)."""
        return self._ai_busy

    # Copy, export and Gmail.

    def _copied(self, what: str) -> None:
        count = len(self._placeholders())
        self.set_status(f"Copied the {what}." + (f" {still_to_fill(count)}" if count else ""))

    def _copy_text(self) -> None:
        QGuiApplication.clipboard().setText(self.body.toPlainText())
        self._copied("text")

    def _copy_subject(self) -> None:
        QGuiApplication.clipboard().setText(self.title_edit.text())
        self._copied("subject")

    def export_document(self, path: str) -> str:
        """The draft as it stands now, as Markdown for a .md path and plain text otherwise."""
        draft = self.draft
        if draft is None:
            return ""
        email = draft.kind in EMAIL_KINDS
        current = draft.model_copy(
            update={
                "title": self.title_edit.text(),
                "to_text": self.to_edit.text() if email else "",
                "cc_text": self.cc_edit.text() if email else "",
                "body": self.body.toPlainText(),
            }
        )
        return export_markdown(current) if path.lower().endswith(".md") else export_text(current)

    def _choose_export(self) -> None:
        draft = self.draft
        if draft is None:
            return
        self._picker = QFileDialog(self, "Export draft")
        self._picker.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        self._picker.setNameFilters(["Markdown (*.md)", "Plain text (*.txt)"])
        self._picker.setDefaultSuffix("md")
        self._picker.selectFile(export_filename(draft, ".md"))
        self._picker.fileSelected.connect(self.export_to)
        self.dialog_guard.show_dialog(self._picker)

    def export_to(self, path: str) -> None:
        """Ask the window to write the draft to ``path``, which the owner chose."""
        if self.draft is None or not path:
            return
        self.status.setText("Exporting…")
        self.export_requested.emit(path, self.export_document(path))

    def set_status(self, text: str) -> None:
        self.status.setText(text)

    def _open_gmail(self) -> None:
        draft = self.draft
        link = None if draft is None or not draft.sources else gmail_link(draft.sources[0])
        if link is not None:
            QDesktopServices.openUrl(QUrl(link))

    # Closing.

    def reject(self) -> None:
        """Close, Esc and the title-bar button ask the window to save and save a version;
        the editor closes once it confirms. After a failure, closing again discards."""
        if self._closing or self._locked:
            return
        if self._discard_armed or self.draft is None:
            self._close_now()
            return
        if self._conflict:
            self._discard_armed = True
            self.status.setText(f"{CONFLICT} Save it as a new draft, or {_DISCARD_HINT_LOWER}")
            return
        edit = self.current_edit()
        if edit is None:
            self._discard_armed = True
            self.status.setText(f"{_TOO_LONG} {_DISCARD_HINT}")
            return
        self._stop_timers()
        self._closing = True
        self._set_locked(True)
        self.status.setText(_SAVING)
        unsaved = edit if self._dirty else None
        self._dirty = False
        self.close_requested.emit(unsaved)

    def finish_closed(self) -> None:
        """The window saved the text and a version: close."""
        self._close_now()

    def cancel_close(self, message: str | None = None) -> None:
        """The save before closing failed: stay open with the text; Close again discards."""
        self._closing = False
        self._dirty = True
        self._discard_armed = True
        self._set_locked(False)
        if message is not None:
            self.status.setText(f"{message} {_DISCARD_HINT}")
        elif self._conflict:
            self.status.setText(f"{CONFLICT} {_DISCARD_HINT}")

    def force_close(self) -> None:
        """Close at once, as the window does when it quits after queueing a final save."""
        self._close_now()

    def _close_now(self) -> None:
        self._stop_timers()
        self._closing = False
        self._set_locked(False)
        super().reject()
