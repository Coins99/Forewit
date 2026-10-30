"""Loopback only: the URLs a local server may have, and the guard on every request."""

import httpx
import pytest
import respx

from mailbrief.providers.local.transport import (
    LoopbackGuard,
    NonLoopbackRequestError,
    require_loopback,
)

BASE = "http://127.0.0.1:8091"


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
