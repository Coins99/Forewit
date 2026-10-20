"""Deadlines the owner sets, on the desktop (B06): the editor's Due by, the email's deadline,
the detail line, and a follow-up that would replace the owner's deadline."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QDate, QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import (
    ActionEdit,
    ActionFilter,
    DeadlineChange,
    DeadlineOrigin,
    StatedDeadline,
)
from mailbrief.domain.analysis import DeadlinePrecision, FollowUpKind, TargetReason
from mailbrief.domain.preferences import OwnerPreferences
from mailbrief.ui.action_editor import BAD_DEADLINE_TIME, ActionEditor
from mailbrief.ui.action_text import action_details
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.proposals_view import effect_text
from tests.factories import make_action, make_proposal
from tests.ui.test_workflow import FakeBackend

TODAY = date(2026, 9, 28)  # A Monday.
NOW = datetime(2026, 9, 28, 15, tzinfo=UTC)
TORONTO = ZoneInfo("America/Toronto")
FRIDAY: dict[str, object] = {
    "deadline_text": "by Friday",
    "deadline_precision": DeadlinePrecision.DATE,
    "deadline_date": date(2026, 10, 2),
    "deadline_timezone": "America/Toronto",
}
FROM_EMAIL = make_action(
    **FRIDAY,
    suggested_target_date=date(2026, 10, 1),
    target_reason=TargetReason.WORKING_DAY_BEFORE,
    target_date=date(2026, 10, 1),
)
STATED = StatedDeadline.model_validate(FRIDAY)
OWNER_MONDAY = make_action(
    deadline_text="2026-10-05",
    deadline_precision=DeadlinePrecision.DATE,
    deadline_date=date(2026, 10, 5),
    deadline_timezone="America/Toronto",
    deadline_origin=DeadlineOrigin.OWNER,
    stated_deadline=STATED,
    suggested_target_date=date(2026, 10, 2),
    target_reason=TargetReason.WORKING_DAY_BEFORE,
    target_date=date(2026, 10, 1),
)
CLEARED = make_action(stated_deadline=STATED, target_date=date(2026, 10, 1))


@pytest.fixture
def editor(qtbot: QtBot) -> Iterator[ActionEditor]:
    parent = QWidget()  # Held by this frame: qtbot keeps widgets only weakly.
    qtbot.addWidget(parent)
    yield ActionEditor(parent)


def save(editor: ActionEditor) -> None:
    button = editor.buttons.button(QDialogButtonBox.StandardButton.Save)
    assert button is not None
    button.click()


def changes(editor: ActionEditor) -> list[tuple[ActionEdit, DeadlineChange | None]]:
    """Each edit and deadline change the editor asks to save or create."""
    found: list[tuple[ActionEdit, DeadlineChange | None]] = []
    editor.save_requested.connect(
        lambda _action, edit, _steps, _repeat, deadline: found.append((edit, deadline))
    )
    editor.create_requested.connect(
        lambda edit, _steps, _rule, deadline: found.append((edit, deadline))
    )
    return found


def fields(editor: ActionEditor) -> tuple[bool, str, str]:
    return (
        editor.has_deadline.isChecked(),
        editor.deadline_date.date().toString("yyyy-MM-dd"),
        editor.deadline_time.currentText(),
    )


def target(editor: ActionEditor) -> date | None:
    picked = editor.target.date()
    if not editor.has_target.isChecked():
        return None
    return date(picked.year(), picked.month(), picked.day())


def test_an_unchanged_deadline_saves_no_change(editor: ActionEditor) -> None:
    sent = changes(editor)
    editor.edit(FROM_EMAIL, TODAY, TORONTO)

    save(editor)

    assert [deadline for _, deadline in sent] == [None]


def test_a_new_task_takes_a_deadline_and_a_target_one_working_day_before(
    editor: ActionEditor,
) -> None:
    sent = changes(editor)
    editor.new_task(TODAY, TORONTO)
    assert fields(editor) == (False, "2026-09-28", "Any time")
    assert not editor.deadline_date.isEnabled()
    assert editor.deadline.text() == ""
    editor.title.setText("Renew the passport")

    editor.has_deadline.setChecked(True)
    editor.deadline_date.setDate(QDate(2026, 10, 2))

    assert editor.deadline_date.isEnabled() and editor.deadline_time.isEnabled()
    assert editor.suggested.text() == "2026-10-01, one working day before the deadline"
    assert target(editor) == date(2026, 10, 1)  # It had none, and none was suggested.
    assert editor.has_unsaved_input()
    save(editor)
    ((edit, deadline),) = sent
    assert deadline == DeadlineChange.to(date(2026, 10, 2))
    assert edit.target_date == date(2026, 10, 1)


def test_a_time_is_chosen_from_the_list_or_typed(editor: ActionEditor) -> None:
    sent = changes(editor)
    editor.new_task(TODAY, TORONTO)
    editor.title.setText("File the claim")
    editor.has_deadline.setChecked(True)
    editor.deadline_date.setDate(QDate(2026, 10, 2))

    editor.deadline_time.setCurrentIndex(editor.deadline_time.findText("17:00"))
    save(editor)
    editor.keep_open("")
    editor.deadline_time.setEditText("9:05")
    save(editor)

    assert [deadline for _, deadline in sent] == [
        DeadlineChange.to(date(2026, 10, 2), time(17, 0)),
        DeadlineChange.to(date(2026, 10, 2), time(9, 5)),
    ]


@pytest.mark.parametrize("typed", ["5pm", "24:00", "17:5"])
def test_a_time_that_isn_t_one_is_refused_with_the_reason(editor: ActionEditor, typed: str) -> None:
    sent = changes(editor)
    editor.edit(FROM_EMAIL, TODAY, TORONTO)

    editor.deadline_time.setEditText(typed)
    save(editor)

    assert sent == []
    assert editor.status.text() == BAD_DEADLINE_TIME
    assert editor.title.isEnabled()  # Still open for a correction.


def test_moving_the_deadline_moves_a_target_that_was_the_suggestion(
    editor: ActionEditor,
) -> None:
    sent = changes(editor)
    editor.edit(FROM_EMAIL, TODAY, TORONTO)

    editor.deadline_date.setDate(QDate(2026, 10, 9))

    assert editor.suggested.text() == "2026-10-08, one working day before the deadline"
    assert target(editor) == date(2026, 10, 8)
    save(editor)
    ((edit, deadline),) = sent
    assert deadline == DeadlineChange.to(date(2026, 10, 9))
    assert edit.target_date == date(2026, 10, 8)


def test_a_target_the_owner_chose_stays_when_the_deadline_moves(editor: ActionEditor) -> None:
    editor.edit(FROM_EMAIL.model_copy(update={"target_date": date(2026, 9, 30)}), TODAY, TORONTO)

    editor.deadline_date.setDate(QDate(2026, 10, 9))

    assert target(editor) == date(2026, 9, 30)
    assert editor.suggested.text() == "2026-10-08, one working day before the deadline"


def test_unticking_clears_the_deadline_and_keeps_the_target(editor: ActionEditor) -> None:
    sent = changes(editor)
    editor.edit(FROM_EMAIL, TODAY, TORONTO)

    editor.has_deadline.setChecked(False)

    assert not editor.deadline_date.isEnabled() and not editor.deadline_time.isEnabled()
    assert editor.suggested.text() == "no target was suggested"
    assert target(editor) == date(2026, 10, 1)
    save(editor)
    assert [deadline for _, deadline in sent] == [DeadlineChange.cleared()]


def test_the_owner_s_deadline_offers_to_go_back_to_the_email_s(editor: ActionEditor) -> None:
    sent = changes(editor)
    editor.edit(OWNER_MONDAY, TODAY, TORONTO)
    assert fields(editor) == (True, "2026-10-05", "Any time")
    assert (
        editor.deadline.text() == "You set this deadline. The email said “by Friday” (Fri Oct 2)."
    )
    assert not editor.from_email.isHidden()

    editor.from_email.click()

    assert fields(editor) == (True, "2026-10-02", "Any time")
    assert editor.from_email.isHidden()
    assert editor.suggested.text() == "2026-10-01, one working day before the deadline"
    assert target(editor) == date(2026, 10, 1)  # The owner's own target stays.
    assert editor.has_unsaved_input()
    save(editor)
    assert [deadline for _, deadline in sent] == [DeadlineChange.from_email()]


def test_changing_the_deadline_after_going_back_sets_it_instead(editor: ActionEditor) -> None:
    sent = changes(editor)
    editor.edit(OWNER_MONDAY, TODAY, TORONTO)
    editor.from_email.click()

    editor.deadline_date.setDate(QDate(2026, 10, 6))

    assert not editor.from_email.isHidden()  # Offered again.
    save(editor)
    assert [deadline for _, deadline in sent] == [DeadlineChange.to(date(2026, 10, 6))]


def test_a_cleared_deadline_offers_the_email_s(editor: ActionEditor) -> None:
    sent = changes(editor)
    editor.edit(CLEARED, TODAY, TORONTO)
    assert fields(editor)[0] is False
    assert editor.deadline.text() == (
        "You cleared the deadline. The email said “by Friday” (Fri Oct 2)."
    )

    editor.from_email.click()

    assert fields(editor) == (True, "2026-10-02", "Any time")
    save(editor)
    assert [deadline for _, deadline in sent] == [DeadlineChange.from_email()]


def test_an_email_s_words_alone_are_offered_back_without_a_day(editor: ActionEditor) -> None:
    sent = changes(editor)
    soon = StatedDeadline(deadline_text="soon", deadline_precision=DeadlinePrecision.UNRESOLVED)
    owner = OWNER_MONDAY.model_copy(update={"stated_deadline": soon})
    editor.edit(owner, TODAY, TORONTO)
    assert editor.deadline.text() == "You set this deadline. The email said “soon”."

    editor.from_email.click()

    assert fields(editor)[0] is False
    assert editor.suggested.text() == "no target was suggested"
    save(editor)
    assert [deadline for _, deadline in sent] == [DeadlineChange.from_email()]


def test_a_deadline_the_owner_set_alone_has_no_note(editor: ActionEditor) -> None:
    editor.edit(OWNER_MONDAY.model_copy(update={"stated_deadline": None}), TODAY, TORONTO)

    assert editor.deadline.text() == ""
    assert editor.from_email.isHidden()


def test_deadline_fields_count_as_unsaved_input(editor: ActionEditor) -> None:
    editor.edit(FROM_EMAIL, TODAY, TORONTO)
    assert not editor.has_unsaved_input()

    editor.deadline_time.setEditText("16:30")
    assert editor.has_unsaved_input()

    editor.edit(FROM_EMAIL, TODAY, TORONTO)
    assert not editor.has_unsaved_input()


def test_the_wheel_leaves_an_unfocused_deadline_time_alone(
    editor: ActionEditor, qtbot: QtBot
) -> None:
    editor.edit(FROM_EMAIL, TODAY, TORONTO)
    editor.show()
    qtbot.waitExposed(editor)
    editor.title.setFocus()
    QTest.qWait(0)
    assert not editor.deadline_time.hasFocus()
    center = editor.deadline_time.rect().center()
    event = QWheelEvent(
        QPointF(center),
        QPointF(editor.deadline_time.mapToGlobal(center)),
        QPoint(0, 0),
        QPoint(0, -120),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )

    QApplication.sendEvent(editor.deadline_time, event)

    assert editor.deadline_time.currentText() == "Any time"


def test_a_new_task_inline_shows_its_deadline_with_more_options(
    editor: ActionEditor, qtbot: QtBot
) -> None:
    host = QWidget()
    qtbot.addWidget(host)
    editor.set_embedded(host, window=host)
    editor.new_task(TODAY, TORONTO)
    form = editor._form

    assert not form.isRowVisible(editor.deadline_row)
    editor.more_options.setChecked(True)
    assert form.isRowVisible(editor.deadline_row)
    assert not form.isRowVisible(editor.deadline_note)  # It has nothing to say.


def test_the_detail_says_who_set_the_deadline() -> None:
    owner = action_details(OWNER_MONDAY, today=TODAY, zone=TORONTO, now=NOW)
    email = action_details(FROM_EMAIL, today=TODAY, zone=TORONTO, now=NOW)

    assert (owner.due, owner.due_by_owner) == ("Mon Oct 5", True)
    assert (email.due, email.due_by_owner) == ("Fri Oct 2", False)


def test_a_new_deadline_over_the_owner_s_says_it_replaces_it() -> None:
    proposal = make_proposal(
        kind=FollowUpKind.NEW_DEADLINE,
        deadline_text="next Monday",
        deadline_precision=DeadlinePrecision.DATE,
        deadline_date=date(2026, 10, 5),
        deadline_timezone="America/Toronto",
        replaces_owner_deadline=True,
    )

    assert effect_text(proposal, TORONTO, today=TODAY) == "Replace your deadline with Mon Oct 5"
    email = proposal.model_copy(update={"replaces_owner_deadline": False})
    assert effect_text(email, TORONTO, today=TODAY) == "Set the deadline to Mon Oct 5"


@pytest.fixture
def backend() -> FakeBackend:
    result = FakeBackend()
    result.actions = {ActionFilter.OPEN: (OWNER_MONDAY,)}
    result.owner_preferences = OwnerPreferences(revision=1, time_zone="America/Toronto")
    return result


@pytest.fixture
def window(qtbot: QtBot, backend: FakeBackend) -> MainWindow:
    result = MainWindow(backend)
    result.now = lambda: NOW
    qtbot.addWidget(result)
    result.show()
    return result


async def test_the_window_shows_and_saves_the_owner_s_deadline(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    assert window.actions_panel.selected() == OWNER_MONDAY
    deadline = window.actions_panel.detail.findChild(QLabel, "actionDeadline")
    assert deadline is not None and deadline.text() == "Due Mon Oct 5 · Set by you"

    window.actions_panel.edit_button.click()
    editor = window.action_editor
    assert (
        editor.deadline.text() == "You set this deadline. The email said “by Friday” (Fri Oct 2)."
    )
    editor.deadline_time.setCurrentIndex(editor.deadline_time.findText("12:00"))
    save(editor)
    assert window.task is not None
    await window.task

    assert backend.deadlines == [DeadlineChange.to(date(2026, 10, 5), time(12, 0))]
    assert window.status.text().startswith("Saved: ")


async def test_a_new_task_is_dated_in_the_window_s_zone(window: MainWindow) -> None:
    await window.initialize()

    window._request_new_task()

    assert window.action_editor._zone == window.zone
    assert window.action_editor.isVisible()
