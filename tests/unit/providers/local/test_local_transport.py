"""Loopback only: the URLs a local server may have, and the guard on every request. Setup's
downloads: HTTPS to the manifest's hosts only, every redirect included."""

import httpx
import pytest
import respx

from mailbrief.providers.local.transport import (
    AllowedHostsGuard,
    LoopbackGuard,
    NonLoopbackRequestError,
    UnlistedHostError,
    host_allowed,
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


DOWNLOAD_HOSTS = frozenset({"github.com", "huggingface.co", ".hf.co"})


@pytest.mark.parametrize(
    ("host", "allowed"),
    [
        ("github.com", True),
        ("huggingface.co", True),
        ("us.aws.cdn.hf.co", True),  # Any host in the ".hf.co" domain.
        ("cdn.hf.co", True),
        ("GitHub.COM", True),  # Matching ignores case.
        ("US.AWS.CDN.HF.CO", True),
        ("hf.co", False),  # The domain itself isn't in it.
        ("evilhf.co", False),
        ("hf.co.evil.test", False),
        ("api.github.com", False),  # An exact entry covers no other host.
        ("github.com.evil.test", False),
        ("", False),
    ],
)
def test_a_host_is_allowed_by_name_or_under_a_dotted_domain(host: str, allowed: bool) -> None:
    assert host_allowed(host, DOWNLOAD_HOSTS) is allowed


def recording(
    redirects: dict[str, str] | None = None,
) -> tuple[httpx.MockTransport, list[httpx.URL]]:
    """An inner transport that answers 200, or redirects as given, and the URLs it got."""
    seen: list[httpx.URL] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url)
        target = (redirects or {}).get(str(request.url))
        if target is not None:
            return httpx.Response(302, headers={"Location": target})
        return httpx.Response(200, content=b"ok")

    return httpx.MockTransport(handle), seen


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/ggml-org/llama.cpp/releases/download/b1/x.zip",  # Plain HTTP.
        "http://us.aws.cdn.hf.co/model.gguf",
        "https://evil.example.test/x",
        "https://hf.co/x",
        "https://evilhf.co/x",
        "https://hf.co.evil.test/x",
    ],
)
async def test_the_download_guard_refuses_before_the_inner_transport_is_called(
    url: str,
) -> None:
    inner, seen = recording()
    guard = AllowedHostsGuard(DOWNLOAD_HOSTS, inner=inner)
    async with httpx.AsyncClient(transport=guard) as client:
        with pytest.raises(UnlistedHostError):
            await client.get(url)
    assert seen == [] and not guard.sent


async def test_the_download_guard_counts_what_it_sent_by_host() -> None:
    inner, seen = recording()
    guard = AllowedHostsGuard(DOWNLOAD_HOSTS, inner=inner)
    async with httpx.AsyncClient(transport=guard) as client:
        for url in ("https://github.com/a", "https://GitHub.com/b", "https://us.aws.cdn.hf.co/c"):
            assert (await client.get(url)).status_code == 200
    assert dict(guard.sent) == {"github.com": 2, "us.aws.cdn.hf.co": 1}
    assert len(seen) == 3


async def test_each_redirect_is_checked_as_a_request_of_its_own() -> None:
    inner, seen = recording(
        {
            "https://huggingface.co/model": "https://us.aws.cdn.hf.co/model",
            "https://github.com/unlisted": "https://hf.co.evil.test/unlisted",
            "https://github.com/plain": "http://github.com/plain",
        }
    )
    guard = AllowedHostsGuard(DOWNLOAD_HOSTS, inner=inner)
    async with httpx.AsyncClient(transport=guard, follow_redirects=True) as client:
        assert (await client.get("https://huggingface.co/model")).status_code == 200
        for url in ("https://github.com/unlisted", "https://github.com/plain"):
            with pytest.raises(UnlistedHostError):
                await client.get(url)
    assert [str(url) for url in seen] == [
        "https://huggingface.co/model",
        "https://us.aws.cdn.hf.co/model",
        "https://github.com/unlisted",
        "https://github.com/plain",
    ]
    assert dict(guard.sent) == {"huggingface.co": 1, "us.aws.cdn.hf.co": 1, "github.com": 2}
