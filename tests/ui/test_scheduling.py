"""Synthetic proposal review, uncertain retry and additive editor controls."""

import asyncio
from datetime import date
from unittest.mock import AsyncMock

import pytest
from PySide6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from mailbrief.domain.projects import Project
from mailbrief.domain.scheduling import (
    PlanningCandidatePage,
    PlanningResult,
    PlanningReview,
    PlanningUndo,
    SchedulingProposal,
    SchedulingRequest,
)
from mailbrief.services.scheduling import (
    SchedulingChangedError,
    SchedulingKnowledgeError,
    SchedulingService,
)
from mailbrief.storage.database import Database
from mailbrief.ui.hours_editor import HoursEditor
from mailbrief.ui.scheduling_view import SchedulingDialog
from mailbrief.ui.theme import ThemeMode, apply_theme
from tests.ui.test_main_window_layout import save_shot
from tests.unit.services.test_scheduling import DAY, NOW, ZONE, setup


class Backend:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.lose_response = False

    async def list_projects(self) -> tuple[Project, ...]:
        return ()

    async def planning_candidates(
        self,
        first: date,
        last: date,
        *,
        query: str = "",
        offset: int = 0,
        needs_time: bool = True,
        project_id: str | None = None,
        focus_only: bool = False,
        focus_day: date | None = None,
    ) -> PlanningCandidatePage:
        async with self.database.session() as session:
            return await SchedulingService(session, clock=lambda: NOW, zone=ZONE).candidates(
                first,
                last,
                query=query,
                offset=offset,
                needs_time=needs_time,
                project_id=project_id,
                focus_only=focus_only,
                focus_day=focus_day,
            )

    async def review_schedule(self, request: SchedulingRequest) -> PlanningReview:
        async with self.database.session() as session:
            return await SchedulingService(session, clock=lambda: NOW, zone=ZONE).review(request)

    async def apply_schedule(
        self,
        proposal: SchedulingProposal,
        selected: tuple[str, ...],
        command_id: str,
        *,
        acknowledged_knowledge: str | None = None,
    ) -> PlanningResult:
        async with self.database.session() as session:
            result = await SchedulingService(session, clock=lambda: NOW, zone=ZONE).apply(
                proposal, selected, command_id, acknowledged_knowledge=acknowledged_knowledge
            )
        if self.lose_response:
            self.lose_response = False
            raise RuntimeError("Synthetic lost response")
        return result

    async def undo_schedule(self, token: PlanningUndo, command_id: str) -> PlanningResult:
        async with self.database.session() as session:
            return await SchedulingService(session, clock=lambda: NOW, zone=ZONE).undo(
                token, command_id
            )


async def ready(dialog: SchedulingDialog) -> None:
    if dialog._reads:
        await asyncio.gather(*tuple(dialog._reads))
    if dialog.chooser._reads:
        await asyncio.gather(*tuple(dialog.chooser._reads))


