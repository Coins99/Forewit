"""Plan with AI on the Calendar page (ADR 0018, phase 3b): preview and consent, review,
apply and Undo, all in the calendar's own words."""

from datetime import timedelta

import pytest
from PySide6.QtCore import Qt
from pytestqt.qtbot import QtBot

from mailbrief.domain.calendar_planning import (
    CalendarPlan,
    PlanningOptions,
    PlanningOutcome,
    PlanningPart,
    PlanningStatus,
    ProposedChange,
)
from mailbrief.domain.events import ChangeKind, EventChange
from mailbrief.services.events import EventConflictError
from mailbrief.ui.diagnostics import error_guidance
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.planning_dialog import NO_INSTRUCTION, TOO_LONG, proposal_text
from tests.ui.test_calendar import NOW, TODAY, TORONTO, stored, timed, whole_days
from tests.ui.test_workflow import FakeBackend


def proposals() -> CalendarPlan:
    dentist = stored(timed(TODAY, 11, 12, "Dentist"), 1)
    trip = stored(whole_days(TODAY + timedelta(days=6), TODAY + timedelta(days=7), "Trip"), 2)
    return CalendarPlan(
        proposals=(
            ProposedChange(
                change=EventChange(
                    kind=ChangeKind.UPDATE,
                    event=dentist,
                    edit=timed(TODAY + timedelta(days=2), 10, 11, "Dentist"),
                ),
                reason="Moves it to Friday.",
                overlaps=("Team sync (Work)",),
            ),
            ProposedChange(
                change=EventChange(
                    kind=ChangeKind.CREATE,
                    edit=timed(TODAY + timedelta(days=1), 9, 11, "Deck work"),
                )
            ),
            ProposedChange(change=EventChange(kind=ChangeKind.DELETE, event=trip)),
        ),
        message="Done.",
        ignored=1,
    )


def test_proposals_read_as_plain_sentences() -> None:
    plan = proposals()
    texts = [proposal_text(p, TORONTO, TODAY) for p in plan.proposals]
    assert texts == [
        "Change · Dentist · Wed Oct 14 11:00–12:00 → Fri Oct 16 10:00–11:00\n"
        "Moves it to Friday.\nOverlaps: Team sync (Work)",
        "Add · Thu Oct 15 09:00–11:00 · Deck work",
        "Delete · Tue Oct 20 – Wed Oct 21 · Trip",
    ]
    renamed = ProposedChange(
        change=EventChange(
            kind=ChangeKind.UPDATE,
            event=stored(timed(TODAY, 11, 12, "Dentist"), 3),
            edit=timed(TODAY, 11, 12, "Dentist (Dr Lee)"),
        )
    )
    assert proposal_text(renamed, TORONTO, TODAY) == (
        "Change · Dentist → Dentist (Dr Lee) · Wed Oct 14 11:00–12:00"
    )
    overnight = ProposedChange(
        change=EventChange(
            kind=ChangeKind.CREATE,
            edit=timed(TODAY, 22, 23, "Flight").model_copy(
                update={"ends_at_utc": timed(TODAY + timedelta(days=1), 1, 2, "x").starts_at_utc}
            ),
        )
    )
    assert proposal_text(overnight, TORONTO, TODAY) == (
        "Add · Wed Oct 14 22:00 – Thu Oct 15 01:00 · Flight"
    )


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def window(qtbot: QtBot, backend: FakeBackend) -> MainWindow:
    result = MainWindow(backend)
    result.now = lambda: NOW
    qtbot.addWidget(result)
    return result


async def finish(window: MainWindow) -> None:
    assert window.task is not None
    await window.task


def calls(backend: FakeBackend, name: str) -> list[tuple[object, ...]]:
    return [call for call in backend.planning_calls if call[0] == name]


async def ask(window: MainWindow, instruction: str = "Move the dentist to Friday at 10") -> None:
    dialog = window.planning_dialog
    window.calendar_panel.plan_button.click()
    dialog.instruction.setPlainText(instruction)
    dialog.preview_button.click()
    await finish(window)
    if dialog.consent.isVisibleTo(dialog):
        dialog.consent.setChecked(True)
    dialog.ask_button.click()
    await finish(window)


