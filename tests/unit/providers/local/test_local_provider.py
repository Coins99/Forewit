"""The local provider: Groq's prompt and schema over loopback, streamed timing, static errors."""

import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from mailbrief.domain.analysis import AIUsage, AnalysisProblem, AnalysisRequest
from mailbrief.domain.messages import EmailContact
from mailbrief.ports.ai_provider import AIProvider
from mailbrief.ports.errors import (
    ProviderRequestRejectedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from mailbrief.providers.groq.provider import INSTRUCTIONS, AnalysisWireBatch
from mailbrief.providers.groq.provider import PROMPT_VERSION as GROQ_PROMPT_VERSION
from mailbrief.providers.local.provider import (
    PROVIDER_NAME,
    TIMEOUT_MESSAGE,
    UNREACHABLE_MESSAGE,
    UNREADABLE_MESSAGE,
    Endpoint,
    LocalModelProvider,
    RunningServer,
    answer_tokens,
    context_tokens,
    to_response,
)
from mailbrief.providers.local.transport import LoopbackGuard
from tests.unit.providers.groq.groq_fixtures import wire_result

BASE = "http://127.0.0.1:8091"
CHAT = BASE + "/v1/chat/completions"
BODY = "Hi Sam, please send the signed form back by Friday."
MARKER = "LOCAL-SERVER-MARKER-77a1"


def make_request(key: str = "0000abcd") -> AnalysisRequest:
    return AnalysisRequest(
        message_key=key,
        subject="Form",
        sender=EmailContact(name="Alex", address="alex@example.com"),
        received_at_utc=datetime(2026, 9, 4, 13, 30, tzinfo=UTC),
        timezone_name="America/Toronto",
        body_text=BODY,
        body_truncated=False,
    )


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


def _answer(key: str = "0000abcd") -> bytes:
    content = json.dumps({"results": [wire_result(key, BODY[:20])]})
    return _stream(*_content_chunks(content))


def _clock() -> Iterator[float]:
    yield from (10.0, 10.5, 14.0)


class LeaseLog:
    """A ModelServer that records each lease, to show a call holds one until it ends."""

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.events: list[str] = []

    async def available(self) -> bool:
        return self.ready

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[Endpoint]:
        self.events.append("leased")
        try:
            yield Endpoint(BASE, "lease-key")
        finally:
            self.events.append("returned")


@asynccontextmanager
async def provider(
    server: RunningServer | LeaseLog | None = None, **options: Any
) -> AsyncIterator[LocalModelProvider]:
    async with httpx.AsyncClient(transport=LoopbackGuard()) as client:
        yield LocalModelProvider(
            client,
            server or RunningServer(BASE),
            model="qwen",
            max_output_tokens=options.pop("max_output_tokens", 64),
            **options,
        )


async def test_it_is_an_ai_provider_named_local_with_its_own_prompt_version() -> None:
    async with provider() as model:
        assert isinstance(model, AIProvider)
        assert (model.provider_name, model.model_name) == (PROVIDER_NAME, "qwen")
        assert model.prompt_version == f"local-{GROQ_PROMPT_VERSION}"
        assert "this computer" in model.privacy_notice
        assert await model.credentials_available()
    async with provider(LeaseLog(ready=False)) as model:
        assert not await model.credentials_available()


async def test_analyze_sends_groqs_instructions_and_schema_and_times_the_stream(
    respx_mock: respx.MockRouter,
) -> None:
    request = make_request()
    route = respx_mock.post(CHAT).respond(200, content=_answer())
    ticks = _clock()
    server = RunningServer(BASE + "/v1/", "per-run-key")
    async with provider(
        server,
        max_output_tokens=512,
        template_kwargs={"enable_thinking": False},
        clock=lambda: next(ticks),
    ) as model:
        response, timing = await model.analyze_timed([request])
        assert model.requests_sent == 1
    assert route.calls.last.request.headers["Authorization"] == "Bearer per-run-key"
    body = json.loads(route.calls.last.request.content)
    assert body["messages"][0] == {"role": "system", "content": INSTRUCTIONS}
    assert json.loads(body["messages"][1]["content"])["messages"][0]["body"] == BODY
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


async def test_analyze_returns_the_response_and_holds_a_lease_for_the_call(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(CHAT).respond(200, content=_answer())
    server = LeaseLog()
    async with provider(server) as model:
        response = await model.analyze([make_request()])
    assert [c.message_key for c in response.candidates] == ["0000abcd"]
    assert route.calls.last.request.headers["Authorization"] == "Bearer lease-key"
    assert "chat_template_kwargs" not in json.loads(route.calls.last.request.content)
    assert server.events == ["leased", "returned"]


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (400, ProviderRequestRejectedError),
        (413, ProviderRequestRejectedError),
        (401, ProviderUnavailableError),
        (500, ProviderUnavailableError),
        (503, ProviderUnavailableError),
    ],
)
async def test_an_http_failure_raises_without_the_servers_text(
    respx_mock: respx.MockRouter, status: int, kind: type[Exception]
) -> None:
    respx_mock.post(CHAT).respond(status, text=MARKER)
    server = LeaseLog()
    async with provider(server) as model:
        with pytest.raises(kind) as raised:
            await model.analyze([make_request()])
    assert MARKER not in str(raised.value) and f"HTTP {status}" in str(raised.value)
    assert getattr(raised.value, "http_status", None) == status
    assert server.events == ["leased", "returned"]


