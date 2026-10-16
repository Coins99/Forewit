"""Plan with AI on the Calendar page (ADR 0018, phase 3b): preview and consent, review,
apply and Undo, all in the calendar's own words."""

import asyncio
from collections.abc import Sequence
from datetime import UTC, date, timedelta
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget
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
from mailbrief.domain.google_calendar import CalendarSyncResult
from mailbrief.ports.errors import AuthenticationRequiredError
from mailbrief.services.events import EventConflictError
from mailbrief.ui.diagnostics import error_guidance
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.planning_dialog import (
    NO_INSTRUCTION,
    READING,
    TOO_LONG,
    PlanningDialog,
    proposal_text,
)
from mailbrief.ui.theme import ThemeMode, apply_theme
from tests.ui.test_calendar import MONDAY, NOW, TODAY, TORONTO, stored, timed, whole_days
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
    result.show()
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


async def asking(window: MainWindow, backend: FakeBackend) -> None:
    """Let the request start, until Groq is being asked."""
    for _ in range(50):
        if calls(backend, "generate") and window.planning_dialog.pages.currentIndex() == 2:
            return
        await asyncio.sleep(0)
    raise AssertionError("the request never started")


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
    assert item.checkState() == Qt.CheckState.Unchecked  # The delete waits for a tick.
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


def test_deletions_start_unticked_and_overlaps_follow_the_ticks(qtbot: QtBot) -> None:
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = PlanningDialog(parent)
    asked: list[tuple[bool, ...]] = []

    def overlaps(changes: Sequence[EventChange], ticked: Sequence[bool]) -> list[tuple[str, ...]]:
        asked.append(tuple(ticked))
        return [("Trip",) if row == 0 and not ticked[2] else () for row in range(len(changes))]

    dialog.show_proposals(proposals(), TORONTO, TODAY, overlaps)
    items = [dialog.list.item(row) for row in range(3)]
    assert all(item is not None for item in items)
    first, _, delete = items
    assert first is not None and delete is not None
    assert [item.checkState() for item in items if item is not None] == [
        Qt.CheckState.Checked,
        Qt.CheckState.Checked,
        Qt.CheckState.Unchecked,
    ]
    assert dialog.list.wordWrap()  # Long reasons wrap rather than scroll sideways.
    assert "Overlaps: Team sync (Work)" in first.text()  # As Groq's answer was checked.
    delete.setCheckState(Qt.CheckState.Checked)
    assert asked == [(True, True, True)] and "Overlaps" not in first.text()
    delete.setCheckState(Qt.CheckState.Unchecked)
    assert asked[-1] == (True, True, False) and first.text().endswith("Overlaps: Trip")
    first.setText(first.text())  # A row's own text changing asks nothing.
    assert len(asked) == 2


def test_long_rows_wrap_inside_the_list(qtbot: QtBot, qapp: QApplication, themed: None) -> None:
    apply_theme(qapp, ThemeMode.DARK)  # The theme pads rows, which once widened them.
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = PlanningDialog(parent)
    first = (
        proposals()
        .proposals[0]
        .model_copy(
            update={
                "reason": "Moves the dentist to Friday morning, the only free hour before your "
                "10:30 call, and keeps it an hour long so nothing else has to move this week. "
                "Thursday was full with the offsite, and Monday's slot clashes with the school "
                "run, so Friday is the one morning left before the deadline on the deck.",
                "overlaps": ("Team sync with the regional finance leads (Work)", "Budget prep"),
            }
        )
    )
    dialog.show_proposals(CalendarPlan(proposals=(first,)), TORONTO, TODAY)
    dialog.resize(560, 420)
    dialog.show()
    qtbot.waitExposed(dialog)
    item = dialog.list.item(0)
    assert item is not None
    row = dialog.list.visualItemRect(item)
    assert dialog.list.horizontalScrollBar().maximum() == 0  # Nothing to scroll sideways.
    assert row.width() <= dialog.list.viewport().width()
    assert row.height() >= 6 * dialog.list.fontMetrics().lineSpacing()  # Every wrapped line.


async def test_the_instruction_stays_until_changes_are_applied(
    window: MainWindow, backend: FakeBackend
) -> None:
    backend.planning_outcome = PlanningOutcome(status=PlanningStatus.PROPOSED, plan=proposals())
    await window.initialize()
    dialog = window.planning_dialog
    await ask(window, "Move the dentist")
    dialog.discard_button.click()
    window.calendar_panel.plan_button.click()  # Ask again, perhaps with a word changed.
    assert dialog.instruction.toPlainText() == "Move the dentist"
    assert dialog.instruction.textCursor().hasSelection()  # Typing replaces it.
    await ask(window, "Move the dentist")
    dialog.apply_button.click()
    await finish(window)
    window.calendar_panel.plan_button.click()
    assert dialog.instruction.toPlainText() == ""


