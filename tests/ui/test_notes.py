"""The Notes page (P2-04): its own sidebar row on the writing page, and a note's project and
task links (ADR 0027), saved through the window's one queue of draft writes."""

import asyncio
from datetime import UTC, datetime

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import ActionStatus, TaskChoice
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.drafts import DraftEdit, DraftKind, WritingView
from mailbrief.domain.projects import Project
from mailbrief.domain.routes import PageRoute, WritingRoute
from mailbrief.ui.draft_editor import CONFLICT
from mailbrief.ui.labels import WrapLabel
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.theme import ThemeMode, apply_theme
from mailbrief.ui.workspace import COUNT_ROLE
from tests.ui.test_main_window_layout import save_shot
from tests.ui.test_palette import window_for
from tests.ui.test_workflow import FakeBackend, finish
from tests.ui.window_wait import drain_writes

HOME = "11111111-1111-4111-8111-111111111111"
OLD = "22222222-2222-4222-8222-222222222222"
WORK = "44444444-4444-4444-8444-444444444444"
TASK = TaskChoice(
    public_id="33333333-3333-4333-8333-333333333333",
    title="Paint the hall",
    status=ActionStatus.OPEN,
    ownership=ActionOwnership.MINE,
)


def project(public_id: str, name: str, *, archived: bool = False) -> Project:
    moment = datetime(2026, 9, 1, tzinfo=UTC)
    return Project(
        public_id=public_id,
        name=name,
        revision=1,
        created_at_utc=moment,
        updated_at_utc=moment,
        archived_at_utc=moment if archived else None,
    )


async def notes_window(qtbot: QtBot) -> tuple[MainWindow, FakeBackend]:
    backend = FakeBackend()
    backend.projects = (project(HOME, "Home"), project(OLD, "Old", archived=True))
    backend.project_names = {HOME: "Home", OLD: "Old"}
    backend.archived_projects = {OLD}
    backend.action_titles = {TASK.public_id: TASK.title}
    backend.task_choice_list = (TASK,)
    note = await backend.create_draft(DraftKind.NOTE)
    await backend.autosave_draft(note.public_id, note.revision, DraftEdit(title="Ideas"))
    email = await backend.create_draft(DraftKind.EMAIL)
    await backend.autosave_draft(email.public_id, email.revision, DraftEdit(title="Hello"))
    window = await window_for(qtbot, backend)
    return window, backend


async def open_notes(window: MainWindow) -> None:
    window._request_page("notes")
    await drain_writes(window)


def calls(backend: FakeBackend, name: str) -> list[tuple[object, ...]]:
    return [call for call in backend.draft_calls if call[0] == name]


async def test_notes_has_its_own_row_heading_and_shortcut(qtbot: QtBot) -> None:
    window, _backend = await notes_window(qtbot)
    sidebar = window.workspace.sidebar
    drafts_row = sidebar.pages.item(sidebar._row("drafts"))
    assert drafts_row.data(COUNT_ROLE) == 1  # The email; notes aren't drafts to send.

    window.page_shortcuts["notes"].activated.emit()
    await drain_writes(window)

    assert window.workspace.current_page() == "drafts"
    assert window.drafts_panel.view() is WritingView.NOTES
    assert sidebar.current_key() == "notes"
    assert window.workspace.header.title.text() == "Notes"
    assert window.drafts_panel.selected() is not None
    assert window.drafts_panel.selected().display_title == "Ideas"  # type: ignore[union-attr]
    assert window.draft_editor.embedded and not window.draft_editor.links.isHidden()
    editor = window.draft_editor
    assert (editor.project_label.objectName(), editor.task_label.objectName()) == (
        "noteProjectLabel",
        "noteTaskLabel",
    )
    assert isinstance(editor.task_line, WrapLabel)  # A long unbroken title can't widen it.

    window.page_shortcuts["drafts"].activated.emit()
    await drain_writes(window)
    assert window.drafts_panel.view() is WritingView.DRAFTS
    assert sidebar.current_key() == "drafts"
    assert window.workspace.header.title.text() == "Drafts"
    assert window.draft_editor.draft is not None
    assert window.draft_editor.draft.kind is DraftKind.EMAIL
    assert window.draft_editor.links.isHidden()  # Only notes take links.


