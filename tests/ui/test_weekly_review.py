"""The weekly review dialog (P5-1): reads a week, opens tasks and acts only when asked."""

from datetime import timedelta

import pytest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import ActionFilter
from mailbrief.domain.drafts import DraftKind
from mailbrief.domain.weekly_review import week_start
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.palette import COMMANDS
from mailbrief.ui.theme import ThemeMode, apply_theme
from tests.factories import make_action
from tests.ui.test_main_window_layout import save_shot
from tests.ui.test_workflow import FakeBackend, finish
from tests.ui.window_wait import show


async def settle(window: MainWindow) -> None:
    await window.drain_calendar_reads()
    QApplication.processEvents()


def task_rows(window: MainWindow) -> list[str]:
    return [row.title for row in window.weekly_review._rows if row is not None]


async def test_review_reads_marks_focuses_and_writes_a_reflection(qtbot: QtBot) -> None:
    backend = FakeBackend()
    window = MainWindow(backend)
    qtbot.addWidget(window)
    await window.initialize()
    today = window._local_today()
    monday = week_start(today)
    late = make_action(title="Left from last week", target_date=monday - timedelta(days=2))
    backend.actions[ActionFilter.OPEN] = (late,)
    try:
        assert window.weekly_review_button.objectName() == "openWeeklyReview"
        window.weekly_review_button.click()
        await settle(window)
        dialog = window.weekly_review
        assert dialog.isVisible()
        assert backend.review_calls == [monday]
        assert task_rows(window) == ["Left from last week"]
        assert "Not marked reviewed." in dialog.status.text()
        assert dialog.focus_day.count() == 7
        assert dialog.focus_day.itemData(0) == monday + timedelta(days=7)

        row = next(index for index, found in enumerate(dialog._rows) if found is not None)
        dialog.list.setCurrentRow(row)
        assert dialog.focus_button.isEnabled()
        dialog.focus_button.click()
        await settle(window)
        plan = backend.focus_plans[monday + timedelta(days=7)]
        assert [item.task_public_id for item in plan.items] == [late.public_id]
        assert backend.actions[ActionFilter.OPEN] == (late,)  # The task is unchanged.

        assert dialog.review_button.text() == "Mark week reviewed"
        dialog.review_button.click()
        await settle(window)
        assert backend.week_records[monday].reviewed
        assert dialog.review_button.text() == "Unmark reviewed"
        assert dialog.status.text().startswith("Reviewed ")
        dialog.review_button.click()
        await settle(window)
        assert not backend.week_records[monday].reviewed

        dialog.previous_button.click()
        await settle(window)
        assert backend.review_calls[-1] == monday - timedelta(days=7)
        dialog.this_week_button.click()
        await settle(window)
        assert backend.review_calls[-1] == monday

        dialog.reflection_button.click()
        await settle(window)
        await finish(window)
        note_id = backend.week_records[monday].reflection_note_public_id
        assert note_id is not None
        assert backend.drafts[note_id].kind is DraftKind.NOTE
        assert not dialog.isVisible()
    finally:
        window.weekly_review.reject()
        window.close()
        await window.shutdown()


async def test_palette_opens_this_and_last_week(qtbot: QtBot) -> None:
    assert {command.key for command in COMMANDS} >= {"review:week", "review:last"}
    backend = FakeBackend()
    window = MainWindow(backend)
    qtbot.addWidget(window)
    await window.initialize()
    monday = week_start(window._local_today())
    try:
        window._run_command("review:last")
        await settle(window)
        assert backend.review_calls[-1] == monday - timedelta(days=7)
        window.weekly_review.reject()
        window._run_command("review:week")
        await settle(window)
        assert backend.review_calls[-1] == monday
        backend.day_fail = RuntimeError("unreadable")
        window.weekly_review.next_button.click()
        await settle(window)
        assert window.weekly_review.status.text() == "Could not read the week. Reopen to retry."
    finally:
        window.weekly_review.reject()
        window.close()
        await window.shutdown()


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
async def test_weekly_review_gallery(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    apply_theme(qapp, mode)
    backend = FakeBackend()
    window = MainWindow(backend)
    qtbot.addWidget(window)
    await window.initialize()
    monday = week_start(window._local_today())
    backend.actions[ActionFilter.OPEN] = (
        make_action(title="Send the quarterly summary", target_date=monday),
        make_action(
            public_id="1c5e2c1d-6b8e-4f55-9d0e-2a7f3b9c1e44",
            title="Book the venue",
            target_date=monday + timedelta(days=8),
        ),
    )
    window.resize(1100, 720)
    show(qtbot, window)
    try:
        window._open_weekly_review()
        await settle(window)
        for _ in range(3):
            QApplication.processEvents()
        save_shot(window.weekly_review.grab(), f"p5-weekly-review-{mode.value}")
        assert task_rows(window)[0] == "Send the quarterly summary"
    finally:
        window.weekly_review.reject()
        window.close()
        await window.shutdown()