async def test_google_is_read_first_when_its_saved_copy_misses_the_four_weeks(
    window: MainWindow, backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    backend.calendar_permission = True
    backend.calendar_now = NOW.astimezone(UTC)
    await window.initialize()
    dialog = window.planning_dialog
    before = len(backend.calendar_calls)

    async def preview(*, google: bool) -> None:
        window.calendar_panel.plan_button.click()
        dialog.instruction.setPlainText("Find two hours for the deck")
        dialog.google_times.setChecked(google)
        dialog.preview_button.click()
        await finish(window)

    await preview(google=False)  # Without Google times, nothing is read.
    assert backend.calendar_calls[before:] == []
    dialog.reject()
    released, sync = asyncio.Event(), backend.sync_calendar

    async def slow(around: date, zone: ZoneInfo) -> CalendarSyncResult:
        await released.wait()
        return await sync(around, zone)

    monkeypatch.setattr(backend, "sync_calendar", slow)
    window.calendar_panel.plan_button.click()
    dialog.google_times.setChecked(True)
    dialog.preview_button.click()
    for _ in range(50):
        await asyncio.sleep(0)
    assert dialog.reading.isVisibleTo(dialog) and dialog.reading.text() == READING
    assert not calls(backend, "prepare")[1:]  # The preview waits for Google.
    released.set()
    await finish(window)
    assert backend.calendar_calls[before:] == [("sync_calendar", MONDAY)]
    assert len(calls(backend, "prepare")) == 2 and not dialog.reading.isVisibleTo(dialog)
    dialog.reject()
    await preview(google=True)  # Now saved for all four weeks, and fresh.
    assert backend.calendar_calls[before:] == [("sync_calendar", MONDAY)]

    dialog.reject()
    backend.google_coverage = None  # Nothing saved now, as the page reads it.
    await window._refresh_calendar()
    backend.calendar_fail = AuthenticationRequiredError("revoked")
    await preview(google=True)
    assert dialog.status.text().startswith("Google rejected the sign-in.")
    assert len(calls(backend, "prepare")) == 3  # Nothing more was prepared.


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


async def test_a_problem_shows_above_the_step_and_close_leaves(
    window: MainWindow, backend: FakeBackend
) -> None:
    backend.planning_first_use = False
    backend.planning_outcome = PlanningOutcome(
        status=PlanningStatus.FAILED, error_code="AI_NETWORK_BLOCKED"
    )
    await window.initialize()
    dialog = window.planning_dialog
    window.calendar_panel.plan_button.click()
    assert not dialog.status.isVisibleTo(dialog)  # Nothing to say yet.
    assert dialog.close_button.isVisibleTo(dialog)
    await ask(window)
    assert dialog.pages.currentIndex() == 0
    assert dialog.status.isVisibleTo(dialog)
    assert dialog.status.text() == error_guidance("AI_NETWORK_BLOCKED")
    assert dialog.status.property("tone") == "warning"
    layout = dialog.layout()
    assert layout is not None
    assert layout.indexOf(dialog.status) < layout.indexOf(dialog.pages)  # Above the step.
    dialog.close_button.click()
    assert not dialog.isVisible()


async def test_close_shows_on_the_first_two_steps_only(
    window: MainWindow, backend: FakeBackend
) -> None:
    backend.planning_outcome = PlanningOutcome(status=PlanningStatus.PROPOSED, plan=proposals())
    backend.planning_hold = asyncio.Event()
    await window.initialize()
    dialog = window.planning_dialog
    window.calendar_panel.plan_button.click()
    dialog.instruction.setPlainText("Move the dentist to Friday at 10")
    dialog.preview_button.click()
    await finish(window)
    assert dialog.pages.currentIndex() == 1 and dialog.close_button.isVisibleTo(dialog)
    dialog.consent.setChecked(True)
    dialog.ask_button.click()
    await asking(window, backend)
    assert not dialog.close_button.isVisibleTo(dialog)  # Cancel request is there instead.
    backend.planning_hold.set()
    await finish(window)
    assert dialog.pages.currentIndex() == 3 and not dialog.close_button.isVisibleTo(dialog)


async def test_closing_while_asking_stops_the_request(
    window: MainWindow, backend: FakeBackend
) -> None:
    backend.planning_first_use = False
    backend.planning_outcome = PlanningOutcome(status=PlanningStatus.PROPOSED, plan=proposals())
    backend.planning_hold = asyncio.Event()
    await window.initialize()
    dialog = window.planning_dialog
    window.calendar_panel.plan_button.click()
    dialog.instruction.setPlainText("Move the dentist to Friday at 10")
    dialog.preview_button.click()
    await finish(window)
    dialog.ask_button.click()
    await asking(window, backend)
    dialog.reject()  # Esc, as well as Close and Discard.
    try:
        for _ in range(50):
            if calls(backend, "cancelled"):
                break
            await asyncio.sleep(0)
        assert calls(backend, "cancelled") and not dialog.isVisible()
    finally:
        backend.planning_hold.set()  # A request that wasn't stopped mustn't hang the test.
    await finish(window)
    assert not calls(backend, "apply")