async def test_routes_open_a_note_in_notes_and_a_draft_in_drafts(qtbot: QtBot) -> None:
    window, backend = await notes_window(qtbot)
    note_id, email_id = list(backend.drafts)
    window._navigate(PageRoute(page="notes"))
    await drain_writes(window)
    assert window.drafts_panel.view() is WritingView.NOTES

    window._navigate(WritingRoute(public_id=email_id))  # No kind given: its own view.
    await finish(window)
    await drain_writes(window)
    assert window.drafts_panel.view() is WritingView.DRAFTS
    assert window.workspace.sidebar.current_key() == "drafts"
    assert window.draft_editor.draft is not None
    assert window.draft_editor.draft.public_id == email_id

    window._navigate(WritingRoute(public_id=note_id, draft_kind=DraftKind.NOTE))
    await finish(window)
    await drain_writes(window)
    assert window.drafts_panel.view() is WritingView.NOTES
    assert window.draft_editor.draft is not None
    assert window.draft_editor.draft.public_id == note_id


async def test_choosing_a_project_saves_after_the_typing_waiting(qtbot: QtBot) -> None:
    window, backend = await notes_window(qtbot)
    await open_notes(window)
    editor = window.draft_editor
    choice = editor.project_choice
    names = [choice.itemText(index) for index in range(choice.count())]
    assert names == ["No project", "Home"]  # An archived project takes no new notes.
    editor.body.setPlainText("Typed before choosing")

    editor._autosave_now()  # Typing queued first, as the debounce would.
    choice.setCurrentIndex(1)
    choice.activated.emit(1)
    await drain_writes(window)

    assert [call[0] for call in backend.draft_calls[-2:]] == ["autosave_draft", "set_note_project"]
    saved = backend.drafts[editor.draft.public_id]  # type: ignore[union-attr]
    assert (saved.project_public_id, saved.body) == (HOME, "Typed before choosing")
    assert editor.draft is not None and editor.draft.revision == saved.revision
    assert choice.currentData() == HOME
    assert window.drafts_panel.list.item(0).text().endswith("in Home")


async def test_a_refused_project_leaves_the_choice_as_it_was(qtbot: QtBot) -> None:
    window, backend = await notes_window(qtbot)
    await open_notes(window)
    editor = window.draft_editor
    backend.archived_projects.add(HOME)  # Archived elsewhere since the list loaded.
    backend.projects = (project(HOME, "Home", archived=True), project(OLD, "Old", archived=True))

    editor.project_choice.setCurrentIndex(1)
    editor.project_choice.activated.emit(1)
    await drain_writes(window)

    assert editor.status.text() == "That project is archived or no longer available."
    assert editor.project_choice.currentData() is None
    assert editor.project_choice.isEnabled()
    # The projects were reloaded, so it isn't offered again.
    choice = editor.project_choice
    assert [choice.itemText(index) for index in range(choice.count())] == ["No project"]


async def test_a_note_changed_elsewhere_shows_the_conflict(qtbot: QtBot) -> None:
    window, backend = await notes_window(qtbot)
    await open_notes(window)
    editor = window.draft_editor
    assert editor.draft is not None
    backend.change_elsewhere(editor.draft.public_id)

    editor.project_choice.setCurrentIndex(1)
    editor.project_choice.activated.emit(1)
    await drain_writes(window)

    assert editor.status.text() == CONFLICT
    assert editor.in_conflict and not editor.project_choice.isEnabled()
    assert backend.drafts[editor.draft.public_id].project_public_id is None


async def test_link_to_task_with_the_keyboard_then_unlink(qtbot: QtBot) -> None:
    window, backend = await notes_window(qtbot)
    await open_notes(window)
    editor = window.draft_editor
    picker = editor.task_picker
    assert editor.task_line.text() == "None" and editor.unlink_task_button.isHidden()

    editor.link_task_button.click()
    assert picker.isVisible()
    await window.drain_calendar_reads()
    assert picker.choices.count() == 1
    assert backend.task_choice_queries == [""]
    assert picker.choices.item(0).text() == "Paint the hall"
    QTest.keyClick(picker.choices, Qt.Key.Key_Return)
    await drain_writes(window)

    assert not picker.isVisible()
    assert editor.task_line.text() == "Paint the hall"
    assert not editor.unlink_task_button.isHidden()
    assert editor.link_task_button.text() == "Change tas&k…"
    public_id = editor.draft.public_id  # type: ignore[union-attr]
    assert backend.drafts[public_id].action_public_id == TASK.public_id

    editor.unlink_task_button.click()
    await drain_writes(window)
    assert editor.task_line.text() == "None"
    assert backend.drafts[public_id].action_title is None
    assert [call[0] for call in calls(backend, "set_note_task")] == ["set_note_task"] * 2