async def test_project_selection_survives_reload_search_and_paging(
    qtbot: QtBot, database: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = Project(
        public_id="11111111-1111-4111-8111-111111111111",
        name="Work",
        revision=1,
        created_at_utc=NOW,
        updated_at_utc=NOW,
    )
    backend = Backend(database)
    projects = AsyncMock(return_value=(project,))
    candidates = AsyncMock(return_value=PlanningCandidatePage(items=(), next_offset=50))
    monkeypatch.setattr(backend, "list_projects", projects)
    monkeypatch.setattr(backend, "planning_candidates", candidates)
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SchedulingDialog(parent, backend)
    dialog.open_range(DAY, DAY, DAY)
    await ready(dialog)
    chooser = dialog.chooser
    chooser.projects.setCurrentIndex(chooser.projects.findData(project.public_id))
    await ready(dialog)
    chooser.query.setText("report")
    chooser.timer.stop()
    chooser.load()
    await ready(dialog)
    chooser.more_button.click()
    await ready(dialog)
    assert chooser.projects.count() == 2
    assert chooser.projects.currentData() == project.public_id
    assert candidates.await_args is not None
    assert candidates.await_args.kwargs["project_id"] == project.public_id
    assert candidates.await_args.kwargs["query"] == "report"
    assert candidates.await_args.kwargs["offset"] == 50
    projects.assert_awaited_once()
    dialog.force_close()
    await dialog.drain()


@pytest.mark.parametrize("knowledge", [False, True])
async def test_definitive_retry_rejection_unfreezes_review(
    qtbot: QtBot, database: Database, monkeypatch: pytest.MonkeyPatch, knowledge: bool
) -> None:
    async with database.session() as session:
        await setup(session)
    backend = Backend(database)
    failure = (
        SchedulingKnowledgeError("stale coverage", "new-token")
        if knowledge
        else SchedulingChangedError("Suggested time has elapsed.")
    )
    apply = AsyncMock(side_effect=[RuntimeError("Synthetic pre-commit failure"), failure])
    monkeypatch.setattr(backend, "apply_schedule", apply)
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SchedulingDialog(parent, backend)
    dialog.open_range(DAY, DAY, DAY)
    await ready(dialog)
    dialog.chooser.tasks.item(0).setSelected(True)
    dialog.chooser._review()
    dialog._generate()
    await ready(dialog)
    dialog._apply()
    assert dialog._write is not None
    await dialog._write
    assert bool(dialog._uncertain) and not dialog.discard_button.isEnabled()
    dialog._apply()
    assert dialog._write is not None
    await dialog._write
    assert apply.await_args_list[0] == apply.await_args_list[1]
    assert not dialog._uncertain and dialog._pending is None
    assert dialog.body.isEnabled() and dialog.discard_button.isEnabled()
    if knowledge:
        assert dialog._review is not None and dialog._knowledge_token == "new-token"
        dialog.ack.setChecked(True)
        assert dialog.apply_button.isEnabled()
    else:
        assert dialog._review is None and dialog.review_button.isEnabled()
    dialog._discard()
    assert not dialog.has_unsaved_input()
    dialog.reject()
    assert dialog._closed
    await dialog.drain()


async def test_review_acknowledgement_lost_response_retry_and_undo(
    qtbot: QtBot, database: Database
) -> None:
    async with database.session() as session:
        await setup(session)
    parent = QWidget()
    qtbot.addWidget(parent)
    backend = Backend(database)
    dialog = SchedulingDialog(parent, backend)
    dialog.open_range(DAY, DAY, DAY)
    await ready(dialog)
    dialog.chooser.tasks.item(0).setSelected(True)
    dialog.chooser._review()
    dialog._generate()
    await ready(dialog)
    assert dialog._review is not None and dialog._review.proposal.blocks
    dialog._apply()
    assert dialog._write is not None
    await dialog._write
    assert dialog._knowledge_token is not None
    dialog.ack.setChecked(True)
    backend.lose_response = True
    dialog._apply()
    assert dialog._write is not None
    await dialog._write
    assert bool(dialog._uncertain) and not dialog.body.isEnabled()
    dialog._apply()
    assert dialog._write is not None
    await dialog._write
    await ready(dialog)
    assert not dialog._uncertain and dialog._undo is not None
    dialog._request_undo()
    assert dialog._write is not None
    await dialog._write
    await ready(dialog)
    assert bool(dialog._undo) is False
    dialog.force_close()
    await dialog.drain()


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
async def test_new_drawers_render_at_narrow_width(
    qtbot: QtBot, database: Database, themed: None, mode: ThemeMode
) -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    assert isinstance(app, QApplication)
    apply_theme(app, mode)
    async with database.session() as session:
        service, task = await setup(session)
        page = await service.candidates(DAY, DAY)
        assert page.items[0].task_public_id == task
        from mailbrief.services.availability import AvailabilityService

        hours = await AvailabilityService(session).hours()
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SchedulingDialog(parent, Backend(database))
    dialog.open_range(DAY, DAY, DAY, page.items)
    dialog.resize(390, 680)
    dialog.show()
    await ready(dialog)
    dialog._generate()
    await ready(dialog)
    qtbot.wait(30)
    assert dialog.form_scroll.horizontalScrollBar().maximum() == 0
    save_shot(dialog.grab(), f"p3-suggestion-{mode.value}")
    dialog._discard()
    dialog.force_close()
    await dialog.drain()
    editor = HoursEditor(parent)
    editor.edit_hours(hours)
    editor.resize(390, 700)
    editor.show()
    editor.overrides.setPlainText(f"{DAY.isoformat()} =")
    built = editor.built_edit()
    assert not isinstance(built, str) and built.overrides[0].windows == ()
    save_shot(editor.grab(), f"p3-hours-{mode.value}")
    editor.force_close()


async def test_calendar_entry_and_hours_drawer_fit_a_short_window(
    qtbot: QtBot, themed: None
) -> None:
    from PySide6.QtWidgets import QApplication

    from mailbrief.ui.main_window import MainWindow
    from tests.ui.test_workflow import FakeBackend, finish

    app = QApplication.instance()
    assert isinstance(app, QApplication)
    apply_theme(app, ThemeMode.LIGHT)
    backend = FakeBackend()
    window = MainWindow(backend)
    qtbot.addWidget(window)
    window.resize(680, 520)
    window.start(window.initialize)
    await finish(window)
    window.show()
    window._show_page("calendar")
    window.calendar_panel.tasks_button.click()
    assert window.calendar_panel.scheduling_button.isVisible()
    window.hours_editor.edit_hours(backend.hours)
    window.dialog_guard.show_dialog(window.hours_editor)
    qtbot.wait(30)
    editor = window.hours_editor
    assert editor.width() <= window.width()
    assert (
        editor.save_button.mapTo(window, editor.save_button.rect().bottomRight()).y()
        <= window.height()
    )
    assert editor.fields_scroll.horizontalScrollBar().maximum() == 0
    assert editor.fields_scroll.verticalScrollBar().maximum() > 0
    save_shot(window.grab(), "p3-hours-short-window")
    editor.force_close()
    window.close()
    await window.shutdown()
