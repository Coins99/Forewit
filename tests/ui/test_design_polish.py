"""Navigation and save boundaries introduced by the design port."""

import asyncio

import pytest
from PySide6.QtCore import QPoint
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QPushButton
from pytestqt.qtbot import QtBot

from mailbrief.domain.drafts import Draft, DraftEdit, DraftKind
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.theme import ThemeMode, apply_theme
from tests.ui.test_main_window_layout import save_shot
from tests.ui.test_workflow import FakeBackend
from tests.ui.window_wait import show
from tests.ui.workspace_fixtures import mockup_digest


async def draft_window(qtbot: QtBot) -> tuple[MainWindow, FakeBackend]:
    backend = FakeBackend()
    for title in ("First", "Second"):
        draft = await backend.create_draft(DraftKind.NOTE)
        await backend.autosave_draft(draft.public_id, draft.revision, DraftEdit(title=title))
    window = MainWindow(backend)
    qtbot.addWidget(window)
    await window.initialize()
    show(qtbot, window)
    window._show_page("drafts")
    await window.draft_writes.drain()
    return window, backend


async def test_inline_draft_switch_saves_before_loading_another(qtbot: QtBot) -> None:
    window, backend = await draft_window(qtbot)
    editor = window.draft_editor
    try:
        assert editor.embedded and not editor.isWindow()
        assert editor.draft is not None
        original = editor.draft.public_id
        editor.body.setPlainText("Unsaved typing belongs to this draft.")
        window.drafts_panel.list.setCurrentRow(1)
        await window.draft_writes.drain()
        assert backend.drafts[original].body == "Unsaved typing belongs to this draft."
        assert editor.draft.public_id != original
        assert editor.body.toPlainText() == ""
    finally:
        window.close()
        await window.shutdown()


async def test_failed_inline_save_keeps_text_and_selection(qtbot: QtBot) -> None:
    window, backend = await draft_window(qtbot)
    editor = window.draft_editor
    try:
        assert editor.draft is not None
        original = editor.draft.public_id
        editor.body.setPlainText("Keep this text after a failed save.")
        backend.draft_fail = RuntimeError("synthetic storage failure")
        window.drafts_panel.list.setCurrentRow(1)
        await window.draft_writes.drain()
        selected = window.drafts_panel.selected()
        assert selected is not None and selected.public_id == original
        assert editor.draft.public_id == original
        assert editor.body.toPlainText() == "Keep this text after a failed save."
        assert backend.drafts[original].body == ""
    finally:
        window.close()
        await window.shutdown()


async def test_hidden_inline_draft_saves_on_quit(qtbot: QtBot) -> None:
    window, backend = await draft_window(qtbot)
    editor = window.draft_editor
    assert editor.draft is not None
    public_id = editor.draft.public_id
    editor.body.setPlainText("Save even after navigating away.")
    window._show_page("today")
    assert not editor.isVisible()
    window.close()
    await window.shutdown()
    assert backend.drafts[public_id].body == "Save even after navigating away."


async def test_new_inline_draft_stays_selected_after_refresh(qtbot: QtBot) -> None:
    window, backend = await draft_window(qtbot)
    try:
        await window._open_new_draft(lambda: backend.create_draft(DraftKind.NOTE))
        await window.draft_writes.drain()
        selected = window.drafts_panel.selected()
        draft = window.draft_editor.draft
        assert selected is not None and draft is not None
        assert selected.public_id == draft.public_id
        assert draft.title == "" and window.draft_editor.embedded
    finally:
        window.close()
        await window.shutdown()


async def test_leaving_inline_editor_for_a_reply_keeps_its_controls(qtbot: QtBot) -> None:
    window, backend = await draft_window(qtbot)
    try:
        assert window.draft_editor.draft is not None
        original = window.draft_editor.draft.public_id
        window.draft_editor.body.setPlainText("Save before opening the reply.")
        window._show_page("today")
        await window._open_new_draft(
            lambda: backend.create_reply_draft("owner@example.com", "local-1")
        )
        assert window.draft_editor.embedded
        assert window.draft_editor.reply_mode
        assert not window.draft_editor.isWindow()
        assert backend.drafts[original].body == "Save before opening the reply."
        assert not window.draft_editor.isAncestorOf(window.drafts_panel.delete_button)
        assert window.draft_editor.more_button.isVisible()
        window._show_page("actions")
        await window._open_new_draft(lambda: backend.create_draft(DraftKind.EMAIL))
        assert window.draft_editor.isWindow()
        assert window.draft_editor.body.maximumHeight() > 160
        buttons = window.draft_editor._buttons
        assert buttons.getItemPosition(buttons.indexOf(window.draft_editor.close_button)) == (
            2,
            0,
            1,
            1,
        )
        assert window.draft_editor.copy_subject_button.isVisible()
    finally:
        window.close()
        await window.shutdown()


