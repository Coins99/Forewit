"""The desktop runtime's AI breakdown calls: real storage, Groq through respx.

The runtime is where the desktop composes the service, so these check what the UI's fake
backend can't: that mail analysed on this computer keeps its tasks here, and that applying,
undoing and recording need neither Groq nor the owner's preferences.
"""

import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from mailbrief.domain.actions import ActionEdit
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.breakdown import (
    LOCAL_MAIL,
    BreakdownEnding,
    BreakdownOptions,
    BreakdownStatus,
    KeptStep,
)
from mailbrief.domain.preferences import PreferencesEdit
from mailbrief.providers.groq.credentials import ENTRY, SERVICE
from mailbrief.services.breakdown import BreakdownUnavailableError
from mailbrief.ui import runtime as runtime_module
from mailbrief.ui.preferences import DesktopPreferences
from mailbrief.ui.runtime import DesktopRuntime
from tests.unit.providers.groq.groq_fixtures import (
    CHAT_URL,
    TEST_KEY,
    MemoryVault,
    output_text,
    response_body,
)
from tests.unit.test_desktop_actions import seed_brief

MANUAL = ActionEdit(
    title="Plan the party",
    ownership=ActionOwnership.MINE,
    person=None,
    priority=None,
    tags=(),
    effort=None,
    estimate_minutes=None,
    target_date=None,
    notes="",
)


@pytest.fixture(autouse=True)
def vault(monkeypatch: pytest.MonkeyPatch) -> MemoryVault:
    entries = MemoryVault({(SERVICE, ENTRY): TEST_KEY})
    monkeypatch.setattr("mailbrief.providers.groq.credentials.os_vault", lambda: entries)
    return entries


class Approve:
    async def request_breakdown_consent(self, preview: Any) -> bool:
        return True


def groq_answer(request: httpx.Request) -> httpx.Response:
    content = json.dumps({"steps": ["Order food", "Send invitations"], "missing_context": []})
    return httpx.Response(200, json=response_body([output_text(content)]))


def history(path: Path) -> list[tuple[Any, ...]]:
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute(
            "SELECT outcome, error_code, applied_count FROM task_breakdowns ORDER BY id"
        ).fetchall()


async def test_a_breakdown_through_the_runtime(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    path = tmp_path / "mailbrief.sqlite3"
    runtime = DesktopRuntime(path)
    route = respx_mock.post(CHAT_URL).mock(side_effect=groq_answer)
    try:
        assert await runtime.load_saved() is None
        await runtime.save_preferences(DesktopPreferences(groq_model="test-model"))
        task = await runtime.create_action(MANUAL, [])
        assert await runtime.owner_consents() == ()

        plan = await runtime.prepare_breakdown(task.public_id, BreakdownOptions())
        assert plan.preview.first_use and route.call_count == 0
        outcome = await runtime.run_breakdown(plan, Approve(), asyncio.Event())
        assert outcome.status is BreakdownStatus.PROPOSED and outcome.proposal is not None
        sent = json.loads(json.loads(route.calls.last.request.content)["messages"][1]["content"])
        assert set(sent) == {"request_key", "today", "instruction", "title"}
        assert task.public_id not in route.calls.last.request.content.decode()
        assert await runtime.owner_consents() == ("breakdown",)

        # Applying and undoing are local: they work with no model and no key saved.
        await runtime.save_preferences(DesktopPreferences(groq_model=""))
        await runtime.remove_key()
        applied = await runtime.apply_breakdown(
            outcome.proposal, [KeptStep(index=1, text="Send invitations")]
        )
        assert [step.text for step in applied.action.steps] == ["Send invitations"]
        undone = await runtime.undo_breakdown(applied.generation_id, applied.action.revision)
        assert undone.steps == ()
        assert route.call_count == 1
        assert history(path) == [("undone", None, 1)]
    finally:
        await runtime.close()


async def test_mail_analysed_on_this_computer_keeps_its_tasks_here(
    tmp_path: Path, respx_mock: respx.MockRouter
) -> None:
    path = tmp_path / "mailbrief.sqlite3"
    runtime = DesktopRuntime(path)
    route = respx_mock.post(CHAT_URL).mock(side_effect=groq_answer)
    try:
        assert await runtime.load_saved() is None
        await runtime.save_preferences(DesktopPreferences(groq_model="test-model"))
        await seed_brief(path)
        saved = await runtime.load_saved()
        assert saved is not None
        from_mail = await runtime.accept_suggestion(saved.items[0].suggestions[0].suggestion_id)
        assert from_mail.sources
        manual = await runtime.create_action(MANUAL, [])

        # In cloud mode the task can be prepared, and its preview says it has an email source.
        plan = await runtime.prepare_breakdown(from_mail.public_id, BreakdownOptions())
        assert plan.preview.email_source

        await runtime.save_owner_preferences(PreferencesEdit(analysis_mode="local"), 0)
        with pytest.raises(BreakdownUnavailableError, match=LOCAL_MAIL):
            await runtime.prepare_breakdown(from_mail.public_id, BreakdownOptions())
        # A task with no email source is still offered, as its own Groq capability.
        assert not (
            await runtime.prepare_breakdown(manual.public_id, BreakdownOptions())
        ).preview.email_source
        assert route.call_count == 0 and history(path) == []
    finally:
        await runtime.close()


async def test_recording_needs_no_provider_or_preferences(
    tmp_path: Path, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "mailbrief.sqlite3"
    runtime = DesktopRuntime(path)
    route = respx_mock.post(CHAT_URL).mock(side_effect=groq_answer)
    try:
        assert await runtime.load_saved() is None
        await runtime.save_preferences(DesktopPreferences(groq_model="test-model"))
        task = await runtime.create_action(MANUAL, [])
        closed = await runtime.prepare_breakdown(task.public_id, BreakdownOptions())
        broken = await runtime.prepare_breakdown(task.public_id, BreakdownOptions())
        shown = await runtime.prepare_breakdown(task.public_id, BreakdownOptions())
        outcome = await runtime.run_breakdown(shown, Approve(), asyncio.Event())
        assert outcome.proposal is not None

        async def unreadable() -> None:
            raise AssertionError("a local breakdown operation read the owner's preferences")

        monkeypatch.setattr(runtime, "get_owner_preferences", unreadable)
        monkeypatch.setattr(
            runtime_module, "groq_provider", lambda *_a, **_k: pytest.fail("Groq was opened")
        )
        await runtime.close_breakdown(closed, BreakdownEnding.DECLINED)
        await runtime.close_breakdown(broken, BreakdownEnding.FAILED, "BREAKDOWN_UNEXPECTED_ERROR")
        await runtime.dismiss_breakdown(outcome.proposal.generation_id)
        assert history(path) == [
            ("dismissed", None, 0),
            ("declined", None, 0),
            ("failed", "BREAKDOWN_UNEXPECTED_ERROR", 0),
        ]
        assert route.call_count == 1
    finally:
        await runtime.close()
