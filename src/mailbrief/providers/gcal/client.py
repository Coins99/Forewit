"""Bounded, read-only Google Calendar requests to one fixed host, with retries.

Every request names the fields it wants, so event descriptions, locations, guests and
conference details never reach MailBrief (ADR 0018). Retries, size limits and rate limits
are the shared Google rules (providers/google/requests.py).
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Final
from urllib.parse import quote

import httpx

from mailbrief.ports.errors import MessageUnavailableError, ProviderRateLimitError
from mailbrief.providers.gcal.auth import CALENDAR_LIST_URL
from mailbrief.providers.gcal.errors import (
    QUOTA_GUIDANCE,
    QUOTA_REASONS,
    calendar_permission_guidance,
    calendar_response_error,
)
from mailbrief.providers.google.requests import (
    GetMessages,
    TokenSource,
    bounded_get,
    error_reasons,
)

API_ROOT: Final = "https://www.googleapis.com/calendar/v3"
CALENDAR_FIELDS: Final = (
    "items(id,summary,summaryOverride,accessRole,primary,selected,hidden,deleted),nextPageToken"
)
EVENT_FIELDS: Final = "items(id,status,summary,htmlLink,start,end),nextPageToken"
# Working-location entries are not events; every other kind is shown.
SHOWN_EVENT_TYPES: Final = ("default", "outOfOffice", "focusTime", "fromGmail", "birthday")
MAX_PAGES: Final = 20
PAGE_SIZE: Final = 250
_MESSAGES: Final = GetMessages(
    too_large="Google Calendar response exceeded its size limit.",
    timed_out="Could not reach Google Calendar; check your connection and retry.",
    unreachable="Could not reach Google Calendar; check your connection and retry.",
    rejected="Google Calendar sign-in was rejected; reconnect.",
    rate_limited="Google Calendar rate limit reached; retry later.",
    unavailable="Google Calendar is temporarily unavailable (HTTP {status}); retry later.",
    failed="Google Calendar request failed (HTTP {status}); retry later.",
    unreadable="Google Calendar request failed (HTTP {status}); retry later.",
    retry_limit="Google Calendar retry limit reached.",
)


def rfc3339(moment: datetime) -> str:
    """A UTC instant as Google wants it, such as 2026-10-12T04:00:00Z."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def events_url(calendar_id: str) -> str:
    """The events URL of a calendar; the ID is fully escaped, so it can't change the path."""
    if not calendar_id or len(calendar_id) > 1024 or not calendar_id.isprintable():
        raise calendar_response_error("Google returned an invalid calendar ID.")
    return f"{API_ROOT}/calendars/{quote(calendar_id, safe='')}/events"


def _special(status: int, _params: httpx.QueryParams, _attempt: int, payload: object) -> None:
    """A calendar that is gone, and a quota a retry won't clear (Google sends both as 403)."""
    if status in {404, 410}:
        raise MessageUnavailableError("The Google calendar is no longer available.")
    if status == 403 and error_reasons(payload) & QUOTA_REASONS:
        raise ProviderRateLimitError(QUOTA_GUIDANCE)


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
        payload = await bounded_get(
            self._http,
            self._auth,
            self._sleep,
            url,
            params,
            messages=_MESSAGES,
            response_error=calendar_response_error,
            permission=calendar_permission_guidance,
            special=_special,
        )
        assert payload is not None  # Only MISSING returns None, and _special never does.
        return payload
