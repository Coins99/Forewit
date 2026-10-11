"""Bounded, read-only Google Calendar requests to one fixed host, with retries.

Every request names the fields it wants, so event descriptions, guests and conference
details never reach MailBrief (ADR 0018).
"""

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Final, Protocol, cast
from urllib.parse import quote

import httpx

from mailbrief.ports.errors import (
    AuthenticationRequiredError,
    MessageUnavailableError,
    ProviderPermissionError,
    ProviderRateLimitError,
)
from mailbrief.providers.gcal.auth import CALENDAR_LIST_URL
from mailbrief.providers.gcal.errors import calendar_permission_guidance, calendar_response_error
from mailbrief.providers.gmail.client import retry_delay

API_ROOT: Final = "https://www.googleapis.com/calendar/v3"
CALENDAR_FIELDS: Final = (
    "items(id,summary,summaryOverride,accessRole,primary,selected,hidden,deleted),nextPageToken"
)
EVENT_FIELDS: Final = (
    "items(id,status,summary,location,htmlLink,start,end,transparency,eventType),nextPageToken"
)
# Working-location entries are not events; every other kind is shown.
SHOWN_EVENT_TYPES: Final = ("default", "outOfOffice", "focusTime", "fromGmail", "birthday")
MAX_RESPONSE_BYTES: Final = 2_000_000
MAX_PAGES: Final = 20
PAGE_SIZE: Final = 250
_ATTEMPTS: Final = 4


class TokenSource(Protocol):
    async def access_token(self) -> str: ...

    async def invalidate_access_token(self, rejected_token: str) -> None: ...


def rfc3339(moment: datetime) -> str:
    """A UTC instant as Google wants it, such as 2026-10-12T04:00:00Z."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def events_url(calendar_id: str) -> str:
    """The events URL of a calendar; the ID is fully escaped, so it can't change the path."""
    if not calendar_id or len(calendar_id) > 1024 or not calendar_id.isprintable():
        raise calendar_response_error("Google returned an invalid calendar ID.")
    return f"{API_ROOT}/calendars/{quote(calendar_id, safe='')}/events"


class CalendarClient:
    """GET requests to www.googleapis.com only; redirects are never followed."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        auth: TokenSource,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._auth = auth
        self._sleep = sleep

    async def calendars(self) -> list[dict[str, object]]:
        """Every entry of the owner's calendar list."""
        return await self._pages(
            CALENDAR_LIST_URL, [("fields", CALENDAR_FIELDS), ("maxResults", str(PAGE_SIZE))]
        )

    async def events(
        self, calendar_id: str, start_utc: datetime, end_utc: datetime
    ) -> list[dict[str, object]]:
        """Events overlapping ``[start_utc, end_utc)``, repeating ones expanded.

        Google's timeMin bounds an event's end and timeMax its start, so together they
        select exactly the overlapping events.
        """
        params = [
            ("fields", EVENT_FIELDS),
            ("maxResults", str(PAGE_SIZE)),
            ("singleEvents", "true"),
            ("orderBy", "startTime"),
            ("showDeleted", "false"),
            ("timeMin", rfc3339(start_utc)),
            ("timeMax", rfc3339(end_utc)),
            *(("eventTypes", kind) for kind in SHOWN_EVENT_TYPES),
        ]
        return await self._pages(events_url(calendar_id), params)

    async def _pages(self, url: str, params: list[tuple[str, str]]) -> list[dict[str, object]]:
        items: list[dict[str, object]] = []
        token: str | None = None
        seen: set[str] = set()
        for _ in range(MAX_PAGES):
            query = httpx.QueryParams([*params, *([("pageToken", token)] if token else [])])
            page = await self._get(url, query)
            found = page.get("items", [])
            if not isinstance(found, list):
                raise calendar_response_error("Google Calendar returned an invalid page.")
            items.extend(item for item in found if isinstance(item, dict))
            next_token = page.get("nextPageToken")
            if next_token is None:
                return items
            if not isinstance(next_token, str) or not next_token or len(next_token) > 4096:
                raise calendar_response_error("Google Calendar returned an invalid page token.")
            if next_token in seen:
                raise calendar_response_error("Google Calendar repeated a page; retry later.")
            seen.add(next_token)
            token = next_token
        raise calendar_response_error("Google Calendar returned too many pages for one range.")

    async def _get(self, url: str, params: httpx.QueryParams) -> dict[str, object]:
        refreshed = False
        for attempt in range(_ATTEMPTS):
            token = await self._auth.access_token()
            try:
                async with self._http.stream(
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
                            raise calendar_response_error(
                                "Google Calendar response exceeded its size limit."
                            )
                    status = response.status_code
                    after = response.headers.get("Retry-After")
            except httpx.HTTPError:
                if attempt == _ATTEMPTS - 1:
                    raise calendar_response_error(
                        "Could not reach Google Calendar; check your connection and retry."
                    ) from None
                await self._sleep(retry_delay(None, attempt))
                continue
            if status == 401:
                if refreshed or attempt == _ATTEMPTS - 1:
                    raise AuthenticationRequiredError(
                        "Google Calendar sign-in was rejected; reconnect."
                    )
                await self._auth.invalidate_access_token(token)
                refreshed = True
                continue
            if status in {404, 410}:
                raise MessageUnavailableError("The Google calendar is no longer available.")
            try:
                payload = json.loads(body)
            except (ValueError, UnicodeError):
                payload = None
            rate_limited = status == 429
            if status == 403 and isinstance(payload, dict):
                error = payload.get("error")
                errors = error.get("errors", []) if isinstance(error, dict) else []
                rate_limited = isinstance(errors, list) and any(
                    isinstance(item, dict)
                    and item.get("reason") in {"rateLimitExceeded", "userRateLimitExceeded"}
                    for item in errors
                )
            if rate_limited or status in {500, 502, 503, 504}:
                delay = retry_delay(after, attempt)
                if attempt == _ATTEMPTS - 1 or delay > 30:
                    if rate_limited:
                        raise ProviderRateLimitError(
                            "Google Calendar rate limit reached; retry later.",
                            retry_after_seconds=delay,
                        )
                    raise calendar_response_error(
                        f"Google Calendar is temporarily unavailable (HTTP {status}); retry later."
                    )
                await self._sleep(delay)
                continue
            if status == 403:
                raise ProviderPermissionError(calendar_permission_guidance(payload))
            if status != 200 or not isinstance(payload, dict):
                raise calendar_response_error(
                    f"Google Calendar request failed (HTTP {status}); retry later."
                )
            return cast(dict[str, object], payload)
        raise calendar_response_error("Google Calendar retry limit reached.")
