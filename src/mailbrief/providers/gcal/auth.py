"""The Google Calendar sign-in: its own grant and vault entry, separate from Gmail's.

The grant holds read-only calendar scopes only, so this credential can't read mail and
the Gmail credential can't read or change calendars (ADR 0018). The vault and token
endpoint handling is shared with Gmail (providers/google/).
"""

import asyncio
import time

import httpx
from pydantic import SecretStr

from mailbrief.infra import vault
from mailbrief.infra.vault import VaultBackend, VaultUnavailableError
from mailbrief.ports.errors import (
    AuthenticationRequiredError,
    ProviderPermissionError,
    ProviderRateLimitError,
)
from mailbrief.providers.gcal.errors import (
    CalendarSetupError,
    calendar_permission_guidance,
    calendar_response_error,
)
from mailbrief.providers.gmail.auth import Authorizer
from mailbrief.providers.gmail.oauth import DesktopClient
from mailbrief.providers.google.credentials import (
    RefreshCredential,
    VaultCredentialStore,
    VaultMessages,
)
from mailbrief.providers.google.tokens import TokenMessages, TokenResponse, exchange_tokens

EVENTS_SCOPE = "https://www.googleapis.com/auth/calendar.events.readonly"
CALENDAR_LIST_SCOPE = "https://www.googleapis.com/auth/calendar.calendarlist.readonly"
CALENDAR_SCOPES = (EVENTS_SCOPE, CALENDAR_LIST_SCOPE)
CALENDAR_LIST_URL = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
SERVICE = "MailBrief.GoogleCalendar"
ENTRY = "calendar-account-v1"
_VAULT_MESSAGES = VaultMessages(
    unreadable="Unable to read the calendar sign-in from the OS vault.",
    invalid="The saved calendar sign-in is invalid; disconnect Google Calendar and reconnect.",
    unsaved="Unable to save the calendar sign-in in the OS vault.",
    unremoved="Unable to remove the calendar sign-in from the OS vault.",
)
_TOKEN_MESSAGES = TokenMessages(
    unreachable="Unable to contact Google sign-in; check your connection and retry.",
    expired="Google Calendar access expired or was revoked; reconnect.",
    bad_client="Google rejected the OAuth client. Check the Desktop app client JSON.",
    unauthorized_client="Google does not authorize this OAuth client for desktop sign-in. "
    "Check that the client type is Desktop app.",
    invalid="Google returned an invalid sign-in response.",
    token_type="Google returned an unsupported token type.",
    missing_scope="Google didn't grant both calendar permissions; reconnect and allow both.",
)


def _os_vault() -> VaultBackend:
    try:
        return vault.os_vault()
    except VaultUnavailableError as exc:
        raise CalendarSetupError(str(exc)) from None


class CalendarCredentialStore(VaultCredentialStore):
    """The calendar grant's refresh credential, only in the OS vault; fails closed."""

    def __init__(self, backend: VaultBackend | None = None) -> None:
        super().__init__(
            backend if backend is not None else _os_vault(),
            service=SERVICE,
            entry=ENTRY,
            messages=_VAULT_MESSAGES,
            error=CalendarSetupError,
        )


