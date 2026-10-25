"""B04 in the desktop: an open task's steps ticked, unticked and added from its detail."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import (
    MAX_ACTION_STEPS,
    ActionActivity,
    ActionActivityKind,
    ActionFilter,
    ActionStatus,
    ActionStep,
    ThreadActivity,
)
from mailbrief.domain.preferences import OwnerPreferences
from mailbrief.services.actions import ActionConflictError
from mailbrief.ui.action_detail import NEW_STEP_TEXT, StepRow
from mailbrief.ui.action_text import steps_status
from mailbrief.ui.actions_view import ActionsPanel
from mailbrief.ui.labels import WrapLabel
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.theme import apply_theme
from tests.factories import make_action
from tests.ui.test_actions_view import NOW, action, detail_texts, show, steps
from tests.ui.test_actions_workflow import finish
from tests.ui.test_keyboard import activate as activate_window
from tests.ui.test_workflow import FakeBackend

PARTY = make_action(
    title="Plan the party",
    steps=(
        ActionStep(step_id=1, position=0, text="Book the venue", done=False),
        ActionStep(step_id=2, position=1, text="Send invites", done=False),
    ),
)


@pytest.fixture
def panel(qtbot: QtBot) -> ActionsPanel:
    result = ActionsPanel()
    qtbot.addWidget(result)
    return result


def rows(panel: ActionsPanel) -> list[StepRow]:
    return panel.detail.step_rows()


def ticks(panel: ActionsPanel) -> list[bool]:
    return [row.box.isChecked() for row in rows(panel)]


def content(panel: ActionsPanel) -> QWidget:
    widget = panel.detail.widget()
    assert widget is not None
    return widget


def plan_texts(panel: ActionsPanel, count: int) -> list[str]:
    """The plan's heading and the ``count`` lines of the detail after it."""
    texts = detail_texts(panel)
    start = next(index for index, text in enumerate(texts) if text.startswith("Plan"))
    return texts[start : start + 1 + count]


def activate(qtbot: QtBot, widget: QWidget) -> None:
    """Focus needs the panel shown in an active window, wide enough for its detail."""
    widget.resize(900, 760)
    activate_window(qtbot, widget)


def test_an_open_task_s_plan_is_a_checklist_with_a_field_to_add_a_step(
    panel: ActionsPanel,
) -> None:
    show(panel, ActionFilter.OPEN, action(1, steps=steps(True, False)))

    found = rows(panel)
    assert [(row.step_id, row.box.isChecked(), row.label.text()) for row in found] == [
        (1, True, "Step 1"),
        (2, False, "Step 2"),
    ]
    assert [row.label.property("tone") for row in found] == ["muted", None]  # Done: muted.
    assert [row.box.accessibleName() for row in found] == ["Step 1", "Step 2"]
    assert [row.accessibleName() for row in found] == ["Step 1", "Step 2"]
    plan = content(panel).findChild(QWidget, "actionPlanBox")
    assert plan is not None and plan.accessibleName() == "Plan"
    assert plan_texts(panel, 2) == ["Plan · 1 of 2 done", "Step 1", "Step 2"]
    field = panel.detail.new_step
    assert field.isVisibleTo(content(panel))
    assert field.placeholderText() == field.accessibleName() == NEW_STEP_TEXT == "Add a step"


def test_an_open_task_without_steps_offers_only_the_field(panel: ActionsPanel) -> None:
    show(panel, ActionFilter.OPEN, action(1))

    assert rows(panel) == []
    assert "Plan" in detail_texts(panel)
    assert panel.detail.new_step.isVisibleTo(content(panel))


def test_a_full_plan_has_no_room_for_another_step(panel: ActionsPanel) -> None:
    show(panel, ActionFilter.OPEN, action(1, steps=steps(*[False] * MAX_ACTION_STEPS)))

    assert len(rows(panel)) == MAX_ACTION_STEPS
    assert not panel.detail.new_step.isVisibleTo(content(panel))


