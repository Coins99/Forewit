"""The command palette (Ctrl+K, Cmd+K on macOS): go to a page, start something, or search
the owner's local records.

The palette only lists and emits; the window searches through the backend and opens what
is chosen. Results are titles, senders and calendar names: the owner's or mail-derived
text, shown as plain list items, never rich text or tooltips.
"""

from dataclasses import dataclass
from datetime import date
from typing import Final
from zoneinfo import ZoneInfo

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QHideEvent, QKeyEvent
from PySide6.QtWidgets import QDialog, QLineEdit, QListWidgetItem, QVBoxLayout, QWidget

from mailbrief.domain.drafts import KIND_NAMES
from mailbrief.domain.search import (
    HITS_PER_KIND,
    SearchHit,
    SearchKind,
    SearchResults,
    query_terms,
)
from mailbrief.ui.deadline_text import day_text, moment_text
from mailbrief.ui.labels import wrap_label
from mailbrief.ui.lists import ActivatingList, clear_list
from mailbrief.ui.theme import SMALL_PX, current_tokens, ui_font
from mailbrief.ui.workspace import PAGES

# How long typing must pause before a search starts (ms).
DEBOUNCE_MS: Final = 200
HINT: Final = (
    "Type a page or a command, or two or more letters to search your actions, drafts, "
    "notes, events and saved mail. Return opens; Esc closes."
)
SEARCHING: Final = "Searching…"
NO_MATCHES: Final = "Nothing matches."
FAILED: Final = "Search couldn't read your records just now. Try again."
SECTION_NAMES: Final = {
    SearchKind.ACTION: "Actions",
    SearchKind.WRITING: "Drafts and notes",
    SearchKind.EVENT: "Your events",
    SearchKind.GOOGLE_EVENT: "Google Calendar",
    SearchKind.MAIL: "Mail",
}
_ROLE: Final = Qt.ItemDataRole.UserRole  # A Command or a SearchHit.


@dataclass(frozen=True)
class Command:
    """Something the palette can do: ``key`` names it to the window."""

    key: str
    label: str


# The pages in the sidebar's order (workspace.PAGES), then the rest.
COMMANDS: Final = (
    *(Command(f"page:{key}", f"Go to {label}") for key, label, _icon in PAGES),
    Command("new:task", "New task"),
    Command("new:event", "New event today"),
    Command("new:note", "New note"),
    Command("sync", "Sync and review"),
    Command("saved_mail", "Open saved mail"),
    Command("data", "Open data and recovery"),
    Command("settings", "Open settings"),
)


def matching_commands(text: str) -> tuple[Command, ...]:
    """Every command for an empty query; else those whose label holds every word."""
    words = text.casefold().split()
    return tuple(
        command for command in COMMANDS if all(word in command.label.casefold() for word in words)
    )


def hit_text(hit: SearchHit, zone: ZoneInfo, today: date) -> str:
    """A hit as one line: its title, then what tells it apart."""
    parts = [hit.title]
    if hit.kind is SearchKind.ACTION:
        parts.append(("waiting for others" if hit.waiting else "open") if hit.open else "done")
    elif hit.kind is SearchKind.WRITING and hit.draft_kind is not None:
        parts.append(KIND_NAMES[hit.draft_kind])
    elif hit.kind is SearchKind.GOOGLE_EVENT and hit.detail:
        parts.append(hit.detail)
    elif hit.kind is SearchKind.MAIL:
        parts.append(hit.detail if hit.open else f"{hit.detail}, not in the Inbox")
    when = _when(hit, zone, today)
    if when:
        parts.append(when)
    return " · ".join(part for part in parts if part)


def _when(hit: SearchHit, zone: ZoneInfo, today: date) -> str:
    if hit.kind in (SearchKind.EVENT, SearchKind.GOOGLE_EVENT, SearchKind.MAIL):
        if hit.when is not None:
            return moment_text(hit.when, zone, today=today)
        if hit.day is not None:
            return day_text(hit.day, today=today)
    return ""


