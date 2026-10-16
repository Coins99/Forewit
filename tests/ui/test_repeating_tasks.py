"""Repeating tasks on the desktop (B05): the editor's Repeats, the detail and row text, Skip."""

from collections.abc import Iterator
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QDate
from PySide6.QtWidgets import QDialogButtonBox, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import ActionFilter, ActionRepeat, ActionStatus
from mailbrief.domain.preferences import OwnerPreferences
from mailbrief.domain.recurrence import (
    ENDS_FIRST,
    WORKING_WEEKDAYS,
    RepeatEdit,
    RepeatFrequency,
    RepeatRule,
)
from mailbrief.ui.action_detail import ActionDetailPane
from mailbrief.ui.action_editor import NO_WEEKDAY, ActionEditor
from mailbrief.ui.action_text import repeat_short, repeat_text, rule_text
from mailbrief.ui.actions_view import action_row, can_skip, describe
from mailbrief.ui.main_window import MainWindow
from tests.factories import make_action
from tests.ui.test_workflow import FakeBackend

TODAY = date(2026, 10, 15)  # A Thursday.
NOW = datetime(2026, 10, 15, 15, tzinfo=UTC)
UTC_ZONE = ZoneInfo("UTC")
SERIES = "6c2e1e1f-4a5d-4b4f-9a6f-1d7c9a8b3e21"
DAILY = RepeatRule(frequency=RepeatFrequency.DAILY)
MONDAY_WEDNESDAY = RepeatRule(frequency=RepeatFrequency.WEEKLY, weekdays=(0, 2))


def repeating(rule: RepeatRule = DAILY, **fields: object) -> ActionRepeat:
    values: dict[str, object] = {
        "series_public_id": SERIES,
        "series_revision": 3,
        "rule": rule,
        "occurrence_date": TODAY,
    }
    values.update(fields)
    return ActionRepeat.model_validate(values)


WATER = make_action(title="Water plants", target_date=TODAY, repeat=repeating(), created_at_utc=NOW)


@pytest.fixture
def editor(qtbot: QtBot) -> Iterator[ActionEditor]:
    parent = QWidget()
    qtbot.addWidget(parent)
    result = ActionEditor(parent)
    yield result


def save(editor: ActionEditor) -> None:
    button = editor.buttons.button(QDialogButtonBox.StandardButton.Save)
    assert button is not None
    button.click()


def test_rule_text_reads_like_a_sentence() -> None:
    assert rule_text(DAILY) == "every day"
    assert rule_text(MONDAY_WEDNESDAY) == "every Mon and Wed"
    weekly = RepeatRule(frequency=RepeatFrequency.WEEKLY, weekdays=(0, 2, 4))
    assert rule_text(weekly) == "every Mon, Wed and Fri"
    assert rule_text(weekly.model_copy(update={"weekdays": (5,)})) == "every Saturday"
    assert rule_text(weekly.model_copy(update={"weekdays": WORKING_WEEKDAYS})) == "every weekday"
    assert rule_text(weekly.model_copy(update={"weekdays": tuple(range(7))})) == "every day"
    monthly = RepeatRule(frequency=RepeatFrequency.MONTHLY_DAY, month_day=1)
    assert rule_text(monthly) == "on the 1st of each month"
    for day, text in ((2, "2nd"), (3, "3rd"), (11, "11th"), (22, "22nd"), (28, "28th")):
        assert rule_text(monthly.model_copy(update={"month_day": day})) == (
            f"on the {text} of each month"
        )
    assert rule_text(monthly.model_copy(update={"month_day": 31})) == (
        "on the 31st of each month (not in shorter months)"
    )
    last = RepeatRule(frequency=RepeatFrequency.MONTHLY_LAST)
    assert rule_text(last) == "on the last day of each month"
    ending = DAILY.model_copy(update={"end_date": date(2026, 10, 30)})
    assert repeat_text(ending, TODAY) == "Repeats every day until Fri Oct 30"


