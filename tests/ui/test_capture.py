"""Account-free global capture retains input, identity and ordinary owner workflows."""

import asyncio
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import Action
from mailbrief.domain.capture import CaptureRequest
from mailbrief.domain.drafts import Draft, DraftEdit, DraftKind
from mailbrief.services.drafts import NoteCreateConflictError
from mailbrief.ui.runtime import DesktopRuntime
from mailbrief.ui.theme import ThemeMode, apply_theme
from mailbrief.ui.workspace import PAGES
from tests.ui.test_main_window_layout import save_shot
from tests.ui.test_palette import window_for
from tests.ui.test_workflow import FakeBackend, finish


@pytest.mark.parametrize("page", [key for key, _label, _icon in PAGES])
async def test_capture_available_from_every_page_without_an_account(
    qtbot: QtBot, page: str
) -> None:
    window = await window_for(qtbot, FakeBackend())
    window._set_account(None)
    window._show_page(page)
    assert window.capture_button.isVisible()
    window.capture_button.click()
    assert window.capture_dialog.isVisible()
    window.capture_dialog.reject()
    assert window.workspace.current_page() == ("actions" if page == "waiting" else page)


@pytest.mark.parametrize("kind", ["task", "note"])
async def test_capture_save_and_open_use_existing_records(qtbot: QtBot, kind: str) -> None:
    backend = FakeBackend()
    window = await window_for(qtbot, backend)
    window._set_account(None)
    window._run_command("capture")
    dialog = window.capture_dialog
    dialog.kind.setCurrentIndex(0 if kind == "task" else 1)
    dialog.title.setText("Remember this")
    dialog.body.setPlainText("Owner writing" if kind == "note" else "")
    dialog.save_button.click()
    await window.drain_capture()
    assert dialog.status.text() == ("Task saved." if kind == "task" else "Note saved.")
    assert not dialog.has_unsaved_input()
    dialog.open_button.click()
    await finish(window)
    if kind == "task":
        assert window.actions_panel.selected() is not None
        assert window.workspace.current_page() == "actions"
    else:
        assert window.draft_editor.draft is not None
        assert window.draft_editor.draft.kind is DraftKind.NOTE
        assert window.draft_editor.draft.body == "Owner writing"
        window.draft_editor.force_close()


async def test_failed_save_keeps_input_and_command_and_suppresses_double_submit(
    qtbot: QtBot,
) -> None:
    backend = FakeBackend()
    requests: list[CaptureRequest] = []
    started, release = asyncio.Event(), asyncio.Event()

    async def capture(request: CaptureRequest) -> Action | Draft:
        requests.append(request)
        if len(requests) == 1:
            started.set()
            await release.wait()
            raise RuntimeError("Synthetic lost response")
        return backend._add(DraftKind.NOTE, request.note_edit())

    backend.capture = capture  # type: ignore[method-assign]
    window = await window_for(qtbot, backend)
    window.capture_shortcut.activated.emit()
    dialog = window.capture_dialog
    dialog.kind.setCurrentIndex(1)
    dialog.body.setPlainText("  Unsaved owner text\n")
    dialog.save_button.click()
    await started.wait()
    dialog._submit()
    assert len(requests) == 1 and not dialog.kind.isEnabled()
    release.set()
    await window.drain_capture()
    assert dialog.body.toPlainText() == "  Unsaved owner text\n"
    assert "not confirmed" in dialog.status.text()
    dialog.save_button.click()
    await window.drain_capture()
    assert requests[0] == requests[1]
    assert len(backend.drafts) == 1
    first_command = requests[1].command_id
    dialog.new_button.click()
    dialog.title.setText("Genuinely new capture")
    dialog.save_button.click()
    await window.drain_capture()
    assert requests[-1].command_id != first_command


async def test_conflict_keeps_edited_input(qtbot: QtBot) -> None:
    backend = FakeBackend()

    async def capture(request: CaptureRequest) -> Action | Draft:
        raise NoteCreateConflictError("This save already created a note with different input.")

    backend.capture = capture  # type: ignore[method-assign]
    window = await window_for(qtbot, backend)
    window._request_capture()
    dialog = window.capture_dialog
    dialog.kind.setCurrentIndex(1)
    dialog.body.setPlainText("Keep this edited input")
    dialog.save_button.click()
    await window.drain_capture()
    assert dialog.body.toPlainText() == "Keep this edited input"
    assert "different input" in dialog.status.text()
    dialog.force_close()


