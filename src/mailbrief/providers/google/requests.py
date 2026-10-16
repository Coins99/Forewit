"""Bounded, read-only GET requests to Google APIs, with retries (Gmail and Google Calendar).

Each API supplies its own fixed messages and its special statuses; the retry, size and
rate-limit rules live here once.
"""

import json
import math
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Final, Protocol, cast

import httpx

from mailbrief.ports.errors import (
    AuthenticationRequiredError,
    ProviderPermissionError,
    ProviderRateLimitError,
)

MAX_RESPONSE_BYTES: Final = 2_000_000
ATTEMPTS: Final = 4
MAX_RETRY_DELAY: Final = 30
_RATE_LIMIT_REASONS: Final = frozenset({"rateLimitExceeded", "userRateLimitExceeded"})


class TokenSource(Protocol):
    async def access_token(self) -> str: ...

    async def invalidate_access_token(self, rejected_token: str) -> None: ...


def retry_delay(value: str | None, attempt: int) -> float:
    """Honor finite Retry-After values; large delays are returned to the caller."""
    if value:
        try:
            delay = float(value)
        except ValueError:
            try:
                delay = (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds()
            except (ValueError, TypeError, OverflowError):
                delay = math.nan
        if math.isfinite(delay):
            return max(0, delay)
    return 2.0**attempt + random.uniform(0, 0.5)


def error_reasons(payload: object) -> set[str]:
    """The ``reason`` strings of a Google error payload: from its ``errors`` list, the older
    form, and its ``details``, where newer APIs put an ``ErrorInfo`` reason."""
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return set()
    reasons: set[str] = set()
    for field in ("errors", "details"):
        entries = error.get(field)
        if isinstance(entries, list):
            reasons.update(
                reason
                for entry in entries
                if isinstance(entry, dict) and isinstance(reason := entry.get("reason"), str)
            )
    return reasons


@dataclass(frozen=True, slots=True)
class GetMessages:
    """One API's fixed error texts; ``{status}`` becomes the numeric HTTP status."""

    too_large: str
    timed_out: str
    unreachable: str
    rejected: str
    rate_limited: str
    unavailable: str
    failed: str
    unreadable: str
    retry_limit: str


@dataclass(frozen=True, slots=True)
class Retry:
    """A special status asks for the same request again with these parameters."""

    params: httpx.QueryParams


class Missing:
    """A special status means the requested item does not exist."""


MISSING: Final = Missing()

# Called after a 401 is handled, with the status, parameters, attempt and parsed payload.
# It returns None for the standard handling, Retry or MISSING, or raises its own error.
StatusHook = Callable[[int, httpx.QueryParams, int, object], Retry | Missing | None]


async def bounded_get(
    http: httpx.AsyncClient,
    auth: TokenSource,
    sleep: Callable[[float], Awaitable[None]],
    url: str,
    params: httpx.QueryParams,
    *,
    messages: GetMessages,
    response_error: Callable[[str], Exception],
    permission: Callable[[object], str],
    special: StatusHook | None = None,
) -> dict[str, object] | None:
    """GET ``url`` as JSON: at most ATTEMPTS tries and MAX_RESPONSE_BYTES, redirects never
    followed, one token refresh on 401, and rate limits and 5xx retried while the wait is
    at most MAX_RETRY_DELAY seconds. Returns None only when ``special`` says MISSING."""
    refreshed = False
    for attempt in range(ATTEMPTS):
        last = attempt == ATTEMPTS - 1
        token = await auth.access_token()
        try:
            async with http.stream(
                "GET",
                url,
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                follow_redirects=False,
                timeout=30,
            ) as response:
                body = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=65_536):
                    body.extend(chunk)
                    if len(body) > MAX_RESPONSE_BYTES:
                        raise response_error(messages.too_large)
                status = response.status_code
                after = response.headers.get("Retry-After")
        except httpx.HTTPError as exc:
            if last:
                timed_out = isinstance(exc, httpx.TimeoutException)
                raise response_error(
                    messages.timed_out if timed_out else messages.unreachable
                ) from None
            await sleep(retry_delay(None, attempt))
            continue
        if status == 401:
            if refreshed or last:
                raise AuthenticationRequiredError(messages.rejected)
            await auth.invalidate_access_token(token)
            refreshed = True
            continue
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            payload = None
        handled = special(status, params, attempt, payload) if special is not None else None
        if isinstance(handled, Missing):
            return None
        if isinstance(handled, Retry):
            params = handled.params
            continue
        rate_limited = status == 429 or (
            status == 403 and bool(error_reasons(payload) & _RATE_LIMIT_REASONS)
        )
        if rate_limited or status in {500, 502, 503, 504}:
            delay = retry_delay(after, attempt)
            if last or delay > MAX_RETRY_DELAY:
                if rate_limited:
                    raise ProviderRateLimitError(messages.rate_limited, retry_after_seconds=delay)
                raise response_error(messages.unavailable.format(status=status))
            await sleep(delay)
            continue
        if status == 403:
            raise ProviderPermissionError(permission(payload))
        if status != 200:
            raise response_error(messages.failed.format(status=status))
        if not isinstance(payload, dict):
            raise response_error(messages.unreadable.format(status=status))
        return cast(dict[str, object], payload)
    raise response_error(messages.retry_limit)
