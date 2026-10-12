"""Google Calendar sign-in and requests: scopes, identity, fields, retries and limits."""

import json
from datetime import UTC, date, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from pydantic import SecretStr, ValidationError

from mailbrief.domain.google_calendar import GoogleCalendarInfo, GoogleCoverage, GoogleEvent
from mailbrief.ports.errors import (
    AuthenticationRequiredError,
    MessageUnavailableError,
    ProviderPermissionError,
    ProviderRateLimitError,
    ProviderResponseError,
)
from mailbrief.providers.gcal.auth import (
    CALENDAR_LIST_URL,
    CALENDAR_SCOPES,
    CalendarAuth,
    CalendarCredentialStore,
)
from mailbrief.providers.gcal.client import CalendarClient, events_url, rfc3339
from mailbrief.providers.gcal.errors import CalendarSetupError
from mailbrief.providers.gcal.reader import MAX_CALENDARS, GoogleCalendarReader
from mailbrief.providers.gmail.oauth import (
    TOKEN_URL,
    AuthorizationGrant,
    DesktopClient,
    LoopbackAuthorization,
)
from mailbrief.providers.google.credentials import RefreshCredential

CLIENT = DesktopClient(client_id="client.apps.googleusercontent.com", client_secret="secret")
PRIMARY = GoogleCalendarInfo(
    calendar_id="owner@example.com", name="Owner", primary=True, shown=True
)
START = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)
END = datetime(2026, 12, 14, 5, 0, tzinfo=UTC)
TOKEN = {
    "access_token": "access",
    "expires_in": 3600,
    "token_type": "Bearer",
    "scope": " ".join(CALENDAR_SCOPES),
}


class MemoryVault:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, entry: str) -> str | None:
        return self.values.get((service, entry))

    def set_password(self, service: str, entry: str, value: str) -> None:
        self.values[(service, entry)] = value

    def delete_password(self, service: str, entry: str) -> None:
        del self.values[(service, entry)]


class Grants:
    def __init__(self) -> None:
        self.calls = 0

    async def authorize(self, client: DesktopClient) -> AuthorizationGrant:
        self.calls += 1
        return AuthorizationGrant(
            code="code", verifier="v" * 43, redirect_uri="http://127.0.0.1:1/oauth2/callback"
        )


class Tokens:
    def __init__(self) -> None:
        self.invalidated: list[str] = []

    async def access_token(self) -> str:
        return "token"

    async def invalidate_access_token(self, rejected_token: str) -> None:
        self.invalidated.append(rejected_token)


async def no_sleep(_delay: float) -> None:
    return None


def primary_list(email: str = "owner@example.com") -> httpx.Response:
    return httpx.Response(
        200, json={"items": [{"id": "x@group.calendar.google.com"}, {"id": email, "primary": True}]}
    )


@respx.mock
async def test_interactive_connect_saves_the_calendar_sign_in_only() -> None:
    vault = MemoryVault()
    grants = Grants()
    token = respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json=TOKEN | {"refresh_token": "refresh"})
    )
    respx.get(CALENDAR_LIST_URL).mock(return_value=primary_list())
    async with httpx.AsyncClient() as http:
        auth = CalendarAuth(CLIENT, CalendarCredentialStore(vault), http, grants)
        with pytest.raises(AuthenticationRequiredError):
            await auth.connect(silent_only=True)  # Nothing saved yet: never a browser.
        assert grants.calls == 0
        assert await auth.connect() == "owner@example.com"
        assert await auth.saved_account() == "owner@example.com"
        assert await auth.access_token() == "access"
        assert list(vault.values) == [("MailBrief.GoogleCalendar", "calendar-account-v1")]
        assert "refresh" in next(iter(vault.values.values()))
        sent = parse_qs(token.calls[-1].request.content.decode())
        assert sent["grant_type"] == ["authorization_code"]
        await auth.disconnect()
        assert vault.values == {} and await auth.saved_account() is None


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (
            httpx.Response(200, json=TOKEN | {"refresh_token": "r", "scope": CALENDAR_SCOPES[0]}),
            ProviderPermissionError,
        ),
        (httpx.Response(400, json={"error": "invalid_grant"}), AuthenticationRequiredError),
        (httpx.Response(401, json={"error": "invalid_client"}), CalendarSetupError),
        (
            httpx.Response(200, json=TOKEN | {"refresh_token": "r", "token_type": "MAC"}),
            ProviderResponseError,
        ),
        (httpx.Response(200, content=b"not json"), ProviderResponseError),
        (httpx.Response(503), ProviderResponseError),
    ],
)
@respx.mock
async def test_token_problems_are_static_errors(
    response: httpx.Response, error: type[Exception]
) -> None:
    respx.post(TOKEN_URL).mock(return_value=response)
    respx.get(CALENDAR_LIST_URL).mock(return_value=primary_list())
    async with httpx.AsyncClient() as http:
        auth = CalendarAuth(CLIENT, CalendarCredentialStore(MemoryVault()), http, Grants())
        with pytest.raises(error) as caught:
            await auth.connect()
    assert "secret" not in str(caught.value)


