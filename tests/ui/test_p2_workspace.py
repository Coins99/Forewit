"""Account-free canonical Notes, Projects, dated selection and upcoming navigation."""

from datetime import timedelta

import pytest
from PySide6.QtWidgets import QApplication, QScrollArea, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import ActionFilter
from mailbrief.domain.capture_state import CaptureOrigin
from mailbrief.domain.drafts import DraftEdit, DraftKind
from mailbrief.domain.projects import ProjectEdit
from mailbrief.domain.routes import PageRoute, WritingRoute
from mailbrief.ui.capture_inbox import CaptureInbox
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.theme import ThemeMode, apply_theme
from tests.factories import make_action
from tests.ui.test_main_window_layout import save_shot
from tests.ui.test_workflow import FakeBackend, finish
from tests.ui.window_wait import show


@pytest.mark.parametrize("tasks_first", [True, False])
def test_capture_type_changes_apply_the_limit_after_filtering(
    qtbot: QtBot, tasks_first: bool
) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    inbox = CaptureInbox(parent)
    backend = FakeBackend()
    tasks = tuple(make_action(public_id=f"{index:036d}") for index in range(100))
    notes = tuple(
        backend._add(DraftKind.NOTE, DraftEdit(title=f"Older note {index}")) for index in range(100)
    )
    # Either kind can be newer in the service's merged recency order.
    inbox.show_records((*tasks, *notes) if tasks_first else (*notes, *tasks))
    assert inbox.list.count() == 100
    assert inbox._shown == (tasks if tasks_first else notes)
    inbox.kind.setCurrentIndex(2)
    assert inbox.list.count() == 100
    assert inbox.list.item(0).text() == "Note · Older note 0"
    opened: list[object] = []
    inbox.open_requested.connect(opened.append)
    inbox._request(True)
    assert opened == [notes[0]]
    inbox.kind.setCurrentIndex(1)
    assert inbox.list.count() == 100
    inbox._request(True)
    assert opened == [notes[0], tasks[0]]
    inbox.kind.setCurrentIndex(0)
    assert inbox.list.count() == 100


async def test_notes_and_mail_drafts_partition_the_same_canonical_writing(qtbot: QtBot) -> None:
    backend = FakeBackend()
    note = backend._add(DraftKind.NOTE, DraftEdit(title="Canonical note", body="Owner text"))
    email = backend._add(DraftKind.EMAIL, DraftEdit(title="Email"))
    window = MainWindow(backend)
    qtbot.addWidget(window)
    await window.initialize()
    show(qtbot, window)
    try:
        window._navigate(WritingRoute(public_id=note.public_id, draft_kind=DraftKind.NOTE))
        await finish(window)
        await window.draft_writes.drain()
        assert window.workspace.current_page() == "notes"
        assert window.drafts_panel.list.count() == 1
        window.draft_editor.body.setPlainText("Updated in Notes")
        window._request_page("drafts")
        await window.draft_writes.drain()
        assert window.drafts_panel.selected().public_id == email.public_id  # type: ignore[union-attr]
        assert backend.drafts[note.public_id].body == "Updated in Notes"
        window._navigate(WritingRoute(public_id=note.public_id))
        await finish(window)
        assert window.draft_editor.body.toPlainText() == "Updated in Notes"
    finally:
        window.close()
        await window.shutdown()


async def test_browsed_plan_date_survives_refresh_and_go_today_is_explicit(qtbot: QtBot) -> None:
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    await window.initialize()
    try:
        window._open_focus()
        await window.drain_calendar_reads()
        yesterday = window._local_today() - timedelta(days=1)
        window._browse_focus_date(yesterday)
        await window.drain_calendar_reads()
        await window._refresh_day()
        assert window.focus_dialog.panel.plan.day == yesterday  # type: ignore[union-attr]
        window._browse_focus_date(None)
        await window.drain_calendar_reads()
        assert window.focus_dialog.panel.plan.day == window._local_today()  # type: ignore[union-attr]
    finally:
        window.focus_dialog.reject()
        window.close()
        await window.shutdown()


