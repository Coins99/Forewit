"""Break down with AI on the Tasks page (ADR 0033): when it is offered, the preview and
consent, the review, Apply and Undo, and what closing the dialog records."""

import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtWidgets import QApplication, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import MAX_ACTION_STEPS, ActionFilter, ActionStatus, ActionStep
from mailbrief.domain.breakdown import (
    FULL,
    LOCAL_MAIL,
    NOT_OPEN,
    BreakdownEnding,
    BreakdownOptions,
    BreakdownPart,
    BreakdownPreview,
    BreakdownProposal,
    BreakdownStatus,
    KeptStep,
)
from mailbrief.domain.drafting import PreviewLine
from mailbrief.domain.preferences import AnalysisMode, OwnerPreferences
from mailbrief.errors import ConfigurationError
from mailbrief.services.breakdown import (
    TASK_CHANGED,
    BreakdownConflictError,
    BreakdownUnavailableError,
)
from mailbrief.services.owner_consents import NO_OWNER_CONSENT
from mailbrief.ui.action_text import NO_BREAKDOWN_AI, breakdown_block
from mailbrief.ui.actions_view import ActionsPanel
from mailbrief.ui.breakdown_dialog import (
    EMPTY_STEP,
    TOO_LONG,
    BreakdownDialog,
    slots_text,
)
from mailbrief.ui.diagnostics import error_guidance
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.theme import ThemeMode, apply_theme
from tests.factories import make_action
from tests.ui.test_actions_view import NOW, show
from tests.ui.test_actions_workflow import finish
from tests.ui.test_main_window_layout import save_shot
from tests.ui.test_workflow import FakeBackend

GENERATION = "00000000-0000-4000-8000-000000000777"
PARTY = make_action(
    title="Plan the party",
    notes="Budget is 2,000.",
    steps=(ActionStep(step_id=1, position=0, text="Book the venue", done=False),),
)


def proposal(*steps: str, free: int = 29, missing: tuple[str, ...] = ()) -> BreakdownProposal:
    return BreakdownProposal(
        generation_id=GENERATION,
        task_public_id=PARTY.public_id,
        read_revision=PARTY.revision,
        steps=steps or ("Order food", "Send invitations"),
        missing_context=missing,
        free_slots=free,
    )


def calls(backend: FakeBackend, name: str) -> list[tuple[object, ...]]:
    return [call for call in backend.breakdown_calls if call[0] == name]


@pytest.fixture
def panel(qtbot: QtBot) -> ActionsPanel:
    result = ActionsPanel()
    qtbot.addWidget(result)
    return result


@pytest.fixture
def dialog(qtbot: QtBot) -> Iterator[BreakdownDialog]:
    parent = QWidget()  # Kept here for the test: the dialog goes with its parent.
    qtbot.addWidget(parent)
    yield BreakdownDialog(parent)


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


async def settle(window: MainWindow) -> None:
    """Let the small tasks the window starts beside its operations finish."""
    await window.drain_calendar_reads()


async def to_review(window: MainWindow) -> BreakdownDialog:
    """Open the dialog on the task shown, preview, agree and ask."""
    dialog = window.breakdown_dialog
    window.actions_panel.breakdown_button.click()
    dialog.preview_button.click()
    await finish(window)
    if dialog.consent.isVisibleTo(dialog):
        dialog.consent.setChecked(True)
    dialog.ask_button.click()
    await finish(window)
    return dialog


# --- When it is offered -----------------------------------------------------------------------


def test_the_reasons_a_task_cannot_be_broken_down() -> None:
    done = make_action(status=ActionStatus.COMPLETED, completed_at_utc=NOW)
    full = make_action(
        steps=tuple(
            ActionStep(step_id=n + 1, position=n, text=f"Step {n}", done=False)
            for n in range(MAX_ACTION_STEPS)
        )
    )
    assert breakdown_block(PARTY, ai_ready=True, local_mail=True) is None
    assert breakdown_block(done, ai_ready=True, local_mail=False) == NOT_OPEN
    assert breakdown_block(full, ai_ready=False, local_mail=False) == FULL
    assert breakdown_block(PARTY, ai_ready=False, local_mail=False) == NO_BREAKDOWN_AI