@respx.mock
async def test_saved_sign_in_restores_silently_and_rejects_another_account() -> None:
    vault = MemoryVault()
    store = CalendarCredentialStore(vault)
    store.save(
        RefreshCredential(
            client_id=CLIENT.client_id,
            email_address="owner@example.com",
            refresh_token=SecretStr("r"),
        )
    )
    respx.post(TOKEN_URL).mock(return_value=httpx.Response(200, json=TOKEN))
    listing = respx.get(CALENDAR_LIST_URL).mock(return_value=primary_list())
    async with httpx.AsyncClient() as http:
        grants = Grants()
        auth = CalendarAuth(CLIENT, store, http, grants)
        assert await auth.connect(silent_only=True) == "owner@example.com"
        assert grants.calls == 0
        listing.mock(return_value=primary_list("someone@example.com"))
        other = CalendarAuth(CLIENT, store, http, grants)
        with pytest.raises(AuthenticationRequiredError, match="different calendar account"):
            await other.connect(silent_only=True)
    changed = CalendarAuth(
        DesktopClient(client_id="other", client_secret="s"), store, httpx.AsyncClient(), Grants()
    )
    with pytest.raises(CalendarSetupError, match="OAuth client changed"):
        await changed.connect(silent_only=True)


@respx.mock
async def test_identity_needs_exactly_one_primary_calendar() -> None:
    respx.post(TOKEN_URL).mock(
        return_value=httpx.Response(200, json=TOKEN | {"refresh_token": "r"})
    )
    respx.get(CALENDAR_LIST_URL).mock(
        return_value=httpx.Response(200, json={"items": [{"id": "a"}]})
    )
    async with httpx.AsyncClient() as http:
        auth = CalendarAuth(CLIENT, CalendarCredentialStore(MemoryVault()), http, Grants())
        with pytest.raises(ProviderResponseError, match="no primary"):
            await auth.connect()


def test_vault_entry_that_no_longer_validates_is_a_setup_error() -> None:
    vault = MemoryVault()
    vault.set_password("MailBrief.GoogleCalendar", "calendar-account-v1", "{}")
    with pytest.raises(CalendarSetupError, match="invalid"):
        CalendarCredentialStore(vault).load()


async def test_loopback_asks_for_the_calendar_scopes_only() -> None:
    opened: list[str] = []

    async def browser(url: str) -> bool:
        opened.append(url)
        return False  # Stop before waiting for a callback.

    with pytest.raises(AuthenticationRequiredError):
        await LoopbackAuthorization(browser=browser, scopes=CALENDAR_SCOPES).authorize(CLIENT)
    scope = parse_qs(urlsplit(opened[0]).query)["scope"]
    assert scope == [" ".join(CALENDAR_SCOPES)]
    assert "gmail" not in opened[0]
    with pytest.raises(ValueError):
        LoopbackAuthorization(scopes=())


def page(items: list[dict[str, Any]], token: str | None = None) -> httpx.Response:
    return httpx.Response(200, json={"items": items} | ({"nextPageToken": token} if token else {}))


