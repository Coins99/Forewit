"""A model on this computer behind an OpenAI-compatible server (llama.cpp's llama-server or
Ollama), asked exactly what the Groq adapter asks: the same instructions, the same minimized
input and the same output schema, so only the model differs.

Requests reach loopback addresses only. ``LoopbackGuard`` refuses any other host before a
request leaves and counts every request by host, so a run can show it made no third-party
call.
"""

import json
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import httpx

from mailbrief.domain.analysis import (
    MAX_ANALYSIS_BATCH,
    AIUsage,
    AnalysisCandidate,
    AnalysisProblem,
    AnalysisRequest,
    AnalysisResponse,
)
from mailbrief.providers.groq.provider import (
    INSTRUCTIONS,
    PROMPT_VERSION,
    AnalysisWireBatch,
    analysis_input,
)

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
CHAT_PATH = "/v1/chat/completions"
# Local generation on a CPU can take minutes for a long email; only connecting is quick.
TIMEOUT = httpx.Timeout(900.0, connect=10.0)


class NonLoopbackRequestError(RuntimeError):
    """A request was about to leave this computer."""


class LocalModelError(RuntimeError):
    """The local server failed; the message never carries email or model text."""


def require_loopback(url: str) -> str:
    """``url`` without a trailing slash or "/v1" (the usual OpenAI-compatible base, which
    CHAT_PATH adds), when it is plain HTTP(S) to a loopback host."""
    parsed = httpx.URL(url)
    if parsed.scheme not in ("http", "https") or parsed.host not in LOOPBACK_HOSTS:
        raise ValueError("the local model server must be on localhost, 127.0.0.1 or [::1]")
    return url.rstrip("/").removesuffix("/v1")


class LoopbackGuard(httpx.AsyncBaseTransport):
    """A transport that sends only to loopback hosts and counts what it sent, by host."""

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self._inner = inner if inner is not None else httpx.AsyncHTTPTransport()
        self.sent: Counter[str] = Counter()
        self.refused = 0

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host not in LOOPBACK_HOSTS:
            self.refused += 1
            raise NonLoopbackRequestError("refused a request to a non-loopback host")
        self.sent[host] += 1
        return await self._inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self._inner.aclose()


@dataclass(frozen=True, slots=True)
class CallTiming:
    """One call's timing: until the first output token and until the last byte."""

    seconds: float
    first_token_seconds: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    # llama-server's own measurements, when it reports them. The prompt tokens it evaluated
    # leave out those it reused from its cache.
    prompt_evaluated: int | None = None
    prompt_per_second: float | None = None
    predicted_per_second: float | None = None


def _count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _rate(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) and value > 0 else None


def to_response(content: str, finish_reason: str | None, usage: AIUsage) -> AnalysisResponse:
    """Check a streamed answer the way the Groq adapter checks its envelope."""
    if finish_reason == "length":
        return AnalysisResponse(problem=AnalysisProblem.INCOMPLETE, usage=usage)
    if finish_reason == "content_filter":
        return AnalysisResponse(problem=AnalysisProblem.REFUSED, usage=usage)
    invalid = AnalysisResponse(problem=AnalysisProblem.INVALID_OUTPUT, usage=usage)
    if finish_reason != "stop" or not content:
        return invalid
    try:
        batch = AnalysisWireBatch.model_validate_json(content, strict=True)
        candidates = tuple(
            AnalysisCandidate.model_validate(result.model_dump()) for result in batch.results
        )
    except ValueError:
        return invalid
    return AnalysisResponse(candidates=candidates, usage=usage)


class LocalModel:
    """Message analysis by a local model, one streamed chat completion per batch."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        base_url: str,
        model: str,
        max_output_tokens: int,
        template_kwargs: Mapping[str, object] | None = None,
        api_key: str | None = None,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._client = client
        self._url = require_loopback(base_url) + CHAT_PATH
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._template_kwargs = dict(template_kwargs or {})
        self._headers = {} if api_key is None else {"Authorization": f"Bearer {api_key}"}
        self._clock = clock

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def prompt_version(self) -> str:
        return PROMPT_VERSION

    def _body(self, requests: Sequence[AnalysisRequest]) -> dict[str, object]:
        body: dict[str, object] = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": INSTRUCTIONS},
                {"role": "user", "content": analysis_input(requests)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "AnalysisWireBatch",
                    "strict": True,
                    "schema": AnalysisWireBatch.model_json_schema(),
                },
            },
            "max_tokens": self._max_output_tokens,
            "temperature": 0,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        if self._template_kwargs:
            body["chat_template_kwargs"] = self._template_kwargs
        return body

    async def analyze(
        self, requests: Sequence[AnalysisRequest]
    ) -> tuple[AnalysisResponse, CallTiming]:
        """One streamed call for 1-10 requests, with its timing. Never retried: a failure
        is a result the benchmark records."""
        keys = [request.message_key for request in requests]
        if not 1 <= len(keys) <= MAX_ANALYSIS_BATCH or len(set(keys)) != len(keys):
            raise ValueError("a call needs 1 to 10 requests with unique message keys")
        started = self._clock()
        first_token: float | None = None
        parts: list[str] = []
        finish_reason: str | None = None
        usage: dict[str, object] = {}
        timings: dict[str, object] = {}
        async with self._client.stream(
            "POST", self._url, json=self._body(requests), headers=self._headers, timeout=TIMEOUT
        ) as response:
            if not response.is_success:
                await response.aread()
                raise LocalModelError(f"the local server answered HTTP {response.status_code}")
            async for line in response.aiter_lines():
                data = line.removeprefix("data:").strip() if line.startswith("data:") else ""
                if not data:
                    continue
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    raise LocalModelError("the local server sent an unreadable chunk") from None
                if not isinstance(chunk, dict):
                    continue
                if isinstance(chunk.get("usage"), dict):
                    usage = chunk["usage"]
                if isinstance(chunk.get("timings"), dict):
                    timings = chunk["timings"]
                choices = chunk.get("choices")
                if not isinstance(choices, list) or not choices:
                    continue
                choice = choices[0] if isinstance(choices[0], dict) else {}
                delta = choice.get("delta")
                text = delta.get("content") if isinstance(delta, dict) else None
                if isinstance(text, str) and text:
                    if first_token is None:
                        first_token = self._clock() - started
                    parts.append(text)
                if isinstance(choice.get("finish_reason"), str):
                    finish_reason = choice["finish_reason"]
        timing = CallTiming(
            seconds=self._clock() - started,
            first_token_seconds=first_token,
            prompt_tokens=_count(usage.get("prompt_tokens")),
            completion_tokens=_count(usage.get("completion_tokens")),
            prompt_evaluated=_count(timings.get("prompt_n")),
            prompt_per_second=_rate(timings.get("prompt_per_second")),
            predicted_per_second=_rate(timings.get("predicted_per_second")),
        )
        counts = AIUsage(input_tokens=timing.prompt_tokens, output_tokens=timing.completion_tokens)
        return to_response("".join(parts), finish_reason, counts), timing
