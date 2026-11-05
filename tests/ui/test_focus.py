"""Daily focus changes persist through the backend and preserve canonical task ownership."""

import asyncio
from datetime import date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QKeySequence
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import Action, ActionFilter, ActionStatus
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.day_plans import DayPlan
from mailbrief.domain.events import CalendarRange
from mailbrief.domain.today import DaySummary
from mailbrief.services.day_plans import DayPlanConflictError
from mailbrief.ui.runtime import DesktopRuntime
from mailbrief.ui.theme import ThemeMode, apply_theme, current_tokens
from tests.factories import make_action
from tests.ui.test_keyboard import activate
from tests.ui.test_main_window_layout import save_shot
from tests.ui.test_palette import NOW, ZONE, window_for
from tests.ui.test_today_view import event
from tests.ui.test_workflow import FakeBackend, finish

A = make_action(
    public_id="00000000-0000-4000-8000-000000000001",
    title="Prepare the report",
    estimate_minutes=45,
)
B = make_action(
    public_id="00000000-0000-4000-8000-000000000002", title="Book dentist", estimate_minutes=None
)
WAITING = make_action(
    public_id="00000000-0000-4000-8000-000000000003",
    title="Wait for the invoice",
    ownership=ActionOwnership.WAITING_FOR,
    estimate_minutes=90,
)


def backend_for_focus() -> FakeBackend:
    backend = FakeBackend()
    backend.actions = {
        ActionFilter.OPEN: (A, B),
        ActionFilter.WAITING: (WAITING,),
        ActionFilter.COMPLETED: (),
    }
    return backend


async def test_today_and_full_chooser_add_remove_and_reorder(qtbot: QtBot) -> None:
    window = await window_for(qtbot, backend_for_focus())
    window._set_account(None)
    window._open_focus()
    await window.drain_calendar_reads()
    assert window.focus_dialog.isVisible()
    assert window.focus_dialog.candidates.count() == 3
    window.focus_dialog.add_button.click()
    await window.drain_focus()
    await window.drain_calendar_reads()
    plan = window.focus_panel.plan
    assert plan is not None and [i.task_public_id for i in plan.items] == [A.public_id]
    window._add_captured_today(B)
    await window.drain_focus()
    await window.drain_calendar_reads()
    assert window.focus_panel.plan is not None and len(window.focus_panel.plan.items) == 2
    window.focus_dialog.panel.list.setCurrentRow(1)
    window.focus_dialog.panel.up_button.click()
    await window.drain_focus()
    await window.drain_calendar_reads()
    assert window.focus_panel.plan.items[0].task_public_id == B.public_id
    window.focus_dialog.panel.remove_button.click()
    await window.drain_focus()
    await window.drain_calendar_reads()
    remaining = window.focus_panel.plan
    assert remaining is not None and len(remaining.items) == 1
    window.focus_dialog.reject()


async def test_keyboard_order_and_known_workload_exclude_waiting(qtbot: QtBot) -> None:
    window = await window_for(qtbot, backend_for_focus())
    for action in (A, B, WAITING):
        window._add_captured_today(action)
        await window.drain_focus()
    assert "45 min" in window.focus_panel.workload.text()
    assert "1 without estimates" in window.focus_panel.workload.text()
    assert "1 waiting" in window.focus_panel.workload.text()
    window.focus_panel.list.setCurrentRow(1)
    activate(qtbot, window)
    window.focus_panel.list.setFocus()
    QTest.keySequence(window.focus_panel.list, QKeySequence("Alt+Up"))
    await window.drain_focus()
    assert window.focus_panel.plan is not None
    assert window.focus_panel.plan.items[0].task_public_id == B.public_id


async def test_direct_completion_leaves_done_selection_visible(qtbot: QtBot) -> None:
    backend = backend_for_focus()
    calls: list[str] = []

    async def complete(public_id: str, revision: int) -> Action:
        calls.append(public_id)
        completed = A.model_copy(
            update={
                "status": ActionStatus.COMPLETED,
                "revision": revision + 1,
                "completed_at_utc": NOW,
            }
        )
        backend.actions[ActionFilter.OPEN] = (B,)
        backend.actions[ActionFilter.COMPLETED] = (completed,)
        return completed

    backend.complete_action = complete  # type: ignore[method-assign]
    window = await window_for(qtbot, backend)
    window._add_captured_today(A)
    await window.drain_focus()
    activate(qtbot, window)
    window.focus_panel.complete_button.setFocus()
    QTest.keyClick(window.focus_panel.complete_button, Qt.Key.Key_Space)
    await window.drain_focus()
    assert calls == [A.public_id]
    assert window.focus_panel.plan is not None and len(window.focus_panel.plan.items) == 1
    assert window.focus_panel.complete_button.text() == "Reopen"
    assert "0 min" in window.focus_panel.workload.text()
    assert window.undo_button.isVisible()