def test_a_closed_task_s_plan_is_read_only_text(panel: ActionsPanel) -> None:
    completed = {"status": ActionStatus.COMPLETED, "completed_at_utc": NOW}
    show(panel, ActionFilter.COMPLETED, action(1, steps=steps(True, False), **completed))
    panel.show_view(ActionFilter.COMPLETED)

    assert rows(panel) == []
    assert plan_texts(panel, 2) == ["Plan · 1 of 2 done", "✓ Step 1", "○ Step 2"]
    assert not panel.detail.new_step.isVisibleTo(content(panel))

    show(panel, ActionFilter.COMPLETED, action(2, **completed))
    assert not any(text.startswith("Plan") for text in detail_texts(panel))


def test_a_step_s_words_stay_plain_and_never_a_mnemonic(panel: ActionsPanel) -> None:
    words = "Fish & chips for <b>Sam</b> " + "x" * 120
    show(
        panel,
        ActionFilter.OPEN,
        action(1, steps=(ActionStep(step_id=7, position=0, text=words, done=False),)),
    )

    (row,) = rows(panel)
    assert isinstance(row.label, WrapLabel)
    assert row.label.text() == words
    assert row.label.textFormat() == Qt.TextFormat.PlainText
    assert row.box.text() == ""  # Its words are the label's, so "&" marks no shortcut.
    assert row.box.accessibleName() == words
    assert (row.box.toolTip(), row.label.toolTip()) == ("", "")


def test_a_click_on_a_box_or_its_words_asks_to_tick_it(qtbot: QtBot, panel: ActionsPanel) -> None:
    shown = action(1, steps=steps(False, True))
    show(panel, ActionFilter.OPEN, shown)
    activate(qtbot, panel)
    first, second = rows(panel)

    with qtbot.waitSignal(panel.step_toggled) as ticked:
        QTest.mouseClick(first.box, Qt.MouseButton.LeftButton)
    assert ticked.args == [shown, 1, True]
    assert first.box.isChecked()  # At once; the window saves it.

    with qtbot.waitSignal(panel.step_toggled) as unticked:
        QTest.mouseClick(second.label, Qt.MouseButton.LeftButton, pos=QPoint(2, 4))
    assert unticked.args == [shown, 2, False]
    assert not second.box.isChecked()


def test_return_asks_to_add_the_typed_step(qtbot: QtBot, panel: ActionsPanel) -> None:
    shown = action(1, steps=steps(False))
    show(panel, ActionFilter.OPEN, shown)
    activate(qtbot, panel)
    field = panel.detail.new_step

    with qtbot.assertNotEmitted(panel.step_added):
        field.setText("   ")
        QTest.keyClick(field, Qt.Key.Key_Return)  # Nothing to add.

    field.setText("  Call Sam  ")
    with qtbot.waitSignal(panel.step_added) as added:
        QTest.keyClick(field, Qt.Key.Key_Return)
    assert added.args == [shown, "Call Sam"]
    assert field.text() == "  Call Sam  "  # Kept until the step is saved.


def test_the_field_empties_only_for_the_step_just_added(panel: ActionsPanel) -> None:
    shown = action(1)
    show(panel, ActionFilter.OPEN, shown, action(2))
    field = panel.detail.new_step
    field.setText("Call Sam")

    panel.detail.clear_added_step(action(2).public_id, "Call Sam")
    assert field.text() == "Call Sam"  # Another action's.
    field.setText("Call Sam later")
    panel.detail.clear_added_step(shown.public_id, "Call Sam")
    assert field.text() == "Call Sam later"  # Typed since.
    field.setText(" Call Sam ")
    panel.detail.clear_added_step(shown.public_id, "Call Sam")
    assert field.text() == ""


