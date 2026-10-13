"""The one Google sign-in (ADR 0019): it asks for Calendar too and knows what was granted."""

from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx

from mailbrief.ports.errors import AuthenticationRequiredError, ProviderPermissionError
from mailbrief.providers.gmail.auth import PROFILE_URL, GmailAuth
from mailbrief.providers.gmail.oauth import (
    CALENDAR_SCOPES,
    GMAIL_SCOPE,
    SIGN_IN_SCOPES,
    TOKEN_URL,
    DesktopClient,
    LoopbackAuthorization,
)
from tests.unit.providers.gmail.test_auth import http, make_auth, seed, token_json

__all__ = ["http"]  # The fixture, used by name.

EVERYTHING = " ".join(SIGN_IN_SCOPES)


def allowed(auth: GmailAuth) -> bool:
    """Read the permission afresh; mypy would otherwise narrow it across awaits."""
    return auth.calendar_allowed


async def test_sign_in_asks_for_gmail_and_calendar_and_nothing_else() -> None:
    opened: list[str] = []

    async def browser(url: str) -> bool:
        opened.append(url)
        return False  # Stop before waiting for a callback.

    with pytest.raises(AuthenticationRequiredError):
        await LoopbackAuthorization(browser=browser).authorize(DesktopClient("client", "secret"))
    query = parse_qs(urlsplit(opened[0]).query)
    assert query["scope"] == [EVERYTHING]
    assert (GMAIL_SCOPE, *CALENDAR_SCOPES) == SIGN_IN_SCOPES
    assert "include_granted_scopes" not in query  # Never folds in other grants.
    assert query["access_type"] == ["offline"]


@pytest.mark.parametrize(
    ("granted", "expected"),
    [
        (GMAIL_SCOPE, False),
        (f"{GMAIL_SCOPE} {CALENDAR_SCOPES[0]}", False),  # One of the two isn't enough.
        (EVERYTHING, True),
        (f"{CALENDAR_SCOPES[1]} {GMAIL_SCOPE} {CALENDAR_SCOPES[0]}", True),
    ],
)
async def test_calendar_is_allowed_only_with_both_calendar_permissions(
    http: httpx.AsyncClient, respx_mock: respx.MockRouter, granted: str, expected: bool
) -> None:
    auth, _, _ = make_auth(http)
    respx_mock.post(TOKEN_URL).respond(json=token_json() | {"scope": granted})
    respx_mock.get(PROFILE_URL).respond(json={"emailAddress": "me@example.com"})
    assert not allowed(auth)  # Nothing is known before a sign-in.
    await auth.connect()
    assert allowed(auth) is expected


async def test_gmail_still_works_when_calendar_is_left_unticked(
    http: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    auth, _, _ = make_auth(http)
    respx_mock.post(TOKEN_URL).respond(json=token_json())  # Gmail only.
    respx_mock.get(PROFILE_URL).respond(json={"emailAddress": "me@example.com"})
    assert (await auth.connect()).email_address == "me@example.com"
    assert await auth.access_token() == "access-secret"
    assert not allowed(auth)


async def test_asking_again_opens_the_browser_even_when_the_sign_in_works(
    http: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    auth, store, browser = make_auth(http)
    seed(store)
    token = respx_mock.post(TOKEN_URL)
    token.respond(json=token_json(refresh=None))  # A refresh: Gmail only, as granted before.
    profile = respx_mock.get(PROFILE_URL)
    profile.respond(json={"emailAddress": "me@example.com"})
    await auth.connect(silent_only=True)
    assert browser.calls == 0 and not allowed(auth)

    token.respond(json=token_json() | {"scope": EVERYTHING})
    await auth.connect(ask_again=True)
    assert browser.calls == 1
    assert parse_qs(token.calls[-1].request.content.decode())["grant_type"] == [
        "authorization_code"
    ]
    assert allowed(auth)
    assert store.load() is not None

    # Asking again can't switch the mailbox.
    profile.respond(json={"emailAddress": "other@example.com"})
    with pytest.raises(AuthenticationRequiredError):
        await auth.connect(ask_again=True)


@pytest.mark.parametrize(
    "wider",
    ["https://www.googleapis.com/auth/gmail.modify", "https://www.googleapis.com/auth/calendar"],
)
async def test_a_grant_wider_than_read_only_is_refused_and_never_saved(
    http: httpx.AsyncClient, respx_mock: respx.MockRouter, wider: str
) -> None:
    auth, store, _ = make_auth(http)
    respx_mock.post(TOKEN_URL).respond(json=token_json() | {"scope": f"{EVERYTHING} {wider}"})
    respx_mock.get(PROFILE_URL).respond(json={"emailAddress": "me@example.com"})
    with pytest.raises(ProviderPermissionError, match="more than read-only"):
        await auth.connect()
    assert store.load() is None and not allowed(auth)

    seed(store)  # A saved sign-in that Google now answers with a wider grant.
    with pytest.raises(ProviderPermissionError):
        await auth.connect(silent_only=True)