async def test_typing_in_the_picker_searches_for_the_latest_words(qtbot: QtBot) -> None:
    window, backend = await notes_window(qtbot)
    await open_notes(window)
    editor = window.draft_editor
    picker = editor.task_picker
    editor.link_task_button.click()
    await window.drain_calendar_reads()

    QTest.keyClicks(picker.query, "pa")
    assert picker.choices.count() == 0 and picker._timer.isActive()
    picker._timer.stop()
    picker._timer.timeout.emit()  # The pause after typing.
    await window.drain_calendar_reads()
    assert backend.task_choice_queries == ["", "pa"]
    assert picker.choices.count() == 1 and picker.link_button.isEnabled()
    picker.cancel_button.click()
    await asyncio.sleep(0)
    assert not editor.task_picker.isVisible()
    assert calls(backend, "set_note_task") == []


async def test_new_note_starts_in_the_filtered_project(qtbot: QtBot) -> None:
    window, backend = await notes_window(qtbot)
    await open_notes(window)
    window.drafts_panel.filter_project(HOME)
    await drain_writes(window)

    window.drafts_panel.new_button.click()
    await finish(window)
    await drain_writes(window)

    (created,) = [call for call in calls(backend, "create_draft") if len(call) == 3]
    assert created == ("create_draft", DraftKind.NOTE, HOME)
    assert window.draft_editor.draft is not None
    assert window.draft_editor.draft.project_public_id == HOME
    assert window.drafts_panel.selected() is not None
    assert window.drafts_panel.selected().public_id == window.draft_editor.draft.public_id  # type: ignore[union-attr]


async def test_moving_the_edited_note_out_of_the_filter_keeps_it_shown(qtbot: QtBot) -> None:
    window, _backend = await notes_window(qtbot)
    await open_notes(window)
    editor = window.draft_editor
    window.drafts_panel.filter_project("unassigned")
    await drain_writes(window)
    assert editor.draft is not None
    note_id = editor.draft.public_id

    editor.project_choice.setCurrentIndex(1)
    editor.project_choice.activated.emit(1)
    await drain_writes(window)

    assert window.drafts_panel.project_filter.currentData() == "all"
    assert window.drafts_panel.selected() is not None
    assert window.drafts_panel.selected().public_id == note_id  # type: ignore[union-attr]
    assert editor.draft is not None and editor.draft.public_id == note_id