def test_a_step_typed_for_one_action_stays_with_it(panel: ActionsPanel) -> None:
    first, second = action(1, steps=steps(False)), action(2)
    show(panel, ActionFilter.OPEN, first, second)
    panel.detail.new_step.setText("Call Sam")

    show(panel, ActionFilter.OPEN, first.model_copy(update={"revision": 2}), second)
    assert panel.detail.new_step.text() == "Call Sam"  # A refresh keeps it.

    panel.lists[ActionFilter.OPEN].setCurrentRow(1)
    assert panel.detail.new_step.text() == ""  # Another action shows.


def test_a_long_step_reaches_the_window_whole(qtbot: QtBot, panel: ActionsPanel) -> None:
    """No length limit cuts it short: the service refuses it with the reason, as the editor
    does, and the field keeps it."""
    shown = action(1)
    show(panel, ActionFilter.OPEN, shown)
    words = "x" * 620

    panel.detail.new_step.setText(words)
    with qtbot.waitSignal(panel.step_added) as added:
        panel.detail.new_step.returnPressed.emit()

    assert added.args == [shown, words]
    assert panel.detail.new_step.text() == words


def test_a_step_put_back_keeps_its_row(panel: ActionsPanel) -> None:
    show(panel, ActionFilter.OPEN, action(1, steps=steps(False, True)))
    first, second = rows(panel)
    first.box.setChecked(True)  # As a click does before the window saves it.

    panel.detail.put_back_step(1, True)
    panel.detail.put_back_step(99, True)  # Another action's step: nothing to put back.

    assert rows(panel) == [first, second]
    assert ticks(panel) == [False, True]


def test_the_same_steps_are_updated_in_place(panel: ActionsPanel) -> None:
    shown = action(1, steps=steps(False, False))
    show(panel, ActionFilter.OPEN, shown)
    before = rows(panel)

    ticked = shown.model_copy(update={"revision": 2, "steps": steps(True, False)})
    show(panel, ActionFilter.OPEN, ticked)

    assert rows(panel) == before  # The same rows: focus and Tab order stay put.
    assert ticks(panel) == [True, False]
    assert [row.label.property("tone") for row in before] == ["muted", None]
    assert plan_texts(panel, 2) == ["Plan · 1 of 2 done", "Step 1", "Step 2"]

    unticked = shown.model_copy(update={"revision": 3, "steps": steps(False, False)})
    show(panel, ActionFilter.OPEN, unticked)
    assert [row.label.property("tone") for row in before] == [None, None]


def test_skip_never_shows_as_a_window_of_its_own(panel: ActionsPanel) -> None:
    show(panel, ActionFilter.OPEN, action(1))
    pane = panel.detail
    assert not pane.skip_button.isWindow()

    pane.set_can_skip(True)

    assert not pane.skip_button.isWindow()
    assert pane.skip_button.isVisibleTo(pane)


def test_edits_in_a_row_read_as_one_activity_line(panel: ActionsPanel) -> None:
    def event(revision: int, kind: ActionActivityKind, minute: int) -> ActionActivity:
        return ActionActivity(
            revision=revision,
            kind=kind,
            occurred_at_utc=datetime(2026, 10, 3, 8, minute, tzinfo=UTC),
        )

    history = (
        event(1, ActionActivityKind.CREATED, 0),
        event(2, ActionActivityKind.EDITED, 1),
        event(3, ActionActivityKind.COMPLETED, 2),
        event(4, ActionActivityKind.REOPENED, 3),
        event(5, ActionActivityKind.EDITED, 4),
        event(6, ActionActivityKind.EDITED, 5),
        event(7, ActionActivityKind.EDITED, 6),
    )
    show(panel, ActionFilter.OPEN, action(1, activity=history, revision=7))

    entries = content(panel).findChildren(QLabel, "actionActivityEntry")
    assert [label.text() for label in entries] == [
        "Edited 3 times · Sat Oct 3, 08:06",
        "Reopened · Sat Oct 3, 08:03",
        "Completed · Sat Oct 3, 08:02",
        "Edited · Sat Oct 3, 08:01",
        "Created · Sat Oct 3, 08:00",
    ]


