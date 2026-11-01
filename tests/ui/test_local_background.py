"""Local inference must coexist with owner work and finish before shutdown/revocation."""

import asyncio
from datetime import timedelta

from pytestqt.qtbot import QtBot

from mailbrief.domain.preferences import OwnerPreferences
from mailbrief.ui.main_window import MainWindow
from tests.ui.test_workflow import FakeBackend, finish


async def test_owner_can_capture_and_save_a_task_during_local_analysis(qtbot: QtBot) -> None:
    backend = FakeBackend()
    backend.owner_preferences = OwnerPreferences(revision=0, analysis_mode="local")
    window = MainWindow(backend)
    qtbot.addWidget(window)
    window.show()
    await window.initialize()
    entered, release = asyncio.Event(), asyncio.Event()

    async def analyze(cancel: asyncio.Event) -> None:
        entered.set()
        await release.wait()

    assert window._start_local_run(analyze)
    local = window._local_run
    assert local is not None
    try:
        await entered.wait()
        assert not window.generate_button.isEnabled()
        assert not window._start_local_run(analyze)
        assert window.actions_panel.new_task_button.isEnabled()
        assert window.inbox_panel.mail_search.isEnabled()
        assert not window.inbox_panel.refresh.isEnabled()
        window.actions_panel.new_task_button.click()
        assert window.action_editor.isVisible()
        window.action_editor.title.setText("Capture while summaries finish")
        window.action_editor._save()
        await finish(window)
        assert backend.action_calls[-1][0] == "create_action"
        assert not local.done()
        assert window._local_running()
        assert not window.settings_dialog.preferences_panel.isEnabled()
        assert window.cancel_button.isEnabled()
    finally:
        release.set()
        await local
    assert window.generate_button.isEnabled()


async def test_revoke_joins_background_and_preserves_an_in_flight_owner_write(qtbot: QtBot) -> None:
    stopped, writing, release, committed = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )

    class Backend(FakeBackend):
        async def revoke_consent(self) -> int:
            assert stopped.is_set()
            assert committed.is_set()
            return await super().revoke_consent()

    backend = Backend()
    window = MainWindow(backend)
    qtbot.addWidget(window)

    async def analyze(cancel: asyncio.Event) -> None:
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    async def owner_write() -> None:
        writing.set()
        await release.wait()
        committed.set()

    assert window._start_local_run(analyze)
    await asyncio.sleep(0)
    assert window.start(owner_write, cancellable=False)
    await writing.wait()
    window._request_revoke_consent()
    await asyncio.sleep(0)
    assert not bool(backend.revoked)
    assert not committed.is_set()
    release.set()
    await window.drain_calendar_reads()
    await finish(window)
    assert bool(backend.revoked)
    assert committed.is_set()
    assert stopped.is_set()


async def test_shutdown_joins_local_inference_before_closing_backend(qtbot: QtBot) -> None:
    entered, stopped = asyncio.Event(), asyncio.Event()

    class Backend(FakeBackend):
        async def close(self) -> None:
            assert stopped.is_set()
            await super().close()

    backend = Backend()
    window = MainWindow(backend)
    qtbot.addWidget(window)

    async def analyze(cancel: asyncio.Event) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    assert window._start_local_run(analyze)
    await entered.wait()
    await window.shutdown()
    assert stopped.is_set()
    assert backend.closed
    assert not window._local_running()


async def test_completed_brief_defers_refresh_until_an_inline_reply_is_saved(qtbot: QtBot) -> None:
    backend = FakeBackend()
    window = MainWindow(backend)
    qtbot.addWidget(window)
    window.show()
    await window.initialize()
    window._show_page("today")
    reply = await backend.create_reply_draft("owner@example.com", "local-1")
    window._edit_draft(reply)
    editor = window.draft_editor
    editor.body.setPlainText("My reply must survive background summaries.")
    cursor = editor.body.textCursor()
    cursor.setPosition(12)
    editor.body.setTextCursor(cursor)
    updated = backend.saved.model_copy(
        update={
            "generated_at_utc": backend.saved.generated_at_utc + timedelta(hours=1),
        }
    )
    await window._show_digest(updated, defer_if_writing=True)
    assert bool(window._pending_brief == updated)
    assert editor.isVisible()
    assert editor.body.textCursor().position() == 12
    assert editor.body.toPlainText() == "My reply must survive background summaries."
    assert "finish writing" in window.workspace.coverage.text()
    await window._close_draft(editor.current_edit())
    assert window._pending_brief is None
    assert not editor.isVisible()
    assert (
        await backend.get_draft(reply.public_id)
    ).body == "My reply must survive background summaries."
    assert "finish writing" not in window.workspace.coverage.text()