@pytest.mark.parametrize("mode", list(ThemeMode))
async def test_notes_page_renders(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    """The gallery's Notes page: a note in a project and linked to a task, in each mode."""
    apply_theme(qapp, mode)
    window, backend = await notes_window(qtbot)
    for title, body in (
        ("Kitchen ideas", "Paint samples: sage, warm white.\nAsk about the tiles."),
        ("Reading list", "Three books for the trip."),
    ):
        note = await backend.create_draft(DraftKind.NOTE)
        await backend.autosave_draft(
            note.public_id, note.revision, DraftEdit(title=title, body=body)
        )
    window.resize(1100, 720)
    window.start(window._refresh_drafts)
    await finish(window)
    await open_notes(window)
    editor = window.draft_editor
    editor.project_choice.setCurrentIndex(1)
    editor.project_choice.activated.emit(1)
    await drain_writes(window)
    editor.link_task_button.click()
    await window.drain_calendar_reads()
    QTest.keyClick(editor.task_picker.choices, Qt.Key.Key_Return)
    await drain_writes(window)
    QApplication.processEvents()
    assert editor.task_line.text() == "Paint the hall"
    assert editor.heading.text() == "LOCAL NOTE"
    save_shot(window.grab(), f"page-notes-{mode.value}")
    editor.link_task_button.click()
    await window.drain_calendar_reads()
    QApplication.processEvents()
    save_shot(editor.task_picker.grab(), f"dialog-task-picker-{mode.value}")
    editor.task_picker.reject()


async def test_a_link_change_goes_to_the_note_it_was_made_for(qtbot: QtBot) -> None:
    """A draft switch queued ahead of a project change must not send it to the next note."""
    window, backend = await notes_window(qtbot)
    other = await backend.create_draft(DraftKind.NOTE)
    await backend.autosave_draft(other.public_id, other.revision, DraftEdit(title="Other"))
    window.start(window._refresh_drafts)
    await finish(window)
    await open_notes(window)
    editor = window.draft_editor
    assert editor.draft is not None
    held = editor.draft.public_id
    # The other note the Notes view lists (the email draft isn't listed there).
    target = next(row.public_id for row in window.drafts_panel._drafts if row.public_id != held)
    backend.draft_gate = asyncio.Event()  # An autosave in flight keeps the queue busy.
    editor.body.setPlainText("Typed first")
    editor._autosave_now()

    window.drafts_panel.select_draft(target)  # Queues the switch to the other note.
    editor.project_choice.setCurrentIndex(1)  # Still the first note's editor.
    editor.project_choice.activated.emit(1)
    backend.draft_gate.set()
    await drain_writes(window)

    assert backend.drafts[held].project_public_id == HOME
    assert backend.drafts[held].body == "Typed first"
    assert backend.drafts[target].project_public_id is None
    assert editor.draft is not None and editor.draft.public_id == target
    assert editor.project_choice.currentData() is None


async def test_link_changes_queue_in_order_and_keep_the_field_enabled(qtbot: QtBot) -> None:
    window, backend = await notes_window(qtbot)
    await open_notes(window)
    editor = window.draft_editor
    choice = editor.project_choice
    assert editor.draft is not None
    public_id = editor.draft.public_id
    backend.draft_gate = asyncio.Event()
    editor.body.setPlainText("Typed")
    editor._autosave_now()

    choice.setCurrentIndex(1)
    choice.activated.emit(1)
    assert choice.isEnabled() and editor.link_task_button.isEnabled()
    choice.setCurrentIndex(0)  # Back to No project before the first change is saved.
    choice.activated.emit(0)
    editor.task_picker.chosen.emit(TASK.public_id)  # And a task, while both wait.
    backend.draft_gate.set()
    await drain_writes(window)

    saved = backend.drafts[public_id]
    assert (saved.project_public_id, saved.action_public_id) == (None, TASK.public_id)
    links = [call[0] for call in backend.draft_calls if str(call[0]).startswith("set_note")]
    assert links == ["set_note_project", "set_note_project", "set_note_task"]
    assert choice.currentData() is None and editor.task_line.text() == "Paint the hall"
    assert editor.status.text().startswith("Saved")


async def test_refreshes_never_hide_the_note_being_edited(qtbot: QtBot) -> None:
    window, backend = await notes_window(qtbot)
    await open_notes(window)
    editor = window.draft_editor
    host = window.drafts_panel.editor_host
    assert host.currentWidget() is editor
    changes: list[int] = []
    host.currentChanged.connect(changes.append)
    backend.projects = (*backend.projects, project(WORK, "Work"))

    window.start(window._refresh_actions)
    await finish(window)
    window.start(window._refresh_drafts)
    await finish(window)
    await drain_writes(window)

    assert changes == [] and host.currentWidget() is editor
    choice = editor.project_choice
    assert "Work" in [choice.itemText(index) for index in range(choice.count())]


async def test_new_note_under_an_archived_filter_shows_in_all_notes(qtbot: QtBot) -> None:
    window, backend = await notes_window(qtbot)
    await open_notes(window)
    window.drafts_panel.filter_project(OLD)
    await drain_writes(window)

    window.drafts_panel.new_button.click()
    await finish(window)
    await drain_writes(window)

    assert calls(backend, "create_draft")[-1] == ("create_draft", DraftKind.NOTE)
    assert window.drafts_panel.project_filter.currentData() == "all"
    editor = window.draft_editor
    assert editor.draft is not None and editor.draft.project_public_id is None
    assert window.drafts_panel.selected() is not None
    assert window.drafts_panel.selected().public_id == editor.draft.public_id  # type: ignore[union-attr]