async def test_conflict_reloads_instead_of_overwriting_order(qtbot: QtBot) -> None:
    backend = backend_for_focus()
    window = await window_for(qtbot, backend)
    window._add_captured_today(A)
    await window.drain_focus()

    async def rejected(day: object, item_id: str, revision: int) -> DayPlan:
        raise DayPlanConflictError("Your day plan changed. Reload it before making another change.")

    backend.deselect_day_task = rejected  # type: ignore[method-assign]
    window.focus_panel.remove_button.click()
    await window.drain_focus()
    assert "changed" in window.focus_panel.status.text()
    assert window.focus_panel.plan is not None and len(window.focus_panel.plan.items) == 1


async def test_midnight_opens_an_empty_new_plan_without_changing_yesterday(qtbot: QtBot) -> None:
    backend = backend_for_focus()
    window = await window_for(qtbot, backend)
    window._add_captured_today(A)
    await window.drain_focus()
    assert window.focus_panel.plan is not None
    yesterday = window.focus_panel.plan
    window.now = lambda: NOW + timedelta(days=1)
    await window._refresh_day()
    assert window.focus_panel.plan is not None and not window.focus_panel.plan.items
    assert backend.focus_plans[yesterday.day].items == yesterday.items


@pytest.mark.parametrize("travel", [False, True])
async def test_date_rollover_refreshes_focus_while_foreground_work_is_busy(
    qtbot: QtBot, monkeypatch: pytest.MonkeyPatch, travel: bool
) -> None:
    backend = backend_for_focus()
    window = await window_for(qtbot, backend)
    window._add_captured_today(A)
    await window.drain_focus()
    previous = window.focus_panel.plan
    assert previous is not None
    window._open_focus()
    await window.drain_calendar_reads()
    release = asyncio.Event()

    async def hold() -> None:
        await release.wait()

    window.start(hold)
    await asyncio.sleep(0)
    if travel:
        window._system_zone = True
        monkeypatch.setattr(
            "mailbrief.ui.main_window.resolve_timezone", lambda _: ZoneInfo("Asia/Tokyo")
        )
    else:
        window.now = lambda: NOW + timedelta(days=1)
    try:
        window._minute_tick()
        await window.drain_calendar_reads()
        current = window.focus_panel.plan
        assert current is not None and current.day == window._local_today()
        assert current.day != previous.day and not current.items
        assert window.focus_dialog.panel.plan == current
        assert window._day_refresh_pending and not release.is_set()
        assert backend.focus_plans[previous.day] == previous
    finally:
        release.set()
        await finish(window)
        window.focus_dialog.reject()


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
async def test_long_agenda_preview_is_bounded_and_opens_the_full_day(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    apply_theme(qapp, mode=mode)
    window = await window_for(qtbot, backend_for_focus())
    window._add_captured_today(A)
    await window.drain_focus()
    window.resize(680, 520)
    day = NOW.astimezone(ZONE).date()
    appointments = tuple(event(i, "Long appointment title " * 8, 10 + i) for i in (1, 2))
    window.focus_panel.show_overview(
        DaySummary(
            day=day,
            attention=(),
            replies=(),
            focus=window.focus_panel.plan,
            calendar=CalendarRange(first_day=day, last_day=day, events=appointments, markers=()),
        ),
        ZONE,
    )
    QApplication.processEvents()
    panel = window.focus_panel
    assert panel.agenda_next.isVisible()
    for row in (panel.agenda, panel.agenda_next):
        assert not row.wordWrap()
        assert row.height() >= row.fontMetrics().height()
        assert row.accessibleName() == row.text()
        assert panel.agenda_preview.rect().contains(row.geometry())
    assert window.workspace.brief_list.height() >= 48
    save_shot(window.grab(), f"focus-long-agenda-{mode}")
    activate(qtbot, window)
    panel.agenda_button.setFocus()
    QTest.keyClick(panel.agenda_button, Qt.Key.Key_Space)
    await window.drain_calendar_reads()
    assert window.workspace.current_page() == "calendar"
    assert window._calendar_bounds()[0] <= day <= window._calendar_bounds()[1]


async def test_capture_can_add_the_saved_task_to_today(qtbot: QtBot) -> None:
    window = await window_for(qtbot, backend_for_focus())
    window._request_capture()
    dialog = window.capture_dialog
    dialog.title.setText("New captured work")
    dialog.save_button.click()
    await window.drain_capture()
    assert dialog.today_button.isVisible()
    dialog.today_button.click()
    await window.drain_focus()
    assert window.focus_panel.plan is not None and len(window.focus_panel.plan.items) == 1
    assert "added to My focus" in dialog.status.text()
    dialog.reject()


async def test_canceled_old_task_search_cannot_replace_new_query_results(qtbot: QtBot) -> None:
    backend = backend_for_focus()
    started, release = asyncio.Event(), asyncio.Event()

    async def candidates(day: date, text: str = "") -> tuple[Action, ...]:
        if text == "old":
            started.set()
            await release.wait()
            return (A,)
        return (B,)

    backend.focus_candidates = candidates  # type: ignore[method-assign]
    window = await window_for(qtbot, backend)
    window._open_focus()
    await window.drain_calendar_reads()
    window.focus_dialog.query.setText("old")
    window._search_focus("old")
    await started.wait()
    window.focus_dialog.query.setText("new")
    window._search_focus("new")
    await asyncio.sleep(0)
    release.set()
    await window.drain_calendar_reads()
    assert window.focus_dialog.candidates.item(0).text().startswith(B.title)
    window.focus_dialog.reject()


async def test_runtime_plan_survives_restart_and_task_completion(tmp_path: Path) -> None:
    path = tmp_path / "owner.sqlite3"
    runtime = DesktopRuntime(path)
    await runtime.load_saved()
    try:
        from tests.unit.services.test_manual_tasks import EDIT

        task = await runtime.create_action(EDIT, ())
        plan = await runtime.select_day_task(NOW.astimezone(ZONE).date(), ZONE, task.public_id, 0)
    finally:
        await runtime.close()
    reopened = DesktopRuntime(path)
    await reopened.load_saved()
    try:
        summary = await reopened.today_summary(plan.day, ZONE, NOW)
        assert summary.focus == plan
        await reopened.complete_action(task.public_id, task.revision)
        summary = await reopened.today_summary(plan.day, ZONE, NOW)
        assert summary.focus is not None and summary.focus.items[0].action is not None
        assert summary.focus.items[0].action.status is ActionStatus.COMPLETED
    finally:
        await reopened.close()


async def test_shutdown_waits_for_inflight_focus_save(qtbot: QtBot) -> None:
    backend = backend_for_focus()
    original = backend.select_day_task
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed(day: date, zone: ZoneInfo, task_id: str, revision: int) -> DayPlan:
        started.set()
        await release.wait()
        return await original(day, zone, task_id, revision)

    backend.select_day_task = delayed  # type: ignore[method-assign]
    window = await window_for(qtbot, backend)
    window._add_captured_today(A)
    await started.wait()
    closing = asyncio.create_task(window.shutdown())
    await asyncio.sleep(0)
    assert not closing.done()
    release.set()
    await closing
    assert backend.focus_plans[NOW.astimezone(ZONE).date()].items[0].task_public_id == A.public_id


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
@pytest.mark.parametrize("size", [(1100, 720), (680, 520)])
async def test_focus_gallery(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode, size: tuple[int, int]
) -> None:
    apply_theme(qapp, mode=mode)
    window = await window_for(qtbot, backend_for_focus())
    for action in (A, B, WAITING):
        window._add_captured_today(action)
        await window.drain_focus()
    window.resize(*size)
    QApplication.processEvents()
    assert window.focus_panel.rect().contains(window.focus_panel.attention_button.geometry())
    assert window.workspace.brief_list.height() >= 48
    save_shot(window.grab(), f"focus-today-{size[0]}-{mode}")
    window._open_focus()
    await window.drain_calendar_reads()
    QApplication.processEvents()
    save_shot(window.focus_dialog.grab(), f"dialog-focus-{mode}")
    window.focus_dialog.reject()


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
async def test_today_focus_card_corners_follow_the_sheet(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    apply_theme(qapp, mode=mode)
    window = await window_for(qtbot, backend_for_focus())
    window.resize(1100, 720)
    QApplication.processEvents()
    panel = window.focus_panel
    pages = window.workspace.sheet.pages
    # Flush with the sheet's top, left and right inside its hairline...
    assert panel.mapTo(pages, QPoint(0, 0)) == QPoint(0, 0)
    assert panel.width() == pages.width()
    # ...so it is a plain band, square down to the straight edge below it, and the
    # sheet's mask alone rounds its top corners, as it rounds every page's.
    image = panel.grab().toImage()
    card = QColor(current_tokens().card)
    for x, y in ((0, 0), (panel.width() - 1, 0), (0, panel.height() - 1)):
        assert image.pixelColor(x, y) == card
    assert not pages.mask().contains(QPoint(0, 0))
    assert not pages.mask().contains(QPoint(pages.width() - 1, 0))
    # The chooser's panel stands alone, so it keeps a card's own rounded corners.
    dialog_panel = window.focus_dialog.panel
    dialog_panel.resize(400, 200)
    assert dialog_panel.grab().toImage().pixelColor(0, 0) != card