def test_the_button_follows_the_task_and_says_why_not(panel: ActionsPanel) -> None:
    show(panel, ActionFilter.OPEN, PARTY)
    reason = panel.detail.breakdown_reason
    assert panel.breakdown_button.isEnabled()
    assert reason.isHidden()
    asked: list[tuple[str, object]] = []
    panel.action_requested.connect(lambda kind, action: asked.append((kind, action)))
    panel.breakdown_button.click()
    assert asked == [("breakdown", PARTY)]

    panel.set_breakdown_access(ai_ready=False, local_mail=False)
    assert not panel.breakdown_button.isEnabled()
    assert reason.text() == NO_BREAKDOWN_AI and not reason.isHidden()
    panel.breakdown_button.click()
    assert len(asked) == 1

    panel.set_breakdown_access(ai_ready=True, local_mail=False)
    done = PARTY.model_copy(update={"status": ActionStatus.COMPLETED, "completed_at_utc": NOW})
    show(panel, ActionFilter.OPEN)
    show(panel, ActionFilter.COMPLETED, done)
    panel.show_view(ActionFilter.COMPLETED)
    assert not panel.breakdown_button.isEnabled()
    assert reason.text() == NOT_OPEN

    panel.set_busy(True)
    assert not panel.breakdown_button.isEnabled()


async def test_local_mail_analysis_keeps_mail_tasks_on_this_computer(
    window: MainWindow, backend: FakeBackend
) -> None:
    from tests.ui.test_actions_view import action as numbered

    assert numbered(1).sources == ()
    backend.owner_preferences = OwnerPreferences(
        revision=1, time_zone="UTC", analysis_mode=AnalysisMode.LOCAL
    )
    await window.initialize()
    assert window.actions_panel.breakdown_button.isEnabled()  # No email source: still offered.
    assert breakdown_block(PARTY, ai_ready=True, local_mail=True) is None
    backend.ai_ready = False
    await window._refresh_ai_status()
    assert not window.actions_panel.breakdown_button.isEnabled()
    assert window.actions_panel.detail.breakdown_reason.text() == NO_BREAKDOWN_AI
    assert LOCAL_MAIL  # Shown for a task with an email source; see the service's tests.


# --- The dialog -------------------------------------------------------------------------------


def test_only_the_parts_the_task_has_are_offered_and_none_is_ticked(
    dialog: BreakdownDialog,
) -> None:
    dialog.start_new(PARTY)
    shown = {part for part, box in dialog.part_boxes.items() if not box.isHidden()}
    assert shown == {BreakdownPart.NOTES, BreakdownPart.STEPS}
    assert not any(box.isChecked() for box in dialog.part_boxes.values())
    assert dialog.task.text() == "Plan the party"
    assert dialog.options() == BreakdownOptions()
    assert not dialog.has_unsaved_input()

    dialog.part_boxes[BreakdownPart.NOTES].setChecked(True)
    dialog.part_boxes[BreakdownPart.ESTIMATE].setChecked(True)  # Hidden: never sent.
    dialog.instruction.setPlainText("  Keep it short  ")
    assert dialog.options() == BreakdownOptions(
        instruction="Keep it short", parts=frozenset({BreakdownPart.NOTES})
    )
    assert dialog.has_unsaved_input()

    asked: list[object] = []
    dialog.preview_requested.connect(asked.append)
    dialog.instruction.setPlainText("x" * 1_001)
    dialog.preview_button.click()
    assert dialog.status.text() == TOO_LONG and asked == []
    dialog.instruction.setPlainText("Keep it short")
    dialog.preview_button.click()
    assert len(asked) == 1 and not dialog.preview_button.isEnabled()  # Held until it shows.

    dialog.start_new(make_action(title="Another"))  # The next task starts clean.
    assert dialog.instruction.toPlainText() == ""
    assert all(box.isHidden() for box in dialog.part_boxes.values())


def test_the_preview_lists_what_is_sent_and_first_use_needs_the_box(
    dialog: BreakdownDialog,
) -> None:
    dialog.start_new(PARTY)
    preview = BreakdownPreview(
        provider="groq",
        model="test-model",
        privacy_notice="Groq notice.",
        first_use=True,
        email_source=False,
        lines=(PreviewLine(label="The task's notes", characters=1_234),),
    )
    agreed: list[bool] = []
    dialog.ask_requested.connect(agreed.append)
    dialog.show_preview(preview)
    text = dialog.preview_lines.text()
    assert "Groq (test-model)" in text
    assert "- The task's notes: 1,234 characters" in text
    assert "never sends the task's person" in text and "Groq notice." in text
    assert not dialog.consent.isHidden() and not dialog.ask_button.isEnabled()
    dialog.ask_button.click()
    assert agreed == []
    dialog.consent.setChecked(True)
    dialog.ask_button.click()
    assert agreed == [True]

    dialog.show_preview(preview.model_copy(update={"first_use": False}))
    assert dialog.consent.isHidden() and dialog.ask_button.isEnabled()
    dialog.back_button.click()
    assert dialog.pages.currentIndex() == 0