def test_rows_and_descriptions_name_the_repeat() -> None:
    assert repeat_short(WATER) == "Repeats daily"
    weekly = WATER.model_copy(update={"repeat": repeating(MONDAY_WEDNESDAY)})
    assert repeat_short(weekly) == "Repeats weekly"
    monthly = WATER.model_copy(
        update={"repeat": repeating(RepeatRule(frequency=RepeatFrequency.MONTHLY_LAST))}
    )
    assert repeat_short(monthly) == "Repeats monthly"
    stopped = WATER.model_copy(update={"repeat": repeating(ended=True)})
    assert repeat_short(stopped) is None
    row = action_row(WATER, today=TODAY, zone=UTC_ZONE, now=NOW)
    assert row.meta == "Target Thu Oct 15 · Repeats daily"
    assert describe(WATER, today=TODAY, zone=UTC_ZONE, now=NOW) == (
        "Water plants — Target Thu Oct 15 · repeats daily"
    )


def test_skip_applies_only_while_it_repeats() -> None:
    assert can_skip(WATER)
    assert not can_skip(make_action())
    stopped = WATER.model_copy(update={"repeat": repeating(ended=True)})
    assert not can_skip(stopped)
    done = WATER.model_copy(update={"status": ActionStatus.COMPLETED, "completed_at_utc": NOW})
    assert not can_skip(done)


def test_the_detail_says_how_it_repeats_and_what_it_follows(qtbot: QtBot) -> None:
    pane = ActionDetailPane()
    qtbot.addWidget(pane)
    pane.resize(560, 600)
    following = WATER.model_copy(
        update={
            "repeat": repeating(
                MONDAY_WEDNESDAY,
                previous_date=date(2026, 10, 14),
                previous_status=ActionStatus.SKIPPED,
            )
        }
    )
    pane.show_action(following, today=TODAY, zone=UTC_ZONE, now=NOW)
    texts = {
        label.objectName(): label.text()
        for label in pane.findChildren(QWidget)
        if hasattr(label, "text")
    }
    assert texts["actionRepeat"] == "Repeats every Mon and Wed"
    assert texts["actionPrevious"] == "Follows Wed Oct 14 · Skipped"

    pane.set_can_skip(True)
    pane.show()
    assert pane.skip_button.isVisibleTo(pane)
    assert pane.skip_button in pane.buttons()
    pane.set_can_skip(False)
    assert not pane.skip_button.isVisibleTo(pane)
    assert pane.skip_button in pane.all_buttons()


def test_a_new_task_can_repeat_weekly_from_its_own_weekday(editor: ActionEditor) -> None:
    created: list[object] = []
    editor.create_requested.connect(lambda _edit, _steps, rule: created.append(rule))
    editor.new_task(TODAY)
    assert editor.repeat.currentText() == "Doesn't repeat"
    assert editor.later_too.isHidden()
    editor.title.setText("Bins out")
    editor.has_target.setChecked(True)
    editor.target.setDate(QDate(2026, 10, 19))  # A Monday.

    editor.repeat.setCurrentIndex(2)  # Every week on…

    assert [box.isChecked() for box in editor.weekday_boxes] == [True] + [False] * 6
    assert not editor.weekdays.isHidden()
    assert editor.has_unsaved_input()
    editor.weekday_boxes[3].setChecked(True)
    save(editor)
    assert created == [RepeatRule(frequency=RepeatFrequency.WEEKLY, weekdays=(0, 3))]


def test_a_weekly_repeat_needs_a_weekday_and_an_end_after_its_date(
    editor: ActionEditor,
) -> None:
    created: list[object] = []
    editor.create_requested.connect(lambda _edit, _steps, rule: created.append(rule))
    editor.new_task(TODAY)
    editor.title.setText("Bins out")
    editor.repeat.setCurrentIndex(2)
    for box in editor.weekday_boxes:
        box.setChecked(False)
    save(editor)
    assert editor.status.text() == NO_WEEKDAY

    editor.repeat.setCurrentIndex(3)  # Every month on day…
    assert not editor.month_day.isHidden()
    assert editor.month_day.value() == 15
    editor.repeat_ends.setChecked(True)
    editor.repeat_end.setDate(QDate(2026, 10, 14))
    save(editor)
    assert editor.status.text() == ENDS_FIRST
    # With no target, the task takes the rule's first date from today, as the service
    # dates it: the 1st is November 1, after an end on October 20.
    editor.month_day.setValue(1)
    editor.repeat_end.setDate(QDate(2026, 10, 20))
    editor.status.clear()
    save(editor)
    assert editor.status.text() == ENDS_FIRST
    assert created == []
    editor.month_day.setValue(15)

    editor.repeat_end.setDate(QDate(2027, 1, 15))
    save(editor)
    assert created == [
        RepeatRule(frequency=RepeatFrequency.MONTHLY_DAY, month_day=15, end_date=date(2027, 1, 15))
    ]