@pytest.mark.parametrize(
    ("failure", "kind", "message"),
    [
        (httpx.ReadTimeout(MARKER), ProviderTimeoutError, TIMEOUT_MESSAGE),
        (httpx.ConnectError(MARKER), ProviderUnavailableError, UNREACHABLE_MESSAGE),
        (httpx.RemoteProtocolError(MARKER), ProviderUnavailableError, UNREACHABLE_MESSAGE),
        (httpx.DecodingError(MARKER), ProviderUnavailableError, UNREACHABLE_MESSAGE),
    ],
)
async def test_a_server_that_cant_answer_raises_a_static_error(
    respx_mock: respx.MockRouter, failure: Exception, kind: type[Exception], message: str
) -> None:
    respx_mock.post(CHAT).mock(side_effect=failure)
    async with provider() as model:
        with pytest.raises(kind) as raised:
            await model.analyze([make_request()])
        assert model.requests_sent == 1
    assert str(raised.value) == message and raised.value.__cause__ is None


async def test_a_garbled_chunk_is_an_unreadable_answer(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(CHAT).respond(200, content=b"data: {not json " + MARKER.encode() + b"\n\n")
    async with provider() as model:
        with pytest.raises(ProviderUnavailableError) as raised:
            await model.analyze([make_request()])
    assert str(raised.value) == UNREADABLE_MESSAGE


async def test_every_call_counts_as_a_request(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(CHAT).mock(
        side_effect=[httpx.Response(200, content=_answer()), httpx.Response(500)]
    )
    async with provider() as model:
        await model.analyze([make_request()])
        with pytest.raises(ProviderUnavailableError):
            await model.analyze([make_request()])
        assert model.requests_sent == 2


async def test_a_batch_needs_one_to_ten_unique_keys() -> None:
    server = LeaseLog()
    async with provider(server) as model:
        request = make_request()
        with pytest.raises(ValueError, match="unique"):
            await model.analyze([request, request])
        with pytest.raises(ValueError, match="unique"):
            await model.analyze([])
        assert model.requests_sent == 0
    assert server.events == []


def test_a_server_on_another_host_cannot_be_named() -> None:
    with pytest.raises(ValueError, match="localhost"):
        RunningServer("https://api.groq.com")
    with pytest.raises(ValueError, match="localhost"):
        Endpoint("http://192.168.1.20:8091", "key")


def test_an_endpoint_keeps_its_key_out_of_its_repr() -> None:
    endpoint = Endpoint(BASE + "/", "secret-key")
    assert endpoint.url == BASE and "secret-key" not in repr(endpoint)


def test_a_call_budget_covers_instructions_emails_and_answers() -> None:
    assert answer_tokens(2) == 1_200
    assert context_tokens(1, 2_048) == 5_048
    assert context_tokens(3, 2_048) == 9_048


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