def test_a_box_keeps_the_focus_when_its_plan_is_shown_again(
    qtbot: QtBot, panel: ActionsPanel
) -> None:
    shown = action(1, steps=steps(False, False))
    show(panel, ActionFilter.OPEN, shown)
    activate(qtbot, panel)
    before = rows(panel)[1]
    before.box.setFocus()
    assert before.box.hasFocus()

    show(
        panel,
        ActionFilter.OPEN,
        shown.model_copy(update={"revision": 2, "steps": steps(False, True)}),
    )

    after = rows(panel)[1]
    assert after is before  # Updated in place.
    assert after.box.isChecked() and after.box.hasFocus()

    show(
        panel,
        ActionFilter.OPEN,
        shown.model_copy(update={"revision": 3, "steps": steps(False, True, False)}),
    )

    remade = rows(panel)[1]  # A step was added, so the rows are made anew.
    assert remade is not before
    assert remade.box.isChecked() and remade.box.hasFocus()
    QApplication.processEvents()  # The layout shows the new boxes at the next turn.
    assert remade.box.isVisible() and remade.box.hasFocus()


def test_tab_runs_from_the_footer_through_the_steps_to_the_field(
    qtbot: QtBot, qapp: QApplication, themed: None, panel: ActionsPanel
) -> None:
    # The app's own style, as Forewit always runs: under macOS's native style buttons and
    # check boxes are Tab stops only with Full Keyboard Access on.
    apply_theme(qapp)
    thread = ThreadActivity(
        new_messages=1,
        latest_at_utc=datetime(2026, 10, 3, 8, 30, tzinfo=UTC),
        latest_sender="Sam",
    )
    shown = action(1, steps=steps(False, True), thread=thread)
    show(panel, ActionFilter.OPEN, shown)
    activate(qtbot, panel)

    # As first shown, updated in place, and with the boxes made anew for an added step.
    plans = (steps(False, True), steps(True, True), steps(True, True, False))
    for revision, plan in enumerate(plans, start=1):
        show(
            panel, ActionFilter.OPEN, shown.model_copy(update={"revision": revision, "steps": plan})
        )
        QApplication.processEvents()  # The layout shows new boxes at the next turn.
        boxes = [row.box for row in rows(panel)]
        panel.delete_button.setFocus()
        visited: list[QWidget | None] = []
        for _ in range(len(boxes) + 2):
            panel.focusNextChild()
            visited.append(QApplication.focusWidget())
        assert visited == [*boxes, panel.detail.new_step, panel.seen_button]


# The window: each tick and each added step saved through the backend.


@pytest.fixture
def backend() -> FakeBackend:
    result = FakeBackend()
    result.actions = {ActionFilter.OPEN: (PARTY,)}
    result.owner_preferences = OwnerPreferences(revision=1, time_zone="UTC")
    return result


@pytest.fixture
def window(qtbot: QtBot, backend: FakeBackend) -> MainWindow:
    result = MainWindow(backend)
    result.zone = ZoneInfo("UTC")
    result.now = lambda: datetime(2026, 9, 28, 15, tzinfo=UTC)
    qtbot.addWidget(result)
    result.show()
    return result


def window_rows(window: MainWindow) -> list[StepRow]:
    return window.actions_panel.detail.step_rows()