async def test_confirmed_save_stays_confirmed_if_view_refresh_fails(qtbot: QtBot) -> None:
    backend = FakeBackend()
    window = await window_for(qtbot, backend)

    async def failed_refresh() -> None:
        raise RuntimeError("Synthetic view refresh failed")

    window._refresh_drafts = failed_refresh  # type: ignore[method-assign]
    window._request_capture()
    window.capture_dialog.kind.setCurrentIndex(1)
    window.capture_dialog.body.setPlainText("Saved writing")
    window.capture_dialog.save_button.click()
    await window.drain_capture()
    assert len(backend.drafts) == 1
    assert window.capture_dialog.status.text() == "Note saved."


async def test_capture_does_not_replace_current_owner_writing(qtbot: QtBot) -> None:
    backend = FakeBackend()
    original = backend._add(DraftKind.NOTE, DraftEdit(body="Original writing"))
    window = await window_for(qtbot, backend)
    window._show_page("notes")
    window._edit_draft(original)
    window.draft_editor.body.setPlainText("Current owner edits")
    window._request_capture()
    window.capture_dialog.kind.setCurrentIndex(1)
    window.capture_dialog.body.setPlainText("A separate capture")
    window.capture_dialog.save_button.click()
    await window.drain_capture()
    await window.draft_writes.drain()
    assert window.draft_editor.draft is not None
    assert window.draft_editor.draft.public_id == original.public_id
    assert window.draft_editor.body.toPlainText() == "Current owner edits"
    await window._save_inline_draft()
    window.draft_editor.force_close()


async def test_cancel_never_creates_a_blank_record_and_typed_input_is_guarded(qtbot: QtBot) -> None:
    backend = FakeBackend()
    window = await window_for(qtbot, backend)
    window._request_capture()
    dialog = window.capture_dialog
    dialog.save_button.click()
    assert "needs a title" in dialog.status.text()
    dialog.reject()
    assert not dialog.isVisible() and not backend.drafts
    window._request_capture()
    dialog.body.setPlainText("Do not lose this")
    dialog.reject()
    assert dialog.isVisible() and dialog.discard_button.isVisible()
    assert window._unsaved_input() == "this capture"
    dialog.discard_button.click()
    assert not dialog.isVisible() and not backend.drafts


async def test_capture_can_save_while_foreground_email_work_is_waiting(qtbot: QtBot) -> None:
    window = await window_for(qtbot, FakeBackend())
    waiting, release = asyncio.Event(), asyncio.Event()

    async def email_work() -> None:
        waiting.set()
        await release.wait()

    window.start(email_work)
    await waiting.wait()
    window._request_capture()
    window.capture_dialog.kind.setCurrentIndex(1)
    window.capture_dialog.body.setPlainText("Capture while email waits")
    window.capture_dialog.save_button.click()
    await window.drain_capture()
    assert window.task is not None and not window.task.done()
    assert window.capture_dialog.status.text() == "Note saved."
    release.set()
    await finish(window)


async def test_shutdown_waits_for_an_inflight_capture(qtbot: QtBot) -> None:
    backend = FakeBackend()
    original = backend.capture
    started, release = asyncio.Event(), asyncio.Event()

    async def capture(request: CaptureRequest) -> Action | Draft:
        started.set()
        await release.wait()
        return await original(request)

    backend.capture = capture  # type: ignore[method-assign]
    window = await window_for(qtbot, backend)
    window._request_capture()
    window.capture_dialog.title.setText("Save before shutdown")
    window.capture_dialog.save_button.click()
    await started.wait()
    shutdown = asyncio.create_task(window.shutdown())
    await asyncio.sleep(0)
    assert not shutdown.done()
    release.set()
    await shutdown
    assert window.capture_dialog.status.text() == "Task saved."


@pytest.mark.parametrize("kind", ["task", "note"])
async def test_runtime_capture_is_offline_and_retry_safe(tmp_path: Path, kind: str) -> None:
    runtime = DesktopRuntime(tmp_path / "owner.sqlite3")
    await runtime.load_saved()
    try:
        request = CaptureRequest.model_validate(
            {
                "command_id": "12345678-1234-4234-8234-123456789abc",
                "kind": kind,
                "title": "Owner capture",
                "body": "Private writing",
            }
        )
        first = await runtime.capture(request)
        assert await runtime.capture(request) == first
    finally:
        await runtime.close()


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
async def test_capture_gallery(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    apply_theme(qapp, mode=mode)
    window = await window_for(qtbot, FakeBackend())
    window._request_capture()
    dialog = window.capture_dialog
    dialog.title.setText("Book a dentist appointment")
    QApplication.processEvents()
    save_shot(dialog.grab(), f"dialog-capture-task-{mode}")
    dialog.kind.setCurrentIndex(1)
    dialog.title.setText("Questions for the appointment")
    dialog.body.setPlainText("Ask about the next visit.\nKeep these notes with my own writing.")
    QApplication.processEvents()
    save_shot(dialog.grab(), f"dialog-capture-note-{mode}")
    dialog.force_close()
