"""Groq calendar planning: only the chosen parts are sent, and answers are parsed strictly."""

import hashlib
import json
from collections.abc import AsyncIterator
from datetime import date

import httpx
import pytest
import respx

from mailbrief.config import Settings
from mailbrief.domain.calendar_planning import (
    BusyTime,
    CalendarPlanningRequest,
    PlannedAction,
    PlannedEvent,
)
from mailbrief.domain.drafting import DraftingProblem
from mailbrief.ports.calendar_planning import CalendarPlanningProvider
from mailbrief.providers.groq.credentials import ENTRY, SERVICE, GroqKeyStore
from mailbrief.providers.groq.factory import groq_provider
from mailbrief.providers.groq.provider import (
    PLANNING_INSTRUCTIONS,
    PLANNING_PROMPT_VERSION,
    GroqProvider,
)
from mailbrief.services.calendar_planning import preview_lines
from tests.unit.providers.groq.groq_fixtures import (
    CHAT_URL,
    TEST_KEY,
    MemoryVault,
    output_text,
    response_body,
)

PINNED_PLANNING_PROMPT = (
    "groq-calendar-2026-10-12.1",
    hashlib.sha256(PLANNING_INSTRUCTIONS.encode()).hexdigest(),
)
REQUEST = CalendarPlanningRequest(
    instruction="Move the dentist to Friday at 10",
    now_local="2026-10-14 14:20 (Wednesday)",
    time_zone="America/Toronto",
    window_first=date(2026, 10, 12),
    window_last=date(2026, 11, 8),
    events=(
        PlannedEvent(
            key="e1",
            title="Dentist",
            all_day=False,
            start="2026-10-14 11:00",
            end="2026-10-14 12:00",
        ),
    ),
)


async def no_sleep(_seconds: float) -> None:
    return None


@pytest.fixture
async def provider() -> AsyncIterator[GroqProvider]:
    settings = Settings(groq_model="test-model", ai_max_output_tokens=4_000)
    store = GroqKeyStore(MemoryVault({(SERVICE, ENTRY): TEST_KEY}))
    async with groq_provider(settings, key_store=store, sleep=no_sleep) as built:
        yield built


def answer(payload: object) -> httpx.Response:
    return httpx.Response(200, json=response_body([output_text(json.dumps(payload))]))


CHANGE = {
    "op": "update",
    "event_key": "e1",
    "title": None,
    "all_day": None,
    "start_date": "2026-10-16",
    "start_time": "10:00",
    "end_date": None,
    "end_time": None,
    "reason": "Moves it to Friday.",
}


def test_groq_is_a_planning_provider(provider: GroqProvider) -> None:
    assert isinstance(provider, CalendarPlanningProvider)
    assert provider.planning_prompt_version == PLANNING_PROMPT_VERSION == PINNED_PLANNING_PROMPT[0]
    assert "untrusted data" in PLANNING_INSTRUCTIONS


async def test_only_the_chosen_parts_are_sent(
    respx_mock: respx.MockRouter, provider: GroqProvider
) -> None:
    route = respx_mock.post(CHAT_URL).mock(
        return_value=answer({"changes": [CHANGE], "message": None})
    )
    response = await provider.plan_calendar(REQUEST)
    assert response.candidate is not None and response.candidate.changes[0].event_key == "e1"
    body = json.loads(route.calls.last.request.content)
    assert body["messages"][0] == {"role": "system", "content": PLANNING_INSTRUCTIONS}
    sent = json.loads(body["messages"][1]["content"])
    assert set(sent) == {
        "instruction",
        "now_local",
        "time_zone",
        "window_first",
        "window_last",
        "events",
    }
    assert body["response_format"]["json_schema"]["name"] == "CalendarPlan"

    with_parts = REQUEST.model_copy(
        update={
            "busy": (
                BusyTime(all_day=False, start="2026-10-16 09:00", end="2026-10-16 10:00"),
                BusyTime(all_day=True, start="2026-10-17", end="2026-10-17", title="Trip"),
            ),
            "actions": (PlannedAction(title="Send the deck", target="2026-10-15"),),
        }
    )
    await provider.plan_calendar(with_parts)
    sent = json.loads(json.loads(route.calls.last.request.content)["messages"][1]["content"])
    assert sent["busy"][0] == {
        "all_day": False,
        "start": "2026-10-16 09:00",
        "end": "2026-10-16 10:00",
    }
    assert sent["busy"][1]["title"] == "Trip"
    assert sent["actions"] == [{"title": "Send the deck", "deadline": None, "target": "2026-10-15"}]
    sizes = [line.characters for line in preview_lines(with_parts)[1:]]
    assert sizes == [
        len(json.dumps(sent[part], ensure_ascii=False)) for part in ("events", "busy", "actions")
    ]  # The preview counts exactly what is sent.


@pytest.mark.parametrize(
    ("payload", "problem"),
    [
        ({"changes": [CHANGE | {"surprise": 1}], "message": None}, DraftingProblem.INVALID_OUTPUT),
        ({"changes": "none", "message": None}, DraftingProblem.INVALID_OUTPUT),
        ("not an object", DraftingProblem.INVALID_OUTPUT),
    ],
)
async def test_unusable_answers_become_problems(
    respx_mock: respx.MockRouter, provider: GroqProvider, payload: object, problem: DraftingProblem
) -> None:
    respx_mock.post(CHAT_URL).mock(return_value=answer(payload))
    response = await provider.plan_calendar(REQUEST)
    assert response.candidate is None and response.problem is problem


async def test_cut_off_answers_are_incomplete(
    respx_mock: respx.MockRouter, provider: GroqProvider
) -> None:
    respx_mock.post(CHAT_URL).mock(
        return_value=httpx.Response(
            200, json=response_body([output_text("{")], status="incomplete")
        )
    )
    response = await provider.plan_calendar(REQUEST)
    assert response.problem is DraftingProblem.INCOMPLETE


async def test_json_validate_failed_is_incomplete(
    respx_mock: respx.MockRouter, provider: GroqProvider
) -> None:
    respx_mock.post(CHAT_URL).mock(
        return_value=httpx.Response(
            400,
            json={
                "error": {
                    "message": "x",
                    "type": "invalid_request_error",
                    "code": "json_validate_failed",
                }
            },
        )
    )
    response = await provider.plan_calendar(REQUEST)
    assert response.problem is DraftingProblem.INCOMPLETE