async def test_account_popup_keeps_connection_controls_accessible(
    qtbot: QtBot, qapp: QApplication, themed: None
) -> None:
    apply_theme(qapp, ThemeMode.LIGHT)
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    await window.initialize()
    show(qtbot, window)
    sidebar = window.workspace.sidebar
    try:
        sidebar.account_button.click()
        assert sidebar.account_menu.isVisible()
        assert sidebar.popup_account.text() == window._account_email
        assert sidebar.account_menu.findChild(QPushButton, "accountSettings") is not None
        assert window.connection.text()
        assert QApplication.focusWidget() is not None
        qapp.processEvents()
        save_shot(sidebar.account_menu.grab(), "design-account-light")
        screen = window.grab()
        painter = QPainter(screen)
        painter.drawPixmap(
            sidebar.account_menu.mapTo(window, QPoint(0, 0)), sidebar.account_menu.grab()
        )
        painter.end()
        save_shot(screen, "design-account-overlay-light")
        sidebar.account_menu.hide()
    finally:
        window.close()
        await window.shutdown()


async def test_reply_survives_brief_refresh_and_saves_after_leaving(qtbot: QtBot) -> None:
    window, backend = await draft_window(qtbot)
    try:
        backend.saved = mockup_digest().digest
        window._show_page("today")
        await window._show_digest(backend.saved)
        await window._open_new_draft(
            lambda: backend.create_reply_draft("owner@example.com", "local-1")
        )
        editor = window.draft_editor
        assert editor.draft is not None
        public_id = editor.draft.public_id
        editor.body.setPlainText("Preserve this reply across a refreshed brief.")
        await window._show_digest(backend.saved)
        assert editor.parentWidget() is window.workspace.detail.reply_host
        assert editor.body.toPlainText() == "Preserve this reply across a refreshed brief."
        window._show_page("actions")
    finally:
        window.close()
        await window.shutdown()
    assert backend.drafts[public_id].body == "Preserve this reply across a refreshed brief."


async def test_inline_task_warns_before_quit_after_navigating_away(qtbot: QtBot) -> None:
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    await window.initialize()
    show(qtbot, window)
    try:
        window._request_new_task()
        assert window.action_editor.embedded and not window.action_editor.isWindow()
        window.action_editor.title.setText("Keep my new task")
        window._show_page("today")
        assert not window.action_editor.isVisible()
        assert window._unsaved_input() == "this action"
        window._request_new_task()
        assert window.action_editor.title.text() == "Keep my new task"
    finally:
        window.action_editor.force_close()
        window.close()
        await window.shutdown()


async def test_revisiting_waiting_keeps_its_header_and_controls(qtbot: QtBot) -> None:
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    await window.initialize()
    show(qtbot, window)
    try:
        for page in ("waiting", "today", "waiting", "waiting"):
            window._show_page(page)
        assert window.workspace.header.title.text() == "Waiting"
        assert window.workspace.sidebar.current_key() == "waiting"
        # B08: a task can start on Waiting, as one someone else does.
        assert window.actions_panel.new_task_button.isVisible()
        assert window.actions_panel.search.isVisible()
        assert not window.actions_panel.group.isVisible()
    finally:
        window.close()
        await window.shutdown()


async def test_delayed_draft_preview_does_not_open_after_leaving_drafts(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    window, backend = await draft_window(qtbot)
    original = window.draft_editor.draft
    assert original is not None
    started, release = asyncio.Event(), asyncio.Event()
    get_draft = backend.get_draft

    async def delayed(public_id: str) -> Draft:
        started.set()
        await release.wait()
        return await get_draft(public_id)

    monkeypatch.setattr(backend, "get_draft", delayed)
    try:
        window.drafts_panel.list.setCurrentRow(1)
        await asyncio.wait_for(started.wait(), 2)
        window._show_page("today")
        release.set()
        await window.draft_writes.drain()
        assert window.workspace.current_page() == "today"
        assert window.draft_editor.draft == original
        assert window.draft_editor.parentWidget() is window.drafts_panel.editor_host
        assert not window.dialog_guard.isVisible()
    finally:
        release.set()
        window.close()
        await window.shutdown()


async def test_search_open_saves_the_inline_draft_before_replacing_it(qtbot: QtBot) -> None:
    window, backend = await draft_window(qtbot)
    original = window.draft_editor.draft
    assert original is not None
    other = next(
        draft for draft in backend.drafts.values() if draft.public_id != original.public_id
    )
    try:
        window.draft_editor.body.setPlainText("Keep writing when opening a search result.")
        await window._open_draft_by_id(other.public_id)
        assert backend.drafts[original.public_id].body == (
            "Keep writing when opening a search result."
        )
        assert window.draft_editor.draft is not None
        assert window.draft_editor.draft.public_id == other.public_id
        selected = window.drafts_panel.selected()
        assert selected is not None and selected.public_id == other.public_id
    finally:
        window.close()
        await window.shutdown()


async def test_short_calendar_drawer_keeps_save_inside_the_window(qtbot: QtBot) -> None:
    from mailbrief.ui.calendar_view import NEW

    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    await window.initialize()
    window.resize(680, 520)
    show(qtbot, window)
    try:
        window._show_page("calendar")
        window._request_event(NEW, window._local_today())
        QApplication.processEvents()
        editor = window.event_editor
        save = editor.buttons.button(QDialogButtonBox.StandardButton.Save)
        assert save is not None
        assert editor.rect().contains(save.mapTo(editor, save.rect().bottomRight()))
        assert window.rect().contains(editor.geometry())
    finally:
        window.event_editor.reject()
        window.close()
        await window.shutdown()