@respx.mock
async def test_events_request_names_its_fields_and_follows_pages() -> None:
    route = respx.get(events_url("owner@example.com")).mock(
        side_effect=[page([{"id": "a"}], "next"), page([{"id": "b"}])]
    )
    async with httpx.AsyncClient() as http:
        items = await CalendarClient(http, Tokens(), no_sleep).events(
            "owner@example.com", START, END
        )
    assert [item["id"] for item in items] == ["a", "b"]
    first = parse_qs(urlsplit(str(route.calls[0].request.url)).query)
    assert first["singleEvents"] == ["true"] and first["showDeleted"] == ["false"]
    assert first["timeMin"] == ["2026-10-05T04:00:00Z"] and first["timeMax"] == [
        "2026-12-14T05:00:00Z"
    ]
    for field in ("description", "attendees", "location", "transparency", "conferenceData"):
        assert field not in first["fields"][0]
    assert "workingLocation" not in first["eventTypes"] and "default" in first["eventTypes"]
    assert parse_qs(urlsplit(str(route.calls[1].request.url)).query)["pageToken"] == ["next"]


def test_calendar_ids_are_escaped_into_one_path_segment() -> None:
    assert (
        events_url("a/b?c#d@x")
        == "https://www.googleapis.com/calendar/v3/calendars/a%2Fb%3Fc%23d%40x/events"
    )
    for bad in ("", "a\nb", "x" * 1025):
        with pytest.raises(ProviderResponseError):
            events_url(bad)
    assert rfc3339(datetime(2026, 10, 5, 4, 0, 7, 9, tzinfo=UTC)) == "2026-10-05T04:00:07Z"


@pytest.mark.parametrize(
    ("responses", "error"),
    [
        ([httpx.Response(404)], MessageUnavailableError),
        (
            [httpx.Response(403, json={"error": {"errors": [{"reason": "accessNotConfigured"}]}})],
            ProviderPermissionError,
        ),
        ([httpx.Response(429)] * 4, ProviderRateLimitError),
        (  # A quota a retry won't clear is a rate limit at once, not a refusal.
            [httpx.Response(403, json={"error": {"errors": [{"reason": "dailyLimitExceeded"}]}})],
            ProviderRateLimitError,
        ),
        ([httpx.Response(503)] * 4, ProviderResponseError),
        ([httpx.Response(401), httpx.Response(401)], AuthenticationRequiredError),
        ([httpx.Response(200, content=b"[]")], ProviderResponseError),
        ([page([], "same"), page([], "same")], ProviderResponseError),
        ([httpx.Response(200, json={"items": "nope"})], ProviderResponseError),
    ],
)
@respx.mock
async def test_request_failures(responses: list[httpx.Response], error: type[Exception]) -> None:
    respx.get(events_url("owner@example.com")).mock(side_effect=responses)
    tokens = Tokens()
    async with httpx.AsyncClient() as http:
        with pytest.raises(error):
            await CalendarClient(http, tokens, no_sleep).events("owner@example.com", START, END)


@respx.mock
async def test_retries_then_succeeds_and_refreshes_once_on_401() -> None:
    respx.get(events_url("owner@example.com")).mock(
        side_effect=[httpx.Response(401), httpx.Response(503), page([{"id": "a"}])]
    )
    tokens = Tokens()
    async with httpx.AsyncClient() as http:
        items = await CalendarClient(http, tokens, no_sleep).events("owner@example.com", START, END)
    assert [item["id"] for item in items] == ["a"] and tokens.invalidated == ["token"]


@respx.mock
async def test_oversized_and_unreachable_responses() -> None:
    respx.get(events_url("owner@example.com")).mock(
        return_value=httpx.Response(200, content=json.dumps({"items": ["x" * 2_100_000]}).encode())
    )
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProviderResponseError, match="size limit"):
            await CalendarClient(http, Tokens(), no_sleep).events("owner@example.com", START, END)
    respx.get(CALENDAR_LIST_URL).mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(ProviderResponseError, match="Could not reach"):
            await CalendarClient(http, Tokens(), no_sleep).calendars()