class CommandPalette(QDialog):
    """``query_changed(text)`` asks for a search once typing pauses; ``command_chosen(key)``
    and ``hit_chosen(hit)`` carry the choice after the palette has closed."""

    query_changed = Signal(str)
    command_chosen = Signal(str)
    hit_chosen = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("commandPalette")
        self.setWindowTitle("Go to or search")
        self.setAccessibleName("Go to or search")
        self.resize(560, 420)
        self.zone = ZoneInfo("UTC")
        self.today = date.min
        layout = QVBoxLayout(self)
        self.field = QLineEdit()
        self.field.setObjectName("paletteField")
        self.field.setAccessibleName("Command or search words")
        self.field.setPlaceholderText("Go to, start or search…")
        self.field.setClearButtonEnabled(True)
        layout.addWidget(self.field)
        self.results = ActivatingList()
        self.results.setObjectName("paletteResults")
        self.results.setAccessibleName("Commands and search results")
        self.results.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.results.setTextElideMode(Qt.TextElideMode.ElideRight)
        layout.addWidget(self.results, 1)
        self.note = wrap_label(HINT, tone="muted", px=SMALL_PX)
        self.note.setObjectName("paletteNote")
        layout.addWidget(self.note)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(DEBOUNCE_MS)
        self._timer.timeout.connect(self._query)
        self.field.textChanged.connect(self._typed)
        self.field.installEventFilter(self)
        self.results.itemActivated.connect(self._activate)

    def open_fresh(self, zone: ZoneInfo, today: date) -> None:
        """Start again with an empty field, every command listed and the field focused."""
        self.zone, self.today = zone, today
        self._timer.stop()
        self.field.blockSignals(True)
        self.field.clear()
        self.field.blockSignals(False)
        self._show(matching_commands(""), None)
        self.note.setText(HINT)
        self.field.setFocus()

    def show_results(self, text: str, results: SearchResults) -> None:
        """Show ``results`` if ``text`` is still what is typed; an older search's are
        dropped."""
        if text != self.field.text():
            return
        self._show(matching_commands(text), results)
        if not results.hits and not matching_commands(text):
            self.note.setText(NO_MATCHES)
        elif results.more:
            self.note.setText(f"Showing the first {HITS_PER_KIND} of each kind; add a word.")
        else:
            self.note.setText(HINT)

    def show_failure(self, text: str) -> None:
        if text == self.field.text():
            self.note.setText(FAILED)

    def _typed(self, text: str) -> None:
        self._show(matching_commands(text), None)
        if query_terms(text):
            self.note.setText(SEARCHING)
            self._timer.start()
        else:
            self._timer.stop()
            self.note.setText(HINT if matching_commands(text) else NO_MATCHES)

    def _query(self) -> None:
        if self.isVisible():  # A search is for an open palette only.
            self.query_changed.emit(self.field.text())

    def hideEvent(self, event: QHideEvent) -> None:
        """However it closes (Esc, a choice, the window quitting), a search still waiting
        for typing to pause never starts."""
        self._timer.stop()
        super().hideEvent(event)

    def _show(self, commands: tuple[Command, ...], results: SearchResults | None) -> None:
        clear_list(self.results)
        if commands:
            self._section("Commands")
            for command in commands:
                self._row(command.label, command)
        for kind, title in SECTION_NAMES.items():
            hits = [hit for hit in (results.hits if results else ()) if hit.kind is kind]
            if hits:
                self._section(title)
                for hit in hits:
                    self._row(hit_text(hit, self.zone, self.today), hit)
        self._select_first()

    def _section(self, title: str) -> None:
        """A heading row: muted, and never selected or activated."""
        item = QListWidgetItem(title)
        item.setFlags(Qt.ItemFlag.NoItemFlags)
        item.setFont(ui_font(SMALL_PX, semibold=True))
        item.setForeground(QColor(current_tokens().text_muted))
        self.results.addItem(item)

    def _row(self, text: str, value: Command | SearchHit) -> None:
        item = QListWidgetItem(text)
        item.setData(_ROLE, value)
        self.results.addItem(item)

    def _selectable(self) -> list[int]:
        return [
            number
            for number in range(self.results.count())
            if (item := self.results.item(number)) is not None
            and item.flags() & Qt.ItemFlag.ItemIsEnabled
        ]

    def _select_first(self) -> None:
        rows = self._selectable()
        self.results.setCurrentRow(rows[0] if rows else -1)

    def _move(self, step: int) -> None:
        rows = self._selectable()
        if not rows:
            return
        current = self.results.currentRow()
        later = [row for row in rows if (row > current if step > 0 else row < current)]
        if later:
            self.results.setCurrentRow(later[0] if step > 0 else later[-1])

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        """In the field, Up and Down move through the rows and Return opens the current one,
        so the owner never has to leave the field."""
        if (
            watched is self.field
            and isinstance(event, QKeyEvent)
            and event.type() == QEvent.Type.KeyPress
        ):
            key = event.key()
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                self._move(1 if key == Qt.Key.Key_Down else -1)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self.results.currentItem()
                if item is not None:
                    self._activate(item)
                return True
        return super().eventFilter(watched, event)

    def _activate(self, item: QListWidgetItem) -> None:
        value = item.data(_ROLE)
        if not isinstance(value, Command | SearchHit):
            return
        self._timer.stop()
        self.close()  # First, so the window sees no open dialog when it acts.
        if isinstance(value, Command):
            self.command_chosen.emit(value.key)
        else:
            self.hit_chosen.emit(value)
