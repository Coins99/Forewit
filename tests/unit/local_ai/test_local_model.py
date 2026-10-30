"""The local model client: loopback only, Groq's prompt and schema, and streamed timing."""

import json
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx

from mailbrief.domain.analysis import AIUsage, AnalysisProblem, AnalysisRequest
from mailbrief.providers.groq.provider import INSTRUCTIONS, AnalysisWireBatch
from scripts.local_ai.fixture import load_fixture
from scripts.local_ai.local_model import (
    LocalModel,
    LocalModelError,
    LoopbackGuard,
    NonLoopbackRequestError,
    require_loopback,
    to_response,
)
from tests.unit.providers.groq.groq_fixtures import wire_result

BASE = "http://127.0.0.1:8091"
CHAT = BASE + "/v1/chat/completions"


def _request() -> AnalysisRequest:
    fixture = load_fixture()
    return fixture.request(fixture.messages[0])


def _stream(*chunks: dict[str, Any]) -> bytes:
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


def _content_chunks(content: str, finish: str = "stop") -> list[dict[str, Any]]:
    middle = len(content) // 2
    return [
        {"choices": [{"delta": {"role": "assistant"}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": content[:middle]}, "finish_reason": None}]},
        {"choices": [{"delta": {"content": content[middle:]}, "finish_reason": finish}]},
        {
            "choices": [],
            "usage": {"prompt_tokens": 900, "completion_tokens": 300},
            "timings": {"prompt_n": 125, "prompt_per_second": 180.5, "predicted_per_second": 12.5},
        },
    ]


def _clock() -> Iterator[float]:
    yield from (10.0, 10.5, 14.0)


@pytest.mark.parametrize(
    "url", ["http://localhost:11434", "http://127.0.0.1:8091/", "http://[::1]:8080"]
)
def test_loopback_urls_are_accepted(url: str) -> None:
    assert require_loopback(url) == url.rstrip("/")


def test_an_openai_style_base_url_loses_its_v1() -> None:
    assert require_loopback("http://127.0.0.1:11434/v1/") == "http://127.0.0.1:11434"


@pytest.mark.parametrize(
    "url",
    [
        "https://api.groq.com/openai",
        "http://192.168.1.20:8091",
        "http://127.0.0.1.example.com",
        "file:///etc/passwd",
    ],
)
def test_other_urls_are_refused(url: str) -> None:
    with pytest.raises(ValueError, match="localhost"):
        require_loopback(url)


async def test_the_guard_refuses_a_non_loopback_request_before_it_is_sent(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.get("https://api.groq.com/x").respond(200)
    local = respx_mock.get(f"{BASE}/health").respond(200)
    guard = LoopbackGuard()
    async with httpx.AsyncClient(transport=guard) as client:
        with pytest.raises(NonLoopbackRequestError):
            await client.get("https://api.groq.com/x")
        assert (await client.get(f"{BASE}/health")).status_code == 200
    assert not route.called and local.called
    assert guard.refused == 1 and dict(guard.sent) == {"127.0.0.1": 1}


async def test_analyze_sends_groqs_instructions_and_schema_and_times_the_stream(
    respx_mock: respx.MockRouter,
) -> None:
    request = _request()
    content = json.dumps({"results": [wire_result(request.message_key, "Hi Sam,")]})
    route = respx_mock.post(CHAT).respond(200, content=_stream(*_content_chunks(content)))
    ticks = _clock()
    async with httpx.AsyncClient(transport=LoopbackGuard()) as client:
        model = LocalModel(
            client,
            base_url=BASE,
            model="qwen",
            max_output_tokens=512,
            template_kwargs={"enable_thinking": False},
            api_key="per-run-key",
            clock=lambda: next(ticks),
        )
        response, timing = await model.analyze([request])
    assert route.calls.last.request.headers["Authorization"] == "Bearer per-run-key"
    body = json.loads(route.calls.last.request.content)
    assert body["messages"][0] == {"role": "system", "content": INSTRUCTIONS}
    assert json.loads(body["messages"][1]["content"])["messages"][0]["body"] == request.body_text
    assert body["response_format"]["json_schema"]["schema"] == (
        AnalysisWireBatch.model_json_schema()
    )
    assert body["temperature"] == 0 and body["stream"] is True and body["max_tokens"] == 512
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert response.problem is None
    assert [c.message_key for c in response.candidates] == [request.message_key]
    assert response.usage == AIUsage(input_tokens=900, output_tokens=300)
    assert timing.first_token_seconds == 0.5 and timing.seconds == 4.0
    assert (timing.prompt_per_second, timing.predicted_per_second) == (180.5, 12.5)
    assert timing.prompt_evaluated == 125


async def test_a_server_error_raises_without_its_text(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(CHAT).respond(500, text="secret prompt text")
    async with httpx.AsyncClient(transport=LoopbackGuard()) as client:
        model = LocalModel(client, base_url=BASE, model="qwen", max_output_tokens=64)
        with pytest.raises(LocalModelError) as raised:
            await model.analyze([_request()])
    assert str(raised.value) == "the local server answered HTTP 500"


async def test_a_garbled_chunk_is_an_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, content=b"data: {not json\n\n")
    async with httpx.AsyncClient(transport=LoopbackGuard()) as client:
        model = LocalModel(client, base_url=BASE, model="qwen", max_output_tokens=64)
        with pytest.raises(LocalModelError, match="unreadable"):
            await model.analyze([_request()])


async def test_a_batch_needs_one_to_ten_unique_keys() -> None:
    async with httpx.AsyncClient(transport=LoopbackGuard()) as client:
        model = LocalModel(client, base_url=BASE, model="qwen", max_output_tokens=64)
        request = _request()
        with pytest.raises(ValueError, match="unique"):
            await model.analyze([request, request])
        with pytest.raises(ValueError, match="unique"):
            await model.analyze([])


async def test_a_model_on_another_host_cannot_be_configured() -> None:
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError, match="localhost"):
            LocalModel(client, base_url="https://api.groq.com", model="x", max_output_tokens=1)


@pytest.mark.parametrize(
    ("content", "finish", "problem"),
    [
        ('{"results": []}', "length", AnalysisProblem.INCOMPLETE),
        ('{"results": []}', "content_filter", AnalysisProblem.REFUSED),
        ('{"results": [', "stop", AnalysisProblem.INVALID_OUTPUT),
        ('{"results": []}', None, AnalysisProblem.INVALID_OUTPUT),
        ("", "stop", AnalysisProblem.INVALID_OUTPUT),
        ('{"results": [{"message_key": "m01"}]}', "stop", AnalysisProblem.INVALID_OUTPUT),
    ],
)
def test_unusable_answers_become_problems(
    content: str, finish: str | None, problem: AnalysisProblem
) -> None:
    assert to_response(content, finish, AIUsage()).problem is problem
