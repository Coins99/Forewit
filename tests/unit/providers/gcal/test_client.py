"""Google Calendar requests (fields, retries and limits) and the calendar reader."""

import json
from datetime import UTC, date, datetime
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import respx
from pydantic import ValidationError

from mailbrief.domain.google_calendar import GoogleCalendarInfo, GoogleCoverage, GoogleEvent
from mailbrief.ports.errors import (
    AuthenticationRequiredError,
    MessageUnavailableError,
    ProviderPermissionError,
    ProviderRateLimitError,
    ProviderResponseError,
)
from mailbrief.providers.gcal.auth import CALENDAR_LIST_URL
from mailbrief.providers.gcal.client import CalendarClient, events_url, rfc3339
from mailbrief.providers.gcal.reader import MAX_CALENDARS, GoogleCalendarReader
from mailbrief.providers.gmail.auth import GmailAuth
from mailbrief.providers.gmail.oauth import (
    DesktopClient,
)

CLIENT = DesktopClient(client_id="client.apps.googleusercontent.com", client_secret="secret")
PRIMARY = GoogleCalendarInfo(
    calendar_id="owner@example.com", name="Owner", primary=True, shown=True
)
START = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)
END = datetime(2026, 12, 14, 5, 0, tzinfo=UTC)


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
            # The reader only keeps its sign-in, for the runtime.
            cast(GmailAuth, Tokens()),
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
            # The reader only keeps its sign-in, for the runtime.
            cast(GmailAuth, Tokens()),
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