def test_the_review_keeps_ticked_steps_as_edited_and_counts_the_room(
    dialog: BreakdownDialog,
) -> None:
    dialog.start_new(PARTY)
    dialog.show_proposal(
        proposal("Order food", "Send invitations", "Tidy up", free=2, missing=("Which date?",))
    )
    rows = dialog.step_rows
    assert [row.isHidden() for row in rows[:4]] == [False, False, False, True]
    assert [row.field.text() for row in rows[:3]] == ["Order food", "Send invitations", "Tidy up"]
    assert dialog.message.text().startswith("Groq proposed 3 steps.")
    assert dialog.missing.text() == "Groq would need to know: Which date?"
    assert dialog.has_unsaved_input()
    # Three ticked, room for two: Apply waits.
    assert dialog.slots.text() == slots_text(3, 2) and "Untick some" in dialog.slots.text()
    assert not dialog.apply_button.isEnabled()
    rows[1].box.setChecked(False)
    assert dialog.slots.text() == "2 steps ticked. This task has room for 2 more steps."
    assert dialog.apply_button.isEnabled()

    rows[2].field.setText("  Tidy the room after  ")
    assert dialog.kept() == (
        KeptStep(index=0, text="Order food"),
        KeptStep(index=2, text="Tidy the room after"),
    )
    kept: list[object] = []
    dialog.apply_requested.connect(kept.append)
    rows[2].field.setText("   ")
    assert not dialog.apply_button.isEnabled()
    dialog._request_apply()
    assert dialog.status.text() == EMPTY_STEP and kept == []
    rows[2].box.setChecked(False)
    dialog.apply_button.click()
    assert kept == [(KeptStep(index=0, text="Order food"),)]
    assert rows[0].field.maxLength() == 500
    assert slots_text(1, 1) == "1 step ticked. This task has room for 1 more step."

    dialog.keep_reviewing("Something to say.")
    assert dialog.status.text() == "Something to say." and dialog.pages.currentIndex() == 3
    dialog.show_proposal(proposal("Only one"))
    assert dialog.message.text().startswith("Groq proposed 1 step.")
    assert dialog.missing.isHidden()


def test_untrusted_text_stays_plain(dialog: BreakdownDialog) -> None:
    hostile = "<b>Bold</b> &amp; <a href='https://example.com'>link</a>"
    dialog.start_new(make_action(title=hostile))
    dialog.show_proposal(proposal(hostile, missing=(hostile,)))
    assert dialog.task.text() == hostile
    assert dialog.step_rows[0].field.text() == hostile
    assert dialog.missing.text().endswith(hostile)
    for label in (dialog.task, dialog.missing, dialog.message, dialog.preview_lines):
        assert not label.openExternalLinks()


# --- The window -------------------------------------------------------------------------------