@respx.mock
async def test_reader_lists_primary_first_and_maps_events() -> None:
    respx.get(CALENDAR_LIST_URL).mock(
        return_value=page(
            [
                {
                    "id": "z@group.calendar.google.com",
                    "summary": "Alpha",
                    "accessRole": "reader",
                    "selected": True,
                },
                {
                    "id": "owner@example.com",
                    "summary": "Owner",
                    "accessRole": "owner",
                    "primary": True,
                },
                {"id": "busy@example.com", "accessRole": "freeBusyReader"},
            ]
        )
    )
    respx.get(events_url("owner@example.com")).mock(
        return_value=page(
            [
                {
                    "id": "e",
                    "summary": "Standup",
                    "start": {"dateTime": "2026-10-12T09:00:00-04:00"},
                    "end": {"dateTime": "2026-10-12T09:30:00-04:00"},
                },
                {"id": "bad"},
            ]
        )
    )
    async with httpx.AsyncClient() as http:
        reader = GoogleCalendarReader(
            CalendarAuth(CLIENT, CalendarCredentialStore(MemoryVault()), http, Grants()),
            CalendarClient(http, Tokens(), no_sleep),
        )
        calendars = await reader.list_calendars()
        assert [c.name for c in calendars] == ["Owner", "Alpha"]
        events = await reader.list_events(PRIMARY, START, END)
    assert [event.event_id for event in events] == ["e"] and reader.auth is not None


def listing(*items: dict[str, Any]) -> httpx.Response:
    return page(list(items))


OWNER_ITEM: dict[str, object] = {
    "id": "owner@example.com",
    "summary": "Owner",
    "accessRole": "owner",
    "primary": True,
}


async def reader_calendars(response: httpx.Response) -> tuple[GoogleCalendarInfo, ...]:
    respx.get(CALENDAR_LIST_URL).mock(return_value=response)
    async with httpx.AsyncClient() as http:
        reader = GoogleCalendarReader(
            CalendarAuth(CLIENT, CalendarCredentialStore(MemoryVault()), http, Grants()),
            CalendarClient(http, Tokens(), no_sleep),
        )
        return await reader.list_calendars()


@respx.mock
async def test_the_cap_never_drops_a_shown_calendar_for_an_unticked_one() -> None:
    unticked = [
        {"id": f"a{n}@group.calendar.google.com", "summary": f"A {n:02}", "accessRole": "reader"}
        for n in range(22)
    ]
    work = {
        "id": "work@group.calendar.google.com",
        "summary": "Work",
        "accessRole": "owner",
        "selected": True,
    }
    calendars = await reader_calendars(listing(*unticked, work, OWNER_ITEM))
    assert len(calendars) == MAX_CALENDARS
    assert [c.name for c in calendars[:2]] == ["Owner", "Work"]
    assert [c.name for c in calendars if c.shown] == ["Owner", "Work"]


@respx.mock
async def test_a_repeated_calendar_is_listed_once() -> None:
    team = {"id": "team@group.calendar.google.com", "summary": "Team", "accessRole": "reader"}
    calendars = await reader_calendars(listing(OWNER_ITEM, team, team | {"summary": "Again"}))
    assert [c.name for c in calendars] == ["Owner", "Team"]


@pytest.mark.parametrize(
    "items",
    [
        [],
        [{"id": "team@group.calendar.google.com", "accessRole": "reader", "selected": True}],
        [OWNER_ITEM, {**OWNER_ITEM, "id": "second@example.com"}],
    ],
)
@respx.mock
async def test_a_list_without_one_primary_is_refused(items: list[dict[str, object]]) -> None:
    with pytest.raises(ProviderResponseError, match="no primary"):
        await reader_calendars(listing(*items))


def test_google_models_refuse_mixed_shapes() -> None:
    base: dict[str, Any] = {"calendar_id": "c", "calendar_name": "C", "event_id": "e", "title": "T"}
    with pytest.raises(ValidationError):
        GoogleEvent(**base, all_day=True, start_date=date(2026, 10, 1))
    with pytest.raises(ValidationError):
        GoogleEvent(
            **base,
            all_day=True,
            start_date=date(2026, 10, 1),
            last_date=date(2026, 10, 1),
            starts_at_utc=START,
        )
    with pytest.raises(ValidationError):
        GoogleEvent(**base, all_day=False, starts_at_utc=START)
    with pytest.raises(ValidationError):
        GoogleEvent(
            **base,
            all_day=False,
            starts_at_utc=START,
            ends_at_utc=END,
            start_date=date(2026, 10, 1),
        )
    coverage = GoogleCoverage(
        first_day=date(2026, 10, 5), last_day=date(2026, 12, 13), synced_at_utc=START
    )
    assert coverage.covers(date(2026, 10, 5), date(2026, 12, 13))
    assert not coverage.covers(date(2026, 10, 4), date(2026, 10, 10))
