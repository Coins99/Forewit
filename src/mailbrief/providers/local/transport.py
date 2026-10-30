"""HTTP that can reach this computer only.

``LoopbackGuard`` refuses any host other than localhost, 127.0.0.1 and ::1 before a request
leaves, and counts what it sent by host, so a run can show it made no third-party call.
"""

from collections import Counter

import httpx

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class NonLoopbackRequestError(RuntimeError):
    """A request was about to leave this computer."""


def require_loopback(url: str) -> str:
    """``url`` without a trailing slash or "/v1" (the usual OpenAI-compatible base, which the
    provider adds), when it is plain HTTP(S) to a loopback host; ValueError otherwise."""
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