async def test_preview_consent_review_apply_and_undo(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    dialog = window.breakdown_dialog
    window.actions_panel.breakdown_button.click()
    assert dialog.isVisible() and dialog.pages.currentIndex() == 0
    assert dialog.task.text() == "Plan the party"
    dialog.part_boxes[BreakdownPart.NOTES].setChecked(True)
    dialog.preview_button.click()
    await finish(window)
    prepared = calls(backend, "prepare")[0]
    assert prepared[1:] == (
        PARTY.public_id,
        BreakdownOptions(parts=frozenset({BreakdownPart.NOTES})),
    )
    assert dialog.pages.currentIndex() == 1
    assert not dialog.ask_button.isEnabled()  # First use needs the ticked box.
    dialog.consent.setChecked(True)
    dialog.ask_button.click()
    await finish(window)

    assert dialog.pages.currentIndex() == 3
    assert [row.field.text() for row in dialog.step_rows[:2]] == [
        "Book the room",
        "Send the invitations",
    ]
    dialog.step_rows[0].box.setChecked(False)
    dialog.step_rows[1].field.setText("Send the invitations by Friday")
    dialog.apply_button.click()
    await finish(window)
    await settle(window)
    assert calls(backend, "apply") == [
        ("apply", (KeptStep(index=1, text="Send the invitations by Friday"),))
    ]
    assert not dialog.isVisible()
    assert not calls(backend, "dismiss") and not calls(backend, "close")  # Nothing to turn down.
    assert window.status.text() == "Added 1 step from Groq."
    assert [row.label.text() for row in window.actions_panel.detail.step_rows()] == [
        "Book the venue",
        "Send the invitations by Friday",
    ]
    assert window.undo_button.text() == "&Undo breakdown"
    window.undo_button.click()
    await finish(window)
    assert calls(backend, "undo") == [("undo", GENERATION, PARTY.revision + 1)]
    assert window.status.text() == "Undone."
    # The next generation shows its preview again, without the consent box.
    window.actions_panel.breakdown_button.click()
    dialog.preview_button.click()
    await finish(window)
    assert dialog.consent.isHidden() and dialog.ask_button.isEnabled()
    dialog.reject()
    await settle(window)


async def test_closing_the_preview_is_recorded_as_declined(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    dialog = window.breakdown_dialog
    window.actions_panel.breakdown_button.click()
    dialog.reject()  # Before any preview: nothing was prepared, nothing is recorded.
    await settle(window)
    assert backend.breakdown_calls == []

    window.actions_panel.breakdown_button.click()
    dialog.preview_button.click()
    await finish(window)
    dialog.back_button.click()
    dialog.preview_button.click()  # A second preview replaces the first without a record.
    await finish(window)
    dialog.reject()
    await settle(window)
    assert calls(backend, "close") == [("close", GENERATION, BreakdownEnding.DECLINED)]
    assert not calls(backend, "run")
    assert window.undo_button.isHidden()


async def test_dismissing_the_review_changes_nothing(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    dialog = await to_review(window)
    dialog.dismiss_button.click()
    await settle(window)
    assert calls(backend, "dismiss") == [("dismiss", GENERATION)]
    assert not calls(backend, "apply") and not dialog.isVisible()
    assert [row.label.text() for row in window.actions_panel.detail.step_rows()] == [
        "Book the venue"
    ]


@pytest.mark.parametrize(
    ("status", "code", "message"),
    [
        (BreakdownStatus.NO_STEPS, None, "Groq proposed no new steps"),
        (BreakdownStatus.FAILED, "AI_OUTPUT_INCOMPLETE", "Groq's answer was cut off"),
        (BreakdownStatus.FAILED, "AI_REFUSED", "Groq declined this request"),
        (BreakdownStatus.FAILED, "AI_KEY_MISSING", error_guidance("AI_KEY_MISSING")),
        (BreakdownStatus.CANCELLED, None, "Cancelled. Your task is unchanged."),
    ],
)
async def test_a_run_that_proposes_nothing_says_so_and_changes_nothing(
    window: MainWindow,
    backend: FakeBackend,
    status: BreakdownStatus,
    code: str | None,
    message: str,
) -> None:
    await window.initialize()
    backend.breakdown_status, backend.breakdown_error = status, code
    dialog = await to_review(window)
    assert dialog.pages.currentIndex() == 0
    assert dialog.status.text().startswith(message)
    assert not calls(backend, "apply")
    assert window.undo_button.isHidden()
    dialog.reject()
    await settle(window)
    assert not calls(backend, "dismiss") and not calls(backend, "close")


async def test_a_task_that_cannot_be_prepared_says_why(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    dialog = window.breakdown_dialog
    for failure, message in (
        (BreakdownUnavailableError(NOT_OPEN), NOT_OPEN),
        (ConfigurationError("AI_MODEL_REQUIRED"), None),
        (RuntimeError("secret text"), "Breaking down with AI failed"),
    ):
        backend.breakdown_fail = failure
        window.actions_panel.breakdown_button.click()
        dialog.preview_button.click()
        await finish(window)
        assert dialog.pages.currentIndex() == 0 and dialog.preview_button.isEnabled()
        assert dialog.status.text() and "secret" not in dialog.status.text()
        if message is not None:
            assert dialog.status.text().startswith(message)
        dialog.reject()
    await settle(window)
    assert not calls(backend, "run") and not calls(backend, "close")


async def test_cancelling_the_request_is_recorded_and_changes_nothing(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    backend.breakdown_hold = asyncio.Event()
    dialog = window.breakdown_dialog
    window.actions_panel.breakdown_button.click()
    dialog.preview_button.click()
    await finish(window)
    dialog.consent.setChecked(True)
    dialog.ask_button.click()
    for _ in range(50):
        if calls(backend, "run") and dialog.pages.currentIndex() == 2:
            break
        await asyncio.sleep(0)
    assert dialog.pages.currentIndex() == 2 and dialog.close_button.isHidden()
    dialog.stop_button.click()
    await finish(window)
    assert calls(backend, "cancelled")
    assert calls(backend, "close") == [("close", GENERATION, BreakdownEnding.CANCELLED)]
    assert dialog.status.text() == "Cancelled. Your task is unchanged."
    assert dialog.pages.currentIndex() == 0
    dialog.reject()
    await settle(window)
    assert len(calls(backend, "close")) == 1


async def test_a_changed_task_refuses_apply_and_keeps_the_review(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    dialog = await to_review(window)
    backend.breakdown_apply_fail = BreakdownConflictError(TASK_CHANGED)
    dialog.apply_button.click()
    await finish(window)
    assert dialog.isVisible() and dialog.pages.currentIndex() == 3
    assert dialog.status.text() == TASK_CHANGED
    assert window.undo_button.isHidden()
    backend.breakdown_apply_fail = RuntimeError("secret text")
    dialog.apply_button.click()
    await finish(window)
    assert dialog.status.text().startswith("Breaking down with AI failed")
    dialog.reject()
    await settle(window)
    assert calls(backend, "dismiss") == [("dismiss", GENERATION)]


async def test_an_undo_that_is_refused_says_so(window: MainWindow, backend: FakeBackend) -> None:
    await window.initialize()
    dialog = await to_review(window)
    dialog.apply_button.click()
    await finish(window)

    async def refuse(generation_id: str, revision: int) -> object:
        raise BreakdownConflictError("Only an unchanged breakdown can be undone.")

    backend.undo_breakdown = refuse  # type: ignore[method-assign, assignment]
    window.undo_button.click()
    await finish(window)
    assert window.status.text() == "Can't undo: it has changed since then."


async def test_quitting_asks_before_losing_a_review(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    assert window._unsaved_input() is None
    dialog = await to_review(window)
    assert window._unsaved_input() == "this breakdown"
    dialog.reject()
    await settle(window)
    assert window._unsaved_input() is None


async def test_settings_lists_the_owner_consents_and_names_breakdown(
    window: MainWindow, backend: FakeBackend
) -> None:
    await window.initialize()
    settings = window.settings_dialog
    assert "breakdown" in settings.revoke_button.text()
    assert settings.owner_consents.text() == NO_OWNER_CONSENT
    dialog = await to_review(window)
    dialog.reject()
    await settle(window)
    assert settings.owner_consents.text() == "Consent is active for: breakdown."
    backend.owner_uses = ()
    await window._revoke_consent()
    assert settings.owner_consents.text() == NO_OWNER_CONSENT
    assert "planning and breakdown" in window.status.text()


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
async def test_breakdown_gallery(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    apply_theme(qapp, mode)
    backend = FakeBackend()
    backend.actions = {ActionFilter.OPEN: (PARTY,)}
    backend.breakdown_steps = (
        "Confirm the guest list with Sam",
        "Order food for twelve",
        "Send the invitations by Friday",
    )
    backend.breakdown_missing = ("The date of the party",)
    window = MainWindow(backend)
    qtbot.addWidget(window)
    await window.initialize()
    window.resize(1100, 720)
    window.show()
    try:
        window._show_page("actions")
        QApplication.processEvents()
        save_shot(window.grab(), f"p5-breakdown-task-{mode.value}")
        dialog = window.breakdown_dialog
        window.actions_panel.breakdown_button.click()
        dialog.part_boxes[BreakdownPart.NOTES].setChecked(True)
        QApplication.processEvents()
        save_shot(dialog.grab(), f"p5-breakdown-compose-{mode.value}")
        dialog.preview_button.click()
        await finish(window)
        QApplication.processEvents()
        save_shot(dialog.grab(), f"p5-breakdown-preview-{mode.value}")
        dialog.consent.setChecked(True)
        dialog.ask_button.click()
        await finish(window)
        QApplication.processEvents()
        save_shot(dialog.grab(), f"p5-breakdown-review-{mode.value}")
        assert dialog.apply_button.isEnabled()
    finally:
        window.breakdown_dialog.reject()
        await settle(window)
        window.close()
        await window.shutdown()