class CalendarAuth:
    """One Google account's calendar session; the account is its primary calendar's ID."""

    def __init__(
        self,
        client: DesktopClient,
        store: CalendarCredentialStore,
        http: httpx.AsyncClient,
        authorizer: Authorizer,
    ) -> None:
        self._client = client
        self._store = store
        self._http = http
        self._authorizer = authorizer
        self._token: SecretStr | None = None
        self._expires_at = 0.0
        self._account: str | None = None
        self._lock = asyncio.Lock()

    async def saved_account(self) -> str | None:
        """The account of the saved sign-in, read from the vault; nothing is sent."""
        cached = await asyncio.to_thread(self._store.load)
        return None if cached is None else cached.email_address

    async def connect(self, *, silent_only: bool = False) -> str:
        """Restore the saved sign-in; only an interactive call may open a browser."""
        async with self._lock:
            return await self._connect(silent_only=silent_only)

    async def access_token(self) -> str:
        async with self._lock:
            await self._connect(silent_only=True)
            assert self._token is not None
            return self._token.get_secret_value()

    async def invalidate_access_token(self, rejected_token: str) -> None:
        async with self._lock:
            if self._token is not None and self._token.get_secret_value() == rejected_token:
                self._expires_at = 0

    async def disconnect(self) -> None:
        """Forget the calendar sign-in; Google access is not revoked."""
        async with self._lock:
            self._token = None
            self._account = None
            self._expires_at = 0
            await asyncio.to_thread(self._store.clear)

    async def _connect(self, *, silent_only: bool) -> str:
        if self._account is not None and self._token and time.monotonic() < self._expires_at:
            return self._account
        self._token = None
        cached = await asyncio.to_thread(self._store.load)
        if cached is not None and cached.client_id != self._client.client_id:
            raise CalendarSetupError(
                "The OAuth client changed; disconnect Google Calendar before reconnecting."
            )
        tokens: TokenResponse | None = None
        restored = False
        if cached is not None:
            try:
                tokens = await self._exchange(
                    {
                        "grant_type": "refresh_token",
                        "refresh_token": cached.refresh_token.get_secret_value(),
                    }
                )
                restored = True
            except AuthenticationRequiredError:
                if silent_only:
                    raise
        if tokens is None:
            if silent_only:
                raise AuthenticationRequiredError("Connect Google Calendar first.")
            grant = await self._authorizer.authorize(self._client)
            tokens = await self._exchange(
                {
                    "grant_type": "authorization_code",
                    "code": grant.code,
                    "code_verifier": grant.verifier,
                    "redirect_uri": grant.redirect_uri,
                }
            )
        account = await self._primary(tokens.access_token)
        if cached is not None and account.casefold() != cached.email_address.casefold():
            raise AuthenticationRequiredError(
                "Google returned a different calendar account; disconnect before switching."
            )
        # A saved refresh token is kept only when it just worked, never after it was refused.
        refresh = tokens.refresh_token or (cached.refresh_token if restored and cached else None)
        if refresh is None:
            raise AuthenticationRequiredError("Google did not grant offline access; reconnect.")
        await asyncio.to_thread(
            self._store.save,
            RefreshCredential(
                client_id=self._client.client_id, email_address=account, refresh_token=refresh
            ),
        )
        self._token = tokens.access_token
        self._expires_at = time.monotonic() + max(0, tokens.expires_in - 60)
        self._account = account
        return account

    async def _exchange(self, fields: dict[str, str]) -> TokenResponse:
        return await exchange_tokens(
            self._http,
            self._client.client_id,
            self._client.client_secret,
            fields,
            scopes=CALENDAR_SCOPES,
            messages=_TOKEN_MESSAGES,
            response_error=calendar_response_error,
            setup_error=CalendarSetupError,
            check_status=_check_status,
        )

    async def _primary(self, token: SecretStr) -> str:
        """The account's address: the ID of its primary calendar."""
        try:
            response = await self._http.get(
                CALENDAR_LIST_URL,
                params={"fields": "items(id,primary)", "maxResults": "250"},
                headers={"Authorization": f"Bearer {token.get_secret_value()}"},
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise calendar_response_error(
                "Unable to read the calendar account; retry later."
            ) from None
        _check_status(response)
        try:
            items = response.json()["items"]
            found = [
                item["id"]
                for item in items
                if isinstance(item, dict) and item.get("primary") is True
            ]
        except (ValueError, KeyError, TypeError):
            raise calendar_response_error("Google returned an invalid calendar list.") from None
        if len(found) != 1 or not isinstance(found[0], str) or "@" not in found[0]:
            raise calendar_response_error("Google returned no primary calendar.")
        return found[0][:320]


def _check_status(response: httpx.Response) -> None:
    if response.status_code == 401:
        raise AuthenticationRequiredError("Google Calendar sign-in was rejected; reconnect.")
    if response.status_code == 403:
        try:
            payload = response.json()
        except ValueError:
            payload = None
        raise ProviderPermissionError(calendar_permission_guidance(payload))
    if response.status_code == 429:
        raise ProviderRateLimitError("Google rate limit reached; retry later.")
    if response.status_code != 200:
        raise calendar_response_error(
            f"Google Calendar sign-in failed (HTTP {response.status_code}); retry later."
        )
