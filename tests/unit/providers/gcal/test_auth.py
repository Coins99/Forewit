"""The calendar sign-in's failure paths: the vault, the token endpoint and the identity read."""

import httpx
import pytest
import respx
from pydantic import SecretStr

from mailbrief.infra import vault
from mailbrief.infra.vault import VaultUnavailableError
from mailbrief.ports.errors import (
    AuthenticationRequiredError,
    ProviderPermissionError,
    ProviderRateLimitError,
    ProviderResponseError,
)
from mailbrief.providers.gcal.auth import (
    CALENDAR_LIST_URL,
    CalendarAuth,
    CalendarCredentialStore,
)
from mailbrief.providers.gcal.errors import CalendarSetupError, calendar_permission_guidance
from mailbrief.providers.gmail.oauth import TOKEN_URL
from mailbrief.providers.google.credentials import RefreshCredential
from tests.unit.providers.gcal.test_client import (
    CLIENT,
    TOKEN,
    Grants,
    MemoryVault,
    primary_list,
)

SAVED = RefreshCredential(
    client_id=CLIENT.client_id, email_address="owner@example.com", refresh_token=SecretStr("old")
)


class BrokenVault:
    def get_password(self, service: str, entry: str) -> str | None:
        raise OSError("locked")

    def set_password(self, service: str, entry: str, value: str) -> None:
        raise OSError("locked")

    def delete_password(self, service: str, entry: str) -> None:
        raise OSError("locked")


def saved_store() -> CalendarCredentialStore:
    store = CalendarCredentialStore(MemoryVault())
    store.save(SAVED)
    return store


def test_vault_failures_are_static_setup_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    store = CalendarCredentialStore(BrokenVault())
    for action, message in (
        (store.load, "Unable to read"),
        (lambda: store.save(SAVED), "Unable to save"),
        (store.clear, "Unable to remove"),
    ):
        with pytest.raises(CalendarSetupError, match=message) as caught:
            action()
        assert "locked" not in str(caught.value)

    def unavailable() -> None:
        raise VaultUnavailableError("No OS credential vault on this system.")

    monkeypatch.setattr(vault, "os_vault", unavailable)
    with pytest.raises(CalendarSetupError, match="No OS credential vault"):
        CalendarCredentialStore()


@respx.mock
async def test_a_refused_saved_sign_in_is_never_saved_again() -> None:
    """Google refuses the saved token and the browser sign-in grants no new refresh token:
    the refused one must not be saved as if it still worked."""
    store = saved_store()
    respx.post(TOKEN_URL).mock(
        side_effect=[
            httpx.Response(400, json={"error": "invalid_grant"}),
            httpx.Response(400, json={"error": "invalid_grant"}),
            httpx.Response(200, json=TOKEN),  # No refresh_token.
        ]
    )
    respx.get(CALENDAR_LIST_URL).mock(return_value=primary_list())
    async with httpx.AsyncClient() as http:
        grants = Grants()
        auth = CalendarAuth(CLIENT, store, http, grants)
        with pytest.raises(AuthenticationRequiredError, match="expired or was revoked"):
            await auth.connect(silent_only=True)  # Never a browser when silent.
        assert grants.calls == 0
        with pytest.raises(AuthenticationRequiredError, match="offline access"):
            await auth.connect()
        assert grants.calls == 1
    loaded = store.load()
    assert loaded is not None and loaded.refresh_token.get_secret_value() == "old"


@respx.mock
async def test_a_working_saved_sign_in_keeps_its_refresh_token() -> None:
    store = saved_store()
    token = respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN))
    respx.get(CALENDAR_LIST_URL).mock(return_value=primary_list())
    async with httpx.AsyncClient() as http:
        auth = CalendarAuth(CLIENT, store, http, Grants())
        assert await auth.access_token() == "access"
        await auth.invalidate_access_token("another")  # Only the refused token is dropped.
        assert await auth.access_token() == "access" and token.call_count == 1
        await auth.invalidate_access_token("access")
        assert await auth.access_token() == "access" and token.call_count == 2
    loaded = store.load()
    assert loaded is not None and loaded.refresh_token.get_secret_value() == "old"


@pytest.mark.parametrize(
    ("token_response", "error", "message"),
    [
        (httpx.ConnectError("down"), ProviderResponseError, "Unable to contact Google sign-in"),
        (
            httpx.Response(400, json={"error": "unauthorized_client"}),
            CalendarSetupError,
            "Desktop app",
        ),
        (httpx.Response(401, json={"error": "other"}), AuthenticationRequiredError, "rejected"),
        (httpx.Response(400, content=b"not json"), ProviderResponseError, "HTTP 400"),
    ],
)
@respx.mock
async def test_token_endpoint_failures(
    token_response: httpx.Response | Exception, error: type[Exception], message: str
) -> None:
    if isinstance(token_response, Exception):
        respx.post(TOKEN_URL).mock(side_effect=token_response)
    else:
        respx.post(TOKEN_URL).mock(return_value=token_response)
    async with httpx.AsyncClient() as http:
        auth = CalendarAuth(CLIENT, saved_store(), http, Grants())
        with pytest.raises(error, match=message):
            await auth.connect(silent_only=True)


@pytest.mark.parametrize(
    ("listing", "error", "message"),
    [
        (httpx.ConnectError("down"), ProviderResponseError, "Unable to read the calendar"),
        (httpx.Response(200, content=b"not json"), ProviderResponseError, "invalid calendar"),
        (httpx.Response(200, json={"items": 5}), ProviderResponseError, "invalid calendar"),
        (
            httpx.Response(403, json={"error": {"errors": [{"reason": "accessNotConfigured"}]}}),
            ProviderPermissionError,
            "API is disabled",
        ),
        (httpx.Response(403, content=b"no json"), ProviderPermissionError, "denied access"),
        (httpx.Response(429), ProviderRateLimitError, "rate limit"),
        (httpx.Response(401), AuthenticationRequiredError, "rejected"),
        (httpx.Response(500), ProviderResponseError, "HTTP 500"),
    ],
)
@respx.mock
async def test_identity_read_failures(
    listing: httpx.Response | Exception, error: type[Exception], message: str
) -> None:
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN))
    if isinstance(listing, Exception):
        respx.get(CALENDAR_LIST_URL).mock(side_effect=listing)
    else:
        respx.get(CALENDAR_LIST_URL).mock(return_value=listing)
    async with httpx.AsyncClient() as http:
        auth = CalendarAuth(CLIENT, saved_store(), http, Grants())
        with pytest.raises(error, match=message):
            await auth.connect(silent_only=True)


def payload(*reasons: str, field: str = "errors") -> dict[str, object]:
    return {"error": {field: [{"reason": reason} for reason in reasons]}}


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (payload("SERVICE_DISABLED", field="details"), "API is disabled"),
        (payload("insufficientPermissions"), "allow both calendar permissions"),
        (payload("domainPolicy"), "administrator"),
        (payload("dailyLimitExceeded"), "quota is used up"),
        (payload("quotaExceeded"), "quota is used up"),
        (payload("forbidden"), "denied access; reconnect"),
        ({"error": {"errors": "nope"}}, "denied access; reconnect"),
        ([], "denied access; reconnect"),
    ],
)
def test_permission_guidance(given: object, expected: str) -> None:
    assert expected in calendar_permission_guidance(given)