async def test_task_notes_reveal_the_same_note_and_preserve_pending_text(qtbot: QtBot) -> None:
    backend = FakeBackend()
    task = make_action()
    backend.actions = {ActionFilter.OPEN: (task,)}
    note = backend._add(DraftKind.NOTE, DraftEdit(title="Linked note", body="Owner text"))
    backend.drafts[note.public_id] = note.model_copy(update={"action_public_id": task.public_id})
    other = backend._add(DraftKind.NOTE, DraftEdit(title="Unrelated", body="Other text"))
    window = MainWindow(backend)
    qtbot.addWidget(window)
    await window.initialize()
    show(qtbot, window)
    try:
        window._open_task_notes(task)
        await finish(window)
        await window.draft_writes.drain()
        assert window.workspace.current_page() == "notes"
        assert window.drafts_panel.list.count() == 1
        assert window.draft_editor.draft is not None
        assert window.draft_editor.draft.public_id == note.public_id
        window.draft_editor.body.setPlainText("Pending edits belong to this note")
        window.drafts_panel.clear_context.click()
        await window.draft_writes.drain()
        assert window.drafts_panel.list.count() == 2
        window._navigate(WritingRoute(public_id=note.public_id))
        await finish(window)
        assert window.draft_editor.body.toPlainText() == "Pending edits belong to this note"
        window._open_task_notes(task)
        await finish(window)
        window._navigate(WritingRoute(public_id=other.public_id, draft_kind=DraftKind.NOTE))
        await finish(window)
        await window.draft_writes.drain()
        assert window.drafts_panel.task_context is None
        assert window.drafts_panel.list.count() == 2
        assert window.draft_editor.draft is not None
        assert window.draft_editor.draft.public_id == other.public_id
    finally:
        window.close()
        await window.shutdown()


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
async def test_new_workspace_gallery(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    apply_theme(qapp, mode)
    backend = FakeBackend()
    note = backend._add(
        DraftKind.NOTE,
        DraftEdit(title="Planning notes", body="A local note linked from the workspace."),
    )
    await backend.save_organization(
        "project",
        None,
        None,
        ProjectEdit(name="Autumn planning", notes="Prepare next week's commitments."),
    )
    window = MainWindow(backend)
    qtbot.addWidget(window)
    await window.initialize()
    window.resize(1100, 720)
    show(qtbot, window)
    try:
        for page in ("today", "notes", "projects"):
            window._navigate(PageRoute(page=page))
            if page == "projects":
                await finish(window)
            await window.draft_writes.drain()
            QApplication.processEvents()
            save_shot(window.grab(), f"p2-{page}-{mode.value}")
        window._show_page("today")
        window._open_focus()
        await window.drain_calendar_reads()
        # Initial polish and the queued layout after hiding empty/history sections
        # need separate event passes before a screenshot represents the settled dialog.
        for _ in range(3):
            QApplication.processEvents()
        scroll = window.focus_dialog.findChild(QScrollArea, "dayPlanScroll")
        assert scroll is not None
        done = window.focus_dialog.close_button
        assert (
            scroll.viewport()
            .rect()
            .contains(done.mapTo(scroll.viewport(), done.rect().bottomRight()))
        )
        save_shot(window.focus_dialog.grab(), f"p2-dated-plan-{mode.value}")
        window.focus_dialog.transfer_toggle.setChecked(True)
        for _ in range(3):
            QApplication.processEvents()
        save_shot(window.focus_dialog.grab(), f"p2-transfer-options-{mode.value}")
        window.focus_dialog.reject()
        window._open_upcoming()
        await window.drain_calendar_reads()
        QApplication.processEvents()
        save_shot(window.upcoming.grab(), f"p2-upcoming-{mode.value}")
        window.upcoming.reject()
        task = make_action(title="Prepare the next milestone")
        window.capture_inbox.show_records(
            (
                task.model_copy(update={"capture_origin": CaptureOrigin.QUICK_CAPTURE}),
                note.model_copy(update={"capture_origin": CaptureOrigin.QUICK_CAPTURE}),
            )
        )
        window.dialog_guard.show_dialog(window.capture_inbox)
        for _ in range(3):
            QApplication.processEvents()
        save_shot(window.capture_inbox.grab(), f"p2-capture-inbox-{mode.value}")
        window.capture_inbox.reject()
    finally:
        window.upcoming.reject()
        window.close()
        await window.shutdown()