async def test_preview_consent_review_apply_and_undo(
    window: MainWindow, backend: FakeBackend
) -> None:
    backend.planning_outcome = PlanningOutcome(status=PlanningStatus.PROPOSED, plan=proposals())
    await window.initialize()
    dialog = window.planning_dialog
    window.calendar_panel.plan_button.click()
    assert dialog.isVisible() and dialog.pages.currentIndex() == 0
    dialog.preview_button.click()
    assert dialog.status.text() == NO_INSTRUCTION and not backend.planning_calls

    dialog.instruction.setPlainText("Move the dentist to Friday at 10")
    dialog.open_actions.setChecked(True)
    dialog.preview_button.click()
    await finish(window)
    options = calls(backend, "prepare")[0][1]
    assert isinstance(options, PlanningOptions)
    assert options.parts == frozenset({PlanningPart.ACTIONS})
    assert dialog.pages.currentIndex() == 1
    assert "test-model" in dialog.preview_heading.text()
    assert "Your 0 events" in dialog.preview_lines.text()
    assert not dialog.ask_button.isEnabled()  # First use needs the ticked box.
    dialog.consent.setChecked(True)
    dialog.ask_button.click()
    await finish(window)

    assert dialog.pages.currentIndex() == 3 and dialog.list.count() == 3
    assert dialog.message.text() == (
        "Done. 1 suggestion was left out because MailBrief couldn't use it."
    )
    item = dialog.list.item(2)
    assert item is not None
    item.setCheckState(Qt.CheckState.Unchecked)  # Keep the trip.
    dialog.apply_button.click()
    await finish(window)
    applied = calls(backend, "apply")[0][1]
    assert isinstance(applied, tuple)
    kinds = [change.kind for change in applied if isinstance(change, EventChange)]
    assert kinds == [ChangeKind.UPDATE, ChangeKind.CREATE]
    assert not dialog.isVisible()
    assert window.status.text() == "Applied 2 changes from Groq."
    assert window.undo_button.text() == "&Undo AI changes"
    window.undo_button.click()
    await finish(window)
    assert len(backend.undone) == 1


async def test_google_parts_are_offered_only_when_calendar_is_allowed(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    dialog = window.planning_dialog
    window.calendar_panel.plan_button.click()
    assert not dialog.google_times.isEnabled()
    dialog.reject()
    window._calendar_allowed = True
    window.calendar_panel.plan_button.click()
    assert dialog.google_times.isEnabled() and not dialog.google_titles.isEnabled()
    dialog.google_times.setChecked(True)
    assert dialog.google_titles.isEnabled()
    dialog.google_titles.setChecked(True)
    dialog.instruction.setPlainText("Find me a free hour on Friday")
    options = dialog.options()
    assert not isinstance(options, str)
    assert options.parts == frozenset({PlanningPart.GOOGLE_BUSY, PlanningPart.GOOGLE_TITLES})
    dialog.google_times.setChecked(False)
    assert not dialog.google_titles.isChecked()
    dialog.instruction.setPlainText("x" * 1_001)
    assert dialog.options() == TOO_LONG


@pytest.mark.parametrize(
    ("code", "message"),
    [
        (
            "AI_INVALID_OUTPUT",
            "Groq's answer couldn't be used. Your calendar is unchanged; try again.",
        ),
        ("AI_REFUSED", "Groq declined this request. Your calendar is unchanged."),
        (
            "AI_OUTPUT_INCOMPLETE",
            "Groq's answer was cut off. Your calendar is unchanged; try again.",
        ),
        ("AI_KEY_MISSING", error_guidance("AI_KEY_MISSING")),
    ],
)
async def test_failures_say_the_calendar_is_unchanged(
    window: MainWindow, backend: FakeBackend, code: str, message: str
) -> None:
    backend.planning_first_use = False
    backend.planning_outcome = PlanningOutcome(status=PlanningStatus.FAILED, error_code=code)
    await window.initialize()
    await ask(window)
    dialog = window.planning_dialog
    assert dialog.pages.currentIndex() == 0 and dialog.status.text() == message
    assert not calls(backend, "apply")


async def test_nothing_to_apply_and_a_stale_calendar(
    window: MainWindow, backend: FakeBackend
) -> None:
    backend.planning_first_use = False
    await window.initialize()
    await ask(window, "Tidy up next week")
    dialog = window.planning_dialog
    assert dialog.message.text() == "Groq suggested no changes."
    assert not dialog.apply_button.isEnabled()
    dialog.reject()

    backend.planning_outcome = PlanningOutcome(status=PlanningStatus.PROPOSED, plan=proposals())
    backend.apply_fail = EventConflictError("stale")
    await ask(window)
    dialog.apply_button.click()
    await finish(window)
    assert dialog.isVisible() and dialog.pages.currentIndex() == 3
    assert dialog.status.text().startswith("Your calendar changed since you asked")
    assert window.undo_button.isHidden() or "AI" not in window.undo_button.text()


async def test_declining_and_failing_to_prepare(window: MainWindow, backend: FakeBackend) -> None:
    backend.planning_first_use = False
    backend.planning_outcome = PlanningOutcome(status=PlanningStatus.DECLINED)
    await window.initialize()
    await ask(window)
    assert window.planning_dialog.status.text() == "Nothing was sent. Your calendar is unchanged."
    window.planning_dialog.reject()
    backend.planning_fail = RuntimeError("private")
    window.calendar_panel.plan_button.click()
    window.planning_dialog.instruction.setPlainText("x")
    window.planning_dialog.preview_button.click()
    await finish(window)
    assert window.planning_dialog.status.text() == (
        "Planning with AI failed. Your calendar is unchanged; try again."
    )
