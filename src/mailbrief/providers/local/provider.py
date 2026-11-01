"""A model on this computer behind the AIProvider port: llama.cpp's llama-server, or another
OpenAI-compatible server on loopback, asked exactly what the Groq adapter asks.

The instructions, the minimized input and the output schema are Groq's, so only the model
differs (P0-B2 Part 6 gives the local model a prompt of its own). Requests reach loopback
addresses only: an endpoint must be one, and the client the factory builds sends through
LoopbackGuard. Raised errors carry static messages, never email or model text, and logs
carry status codes only.
"""

import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Final, Protocol

import httpx

from mailbrief.domain.analysis import (
    MAX_ANALYSIS_BATCH,
    AIUsage,
    AnalysisCandidate,
    AnalysisProblem,
    AnalysisRequest,
    AnalysisResponse,
)
from mailbrief.ports.errors import (
    ProviderRequestRejectedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from mailbrief.providers.groq.provider import (
    INSTRUCTIONS,
    AnalysisWireBatch,
    analysis_input,
)
from mailbrief.providers.groq.provider import PROMPT_VERSION as GROQ_PROMPT_VERSION
from mailbrief.providers.local.transport import require_loopback

logger = logging.getLogger(__name__)

PROVIDER_NAME: Final = "local"
# Groq's instructions and schema until Part 6. The version follows Groq's, so a change there
# analyzes local results again too.
PROMPT_VERSION: Final = f"local-{GROQ_PROMPT_VERSION}"
PRIVACY_NOTICE: Final = (
    "The model runs on this computer: no message is sent to an AI service, and only the "
    "summary, short quotes and suggestions are kept."
)
CHAT_PATH: Final = "/v1/chat/completions"
# Generating on a CPU can take minutes for a long email; connecting is quick.
DEFAULT_TIMEOUT: Final = httpx.Timeout(900.0, connect=10.0)
# A conservative context budget per call: the instructions, each email at the 8,000-character
# limit (about 1,700 tokens measured in P0-B1) and the answer.
INSTRUCTION_TOKENS: Final = 1_000
EMAIL_TOKENS: Final = 2_000
ANSWER_TOKENS_PER_EMAIL: Final = 600

UNREACHABLE_MESSAGE: Final = "The local model isn't answering."
TIMEOUT_MESSAGE: Final = "The local model did not answer in time."
UNREADABLE_MESSAGE: Final = "The local model returned an unreadable answer."
# Statuses that refuse this particular request, such as one over the context size.
_REJECTED_STATUSES: Final = frozenset({400, 413, 422})


def answer_tokens(batch_size: int) -> int:
    """The output tokens a call of ``batch_size`` emails may need."""
    return batch_size * ANSWER_TOKENS_PER_EMAIL


def context_tokens(batch_size: int, max_output_tokens: int) -> int:
    """The context a call of ``batch_size`` emails needs: instructions, emails and answer."""
    return INSTRUCTION_TOKENS + batch_size * EMAIL_TOKENS + max_output_tokens


@dataclass(frozen=True, slots=True)
class Endpoint:
    """A running server: its loopback base URL and the API key it was started with."""

    url: str
    api_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", require_loopback(self.url))


class ModelServer(Protocol):
    """Where the local model answers."""

    async def available(self) -> bool:
        """Whether the model can be served, without starting it; never raises."""
        ...

    def lease(self) -> AbstractAsyncContextManager[Endpoint]:
        """The server's endpoint for one call, held until the call ends.

        Raises ProviderUnavailableError, with a static message and a code, when no server can
        answer.
        """
        ...


class RunningServer:
    """A server already running on loopback, which Forewit neither starts nor stops."""

    def __init__(self, url: str, api_key: str | None = None) -> None:
        self._endpoint = Endpoint(url, api_key)

    async def available(self) -> bool:
        return True

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[Endpoint]:
        yield self._endpoint


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


def _failure(status: int) -> ProviderRequestRejectedError | ProviderUnavailableError:
    """The error for an HTTP failure, with a static message; the server's text never passes."""
    if status in _REJECTED_STATUSES:
        return ProviderRequestRejectedError(
            f"The local model refused the request (HTTP {status}).", http_status=status
        )
    return ProviderUnavailableError(f"The local model failed (HTTP {status}).", http_status=status)


class LocalModelProvider:
    """Message analysis by the model a ModelServer serves: one streamed chat completion per
    batch. Nothing is retried here; the analysis service retries a message on its own."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        server: ModelServer,
        *,
        model: str,
        max_output_tokens: int,
        template_kwargs: Mapping[str, object] | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self._client = client
        self._server = server
        self._model = model
        self._max_output_tokens = max_output_tokens
        # Only for a hybrid model that thinks by default (the benchmark's --no-think).
        self._template_kwargs = dict(template_kwargs or {})
        self._timeout = timeout
        self._clock = clock
        self._requests_sent = 0

    @property
    def provider_name(self) -> str:
        return PROVIDER_NAME

    @property
    def model_name(self) -> str:
        return self._model

    @property
    def prompt_version(self) -> str:
        return PROMPT_VERSION

    @property
    def privacy_notice(self) -> str:
        return PRIVACY_NOTICE

    @property
    def requests_sent(self) -> int:
        return self._requests_sent

    async def credentials_available(self) -> bool:
        """Whether the model can be served; there is no key of the owner's to load."""
        return await self._server.available()

    async def analyze(self, requests: Sequence[AnalysisRequest]) -> AnalysisResponse:
        """One streamed chat completion for 1-10 requests."""
        response, _ = await self.analyze_timed(requests)
        return response

    async def analyze_timed(
        self, requests: Sequence[AnalysisRequest]
    ) -> tuple[AnalysisResponse, CallTiming]:
        """``analyze`` with the call's timing, which the P0-B1 benchmark records.

        A server that can't be reached, a timeout, an HTTP failure or an unreadable stream
        raises a ``ports.errors`` type with a static message. Cancelling closes the stream,
        which stops llama-server generating.
        """
        keys = [request.message_key for request in requests]
        if not 1 <= len(keys) <= MAX_ANALYSIS_BATCH or len(set(keys)) != len(keys):
            raise ValueError("a local call needs 1 to 10 requests with unique message keys")
        body = self._body(requests)
        async with self._server.lease() as endpoint:
            headers = (
                {} if endpoint.api_key is None else {"Authorization": f"Bearer {endpoint.api_key}"}
            )
            self._requests_sent += 1
            try:
                return await self._stream(endpoint.url + CHAT_PATH, body, headers)
            except httpx.TimeoutException:
                logger.info("Local model call timed out")
                raise ProviderTimeoutError(TIMEOUT_MESSAGE) from None
            except httpx.RequestError:  # Refused, cut off or undecodable.
                logger.info("Local model call lost its connection")
                raise ProviderUnavailableError(UNREACHABLE_MESSAGE) from None

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

    async def _stream(
        self, url: str, body: dict[str, object], headers: dict[str, str]
    ) -> tuple[AnalysisResponse, CallTiming]:
        started = self._clock()
        first_token: float | None = None
        parts: list[str] = []
        finish_reason: str | None = None
        usage: dict[str, object] = {}
        timings: dict[str, object] = {}
        async with self._client.stream(
            "POST", url, json=body, headers=headers, timeout=self._timeout, follow_redirects=False
        ) as response:
            if not response.is_success:
                await response.aread()
                logger.info("Local model call failed: HTTP %d", response.status_code)
                raise _failure(response.status_code)
            async for line in response.aiter_lines():
                data = line.removeprefix("data:").strip() if line.startswith("data:") else ""
                if not data:
                    continue
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    raise ProviderUnavailableError(UNREADABLE_MESSAGE) from None
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
