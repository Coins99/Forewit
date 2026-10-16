"""Project manager and task grouping work with no connected provider."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from PySide6.QtWidgets import QLabel
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import ActionEdit, ActionFilter
from mailbrief.domain.projects import AreaEdit, LifeArea, Project, ProjectEdit
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.runtime import DesktopRuntime
from tests.factories import make_action
from tests.ui.test_workflow import FakeBackend

NOW = datetime(2026, 10, 11, tzinfo=UTC)
AREA = LifeArea(
    public_id="11111111-1111-4111-8111-111111111111",
    revision=1,
    name="Work",
    created_at_utc=NOW,
    updated_at_utc=NOW,
)
PROJECT = Project(
    public_id="22222222-2222-4222-8222-222222222222",
    revision=1,
    name="Launch",
    area_public_id=AREA.public_id,
    area_name=AREA.name,
    created_at_utc=NOW,
    updated_at_utc=NOW,
)


async def finish(window: MainWindow) -> None:
    assert window.task is not None
    await window.task


async def test_manager_creates_area_project_assigns_task_and_archives(qtbot: QtBot) -> None:
    backend = FakeBackend()
    window = MainWindow(backend)
    qtbot.addWidget(window)
    await window.initialize()
    window._show_page("actions")
    menu = window.actions_panel.organize_button.menu()
    assert menu is not None
    menu.actions()[0].trigger()
    await finish(window)
    dialog = window.projects_dialog
    assert dialog.isVisible()
    dialog.kind.setCurrentIndex(1)
    dialog.name.setText("Learning")
    dialog.notes.setPlainText("Courses and practice")
    dialog.save.click()
    await finish(window)
    assert len(backend.areas) == 1
    area = backend.areas[0]
    dialog.kind.setCurrentIndex(0)
    dialog.name.setText("Study plan")
    dialog.area.setCurrentIndex(dialog.area.findData(area.public_id))
    dialog.save.click()
    await finish(window)
    project = backend.projects[0]
    assert project.area_public_id == area.public_id
    dialog.archive.click()
    await finish(window)
    assert (await backend.list_projects())[0].archived_at_utc is not None
    dialog.archive.click()
    await finish(window)
    assert (await backend.list_projects())[0].archived_at_utc is None
    dialog.reject()
    window._request_new_task()
    editor = window.action_editor
    editor.title.setText("Read first chapter")
    editor.project.setCurrentIndex(editor.project.findData(project.public_id))
    editor._save()
    await finish(window)
    call = backend.action_calls[-1]
    assert call[0] == "create_action"
    assert isinstance(call[1], ActionEdit)
    assert call[1].project_public_id == project.public_id


async def test_filter_and_edit_keep_organization(qtbot: QtBot) -> None:
    backend = FakeBackend()
    backend.projects, backend.areas = (PROJECT,), (AREA,)
    assigned = make_action(project=PROJECT)
    unassigned = make_action(public_id="33333333-3333-4333-8333-333333333333")
    backend.actions = {ActionFilter.OPEN: (assigned, unassigned)}
    window = MainWindow(backend)
    qtbot.addWidget(window)
    await window.initialize()
    window._show_page("actions")
    panel = window.actions_panel
    panel.group.setCurrentIndex(panel.group.findData("project:" + PROJECT.public_id))
    await finish(window)
    assert panel.lists[ActionFilter.OPEN].count() == 1
    assert panel.selected() == assigned
    assert [label.text() for label in panel.detail.findChildren(QLabel, "actionProject")] == [
        "Project: Launch · Work"
    ]
    panel.edit_button.click()
    editor = window.action_editor
    assert editor.project.currentData() == PROJECT.public_id
    editor.title.setText("Renamed task")
    editor._save()
    await finish(window)
    edit = backend.action_calls[-1][3]
    assert isinstance(edit, ActionEdit)
    assert edit.project_public_id == PROJECT.public_id
    panel.group.setCurrentIndex(panel.group.findData("area:" + AREA.public_id))
    await finish(window)
    assert panel.lists[ActionFilter.OPEN].count() == 1
    panel.group.setCurrentIndex(panel.group.findData("unassigned"))
    await finish(window)
    assert panel.selected() == unassigned


async def test_manager_failed_save_keeps_input_and_remove_area_keeps_project(qtbot: QtBot) -> None:
    backend = FakeBackend()
    backend.areas, backend.projects = (AREA,), (PROJECT,)
    window = MainWindow(backend)
    qtbot.addWidget(window)
    await window.initialize()
    window._open_organization()
    await finish(window)
    dialog = window.projects_dialog
    dialog.name.setText("Keep my name")
    backend.action_fail = RuntimeError("synthetic failure")
    dialog.save.click()
    await finish(window)
    assert dialog.name.text() == "Keep my name"
    assert "input is still here" in dialog.status.text()
    dialog.discard.click()
    dialog.kind.setCurrentIndex(1)
    dialog.list.setCurrentRow(0)
    dialog.delete_area.click()
    await finish(window)
    assert await backend.list_areas() == ()
    assert len(backend.projects) == 1


def test_archived_project_is_available_only_to_already_linked_task(qtbot: QtBot) -> None:
    from PySide6.QtWidgets import QWidget

    from mailbrief.ui.action_editor import ActionEditor

    parent = QWidget()
    qtbot.addWidget(parent)
    editor = ActionEditor(parent)
    archived = PROJECT.model_copy(update={"archived_at_utc": NOW})
    editor.set_projects((archived,))
    editor.edit(make_action(project=archived), NOW.date())
    assert editor.project.currentData() == PROJECT.public_id
    editor.new_task(NOW.date())
    assert editor.project.findData(PROJECT.public_id) == -1


async def test_runtime_organization_api_and_restart(tmp_path: Path) -> None:
    runtime = DesktopRuntime(tmp_path / "owner.sqlite3")
    try:
        await runtime.load_saved()
        area = await runtime.save_organization("area", None, None, AreaEdit(name="Home"))
        project = await runtime.save_organization(
            "project", None, None, ProjectEdit(name="Move", area_public_id=area.public_id)
        )
        assert await runtime.list_areas() == (area,)
        assert await runtime.list_projects() == (project,)
        area = await runtime.save_organization("area", area.public_id, 1, AreaEdit(name="Life"))
        project = await runtime.save_organization(
            "project", project.public_id, 1, ProjectEdit(name="Move house")
        )
        assert (
            await runtime.list_grouped_actions(ActionFilter.OPEN, "project", project.public_id)
            == ()
        )
        assert (
            await runtime.count_grouped_actions(ActionFilter.OPEN, "project", project.public_id)
            == 0
        )
        await runtime.archive_project(project.public_id, 2, True)
        await runtime.delete_area(area.public_id, 2)
        with pytest.raises(ValueError):
            await runtime.save_organization("bad", None, None, AreaEdit(name="Invalid"))
    finally:
        await runtime.close()


async def test_dirty_manager_requires_save_or_discard_before_archive_navigation_and_close(
    qtbot: QtBot,
) -> None:
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    backend = FakeBackend()
    backend.projects, backend.areas = (PROJECT,), (AREA,)
    window = MainWindow(backend)
    qtbot.addWidget(window)
    await window.initialize()
    window._open_organization()
    await finish(window)
    dialog = window.projects_dialog
    dialog.list.setCurrentRow(0)
    dialog.notes.setPlainText("Preserve these notes")
    assert not dialog.archive.isEnabled()
    assert not dialog.kind.isEnabled()
    assert not dialog.list.isEnabled()
    assert not dialog.new.isEnabled()
    dialog.archive.click()
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    dialog.close()
    assert dialog.isVisible()
    assert dialog.notes.toPlainText() == "Preserve these notes"
    dialog.save.click()
    await finish(window)
    assert dialog.archive.isEnabled()
    dialog.archive.click()
    await finish(window)
    assert backend.projects[0].notes == "Preserve these notes"
    assert dialog.notes.toPlainText() == "Preserve these notes"
    dialog.name.setText("Discard me")
    dialog.discard.click()
    assert dialog.name.text() == PROJECT.name
    assert not dialog.is_dirty()
    dialog.set_busy(True)
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    dialog.close()
    assert dialog.isVisible()
    dialog.set_busy(False)
    dialog.reject()
    assert not dialog.isVisible()


async def test_committed_save_survives_refresh_failure_and_reopen_reads_fresh_records(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend = FakeBackend()
    backend.projects, backend.areas = (PROJECT,), (AREA,)
    window = MainWindow(backend)
    qtbot.addWidget(window)
    await window.initialize()
    window._open_organization()
    await finish(window)
    dialog = window.projects_dialog
    dialog.list.setCurrentRow(0)
    dialog.name.setText("Committed name")
    original = backend.list_projects

    async def fail_read() -> tuple[Project, ...]:
        raise RuntimeError("Synthetic read failure")

    monkeypatch.setattr(backend, "list_projects", fail_read)
    dialog.save.click()
    await finish(window)
    assert backend.projects[0].name == "Committed name"
    assert dialog.name.text() == "Committed name"
    assert dialog._selected is not None and dialog._selected.revision == 2
    dialog.reject()
    backend.projects = (
        backend.projects[0].model_copy(update={"name": "Fresh name", "revision": 3}),
    )
    monkeypatch.setattr(backend, "list_projects", original)
    window._open_organization()
    await finish(window)
    assert dialog.name.text() == "Fresh name"
    assert dialog._selected is not None and dialog._selected.revision == 3


async def test_tags_reach_the_filter_and_the_editor(qtbot: QtBot) -> None:
    backend = FakeBackend()
    tagged = make_action(tags=("Home",))
    plain = make_action(public_id="44444444-4444-4444-8444-444444444444")
    backend.actions = {ActionFilter.OPEN: (tagged, plain)}
    backend.tags = ("Home",)
    window = MainWindow(backend)
    qtbot.addWidget(window)
    await window.initialize()
    window._show_page("actions")
    panel = window.actions_panel
    panel.group.setCurrentIndex(panel.group.findData("tag:home"))
    await finish(window)
    assert panel.lists[ActionFilter.OPEN].count() == 1
    assert panel.selected() == tagged
    completer = window.action_editor.tags.completer()
    assert completer is not None
    model = completer.model()
    assert [model.index(row, 0).data() for row in range(model.rowCount())] == ["Home"]