async def test_ticking_saves_each_step_and_says_how_far_the_plan_is(
    window: MainWindow, backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    await window.initialize()
    refreshed = AsyncMock()
    monkeypatch.setattr(window, "_refresh_drafts", refreshed)
    monkeypatch.setattr(window, "_refresh_calendar", refreshed)

    window_rows(window)[0].box.click()
    await finish(window)

    assert backend.action_calls[-1] == ("set_step_done", PARTY.public_id, 1, 1, True)
    assert window.status.text() == "1 of 2 steps done: Plan the party."
    assert [row.box.isChecked() for row in window_rows(window)] == [True, False]  # As stored.
    assert window.undo_button.isHidden()  # Ticking it again undoes it.
    assert not refreshed.await_count  # Drafts and the calendar: no step changes them.

    window_rows(window)[1].box.click()
    await finish(window)

    assert backend.action_calls[-1] == ("set_step_done", PARTY.public_id, 2, 2, True)
    assert window.status.text() == "Every step done: Plan the party."
    selected = window.actions_panel.selected()
    assert selected is not None and selected.status is ActionStatus.OPEN  # Still to complete.

    window_rows(window)[0].box.click()
    await finish(window)

    assert backend.action_calls[-1] == ("set_step_done", PARTY.public_id, 3, 1, False)
    assert window.status.text() == "1 of 2 steps done: Plan the party."


async def test_a_tick_withdraws_an_older_undo(window: MainWindow, backend: FakeBackend) -> None:
    await window.initialize()
    window.actions_panel.complete_button.click()
    await finish(window)
    assert not window.undo_button.isHidden()

    window_rows(window)[0].box.click()
    await finish(window)

    assert window.undo_button.isHidden()  # It would undo a past revision.


async def test_a_conflicting_tick_says_why_and_puts_the_box_back(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    backend.action_fail = ActionConflictError("Reopen the task to change its plan.")

    window_rows(window)[0].box.click()
    await finish(window)

    assert window.status.text() == "Reopen the task to change its plan."
    assert ticks(window.actions_panel) == [False, False]


async def test_a_failed_tick_puts_the_box_back(window: MainWindow, backend: FakeBackend) -> None:
    await window.initialize()
    backend.action_fail = RuntimeError("disk full")

    window_rows(window)[0].box.click()
    await finish(window)

    assert backend.action_calls[-1][0] == "set_step_done"
    assert "steps done" not in window.status.text()
    assert ticks(window.actions_panel) == [False, False]


async def test_a_tick_while_another_operation_runs_says_so_and_saves_nothing(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    window.start(window._generate)
    await asyncio.sleep(0)
    busy = "Forewit is busy; try again in a moment."

    first = window_rows(window)[0]
    first.box.click()
    assert window.status.text() == busy
    assert ticks(window.actions_panel) == [False, False]  # Put back, in the same row.
    assert window_rows(window)[0] is first
    window.status.setText("")
    field = window.actions_panel.detail.new_step
    field.setText("Call the caterer")
    field.returnPressed.emit()
    assert window.status.text() == busy
    assert field.text() == "Call the caterer"

    window.cancel()
    await finish(window)
    assert not [call for call in backend.action_calls if call[0] in {"set_step_done", "add_step"}]


async def test_adding_a_step_saves_it_and_empties_the_field(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    field = window.actions_panel.detail.new_step
    field.setText("Call the caterer")

    QTest.keyClick(field, Qt.Key.Key_Return)
    await finish(window)

    assert backend.action_calls[-1] == ("add_step", PARTY.public_id, 1, "Call the caterer")
    assert field.text() == ""
    assert [row.label.text() for row in window_rows(window)] == [
        "Book the venue",
        "Send invites",
        "Call the caterer",
    ]
    assert window.status.text() == "Step added: Plan the party."
    assert window.undo_button.isHidden()


async def test_a_refused_step_stays_in_its_field_with_the_reason(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    backend.action_fail = ActionConflictError("An action has at most 30 steps.")
    field = window.actions_panel.detail.new_step
    field.setText("Call the caterer")

    QTest.keyClick(field, Qt.Key.Key_Return)
    await finish(window)

    assert window.status.text() == "An action has at most 30 steps."
    assert field.text() == "Call the caterer"


def test_the_status_counts_the_steps_done() -> None:
    assert steps_status(make_action(title="Pack", steps=steps(True, False, False))) == (
        "1 of 3 steps done: Pack."
    )
    assert steps_status(make_action(title="Pack", steps=steps(False))) == "0 of 1 step done: Pack."
    assert steps_status(make_action(title="Pack", steps=steps(True))) == "Every step done: Pack."