def test_editing_a_repeating_task_sends_its_series_revision(editor: ActionEditor) -> None:
    sent: list[object] = []
    editor.save_requested.connect(lambda _action, _edit, _steps, repeat: sent.append(repeat))
    editor.edit(WATER, TODAY)
    assert editor.repeat.currentText() == "Every day"
    assert not editor.later_too.isHidden()
    assert editor.later_too.isChecked()
    editor.later_too.setChecked(False)
    save(editor)
    assert sent == [RepeatEdit(rule=DAILY, series_revision=3, later_too=False)]

    sent.clear()
    editor.edit(WATER, TODAY)
    editor.repeat.setCurrentIndex(0)  # Doesn't repeat: stop.
    assert editor.later_too.isHidden()
    save(editor)
    assert sent == [RepeatEdit(rule=None, series_revision=3, later_too=False)]


def test_a_closed_or_stopped_task_shows_no_live_repeat(editor: ActionEditor) -> None:
    sent: list[object] = []
    editor.save_requested.connect(lambda _action, _edit, _steps, repeat: sent.append(repeat))
    done = WATER.model_copy(update={"status": ActionStatus.COMPLETED, "completed_at_utc": NOW})
    editor.edit(done, TODAY)
    assert not editor.repeat.isEnabled()
    assert editor.later_too.isHidden()
    save(editor)
    assert sent == [None]  # A closed task's repeat can't change.

    stopped = WATER.model_copy(update={"repeat": repeating(ended=True)})
    editor.edit(stopped, TODAY)
    assert editor.repeat.currentText() == "Doesn't repeat"
    assert editor.repeat.isEnabled()


@pytest.fixture
def backend() -> FakeBackend:
    result = FakeBackend()
    result.actions = {ActionFilter.OPEN: (WATER,)}
    result.owner_preferences = OwnerPreferences(revision=1, time_zone="UTC")
    # What the service returns: the closed occurrence, naming the one its close made.
    result.closed_repeat = repeating(next_date=date(2026, 10, 16))
    return result


@pytest.fixture
def window(qtbot: QtBot, backend: FakeBackend) -> MainWindow:
    result = MainWindow(backend)
    result.zone = UTC_ZONE
    result.now = lambda: NOW
    qtbot.addWidget(result)
    result.show()
    return result


async def test_skipping_then_undoing(window: MainWindow, backend: FakeBackend) -> None:
    await window.initialize()
    panel = window.actions_panel
    assert panel.selected() == WATER
    assert panel.skip_button in panel.detail.buttons()  # The Actions page needn't be shown.

    panel.skip_button.click()
    assert window.task is not None
    await window.task

    assert backend.action_calls == [("skip_action", WATER.public_id, 1)]
    assert window.status.text() == "Skipped: Approve the proposal. Next Fri Oct 16."
    assert window.undo_button.text() == "&Undo skip"
    window.undo_button.click()
    assert window.task is not None
    await window.task
    assert backend.action_calls[-1] == ("undo_action_status", WATER.public_id, 2)


async def test_completing_says_when_it_comes_back(window: MainWindow, backend: FakeBackend) -> None:
    await window.initialize()
    window.actions_panel.complete_button.click()
    assert window.task is not None
    await window.task
    assert window.status.text() == "Completed: Approve the proposal. Next Fri Oct 16."


async def test_the_status_names_no_next_date_when_none_was_made(
    window: MainWindow, backend: FakeBackend
) -> None:
    backend.closed_repeat = repeating()  # Its next date already had an occurrence.
    await window.initialize()
    window.actions_panel.complete_button.click()
    assert window.task is not None
    await window.task
    assert window.status.text() == "Completed: Approve the proposal."
