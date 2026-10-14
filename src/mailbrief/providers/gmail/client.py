"""Bounded, read-only Gmail requests with cancellable retries."""

import asyncio
import re
from collections.abc import Awaitable, Callable

import httpx

from mailbrief.ports.errors import ProviderContinuationError
from mailbrief.providers.gmail.errors import permission_guidance, response_error
from mailbrief.providers.google.requests import MAX_RESPONSE_BYTES as MAX_RESPONSE_BYTES
from mailbrief.providers.google.requests import (
    MISSING,
    GetMessages,
    Missing,
    Retry,
    TokenSource,
    bounded_get,
)
from mailbrief.providers.google.requests import retry_delay as retry_delay

MESSAGES_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
THREADS_URL = "https://gmail.googleapis.com/gmail/v1/users/me/threads"
# List-Id, List-Unsubscribe and Precedence only tell mailing-list and bulk mail apart
# (ADR 0020); the mapper keeps whether they are there, never their values.
METADATA_HEADERS = (
    "From",
    "To",
    "Subject",
    "Message-ID",
    "List-Id",
    "List-Unsubscribe",
    "Precedence",
)
_MESSAGES = GetMessages(
    too_large="Gmail response exceeded its size limit.",
    timed_out="Gmail request timed out after retries; check your connection and retry.",
    unreachable="Gmail network request failed after retries; check connectivity to "
    "gmail.googleapis.com, including firewall or TLS restrictions.",
    rejected="Gmail authorization rejected; reconnect.",
    rate_limited="Gmail rate limit reached; retry later.",
    unavailable="Gmail is temporarily unavailable (HTTP {status}); retry later.",
    failed="Gmail request rejected (HTTP {status}).",
    unreadable="Gmail returned an unreadable response (HTTP 200).",
    retry_limit="Gmail retry limit reached.",
)


def message_id(value: object) -> str:
    """Reject path/query injection and invalid IDs before constructing requests."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,512}", value):
        raise response_error("Gmail returned an invalid message identifier.")
    return value


def attachment_id(value: object) -> str:
    """Accept only opaque Gmail attachment identifiers before building a request path."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,4096}", value):
        raise response_error("Gmail returned an invalid attachment identifier.")
    return value


class GmailClient:
    """Read-only requests to fixed Gmail hosts; file attachments are never downloaded."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        auth: TokenSource,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._http = http
        self._auth = auth
        self._sleep = sleep

    async def list_messages(
        self,
        query: str,
        page_token: str | None,
        *,
        max_results: int = 100,
        inbox_only: bool = True,
    ) -> dict[str, object]:
        if not 1 <= max_results <= 100:
            raise ValueError("Invalid Gmail page size.")
        params = {
            "q": query,
            "maxResults": str(max_results),
            "includeSpamTrash": "false",
            "fields": "messages(id),nextPageToken,resultSizeEstimate",
        }
        if inbox_only:
            params["labelIds"] = "INBOX"
        if page_token:
            params["pageToken"] = page_token
        result = await self._get(MESSAGES_URL, httpx.QueryParams(params), missing_ok=False)
        assert result is not None
        return result

    async def metadata(self, identifier: str) -> dict[str, object] | None:
        return await self._get(
            MESSAGES_URL + "/" + message_id(identifier),
            httpx.QueryParams(
                [
                    ("format", "metadata"),
                    ("fields", "id,threadId,labelIds,internalDate,snippet,payload/headers"),
                    *(("metadataHeaders", name) for name in METADATA_HEADERS),
                ]
            ),
            missing_ok=True,
        )

    async def thread(self, identifier: str) -> dict[str, object] | None:
        """One thread's message metadata: the same headers, labels, time and snippet as
        metadata(), never bodies or attachments. None when the thread is gone."""
        return await self._get(
            THREADS_URL + "/" + message_id(identifier),
            httpx.QueryParams(
                [
                    ("format", "metadata"),
                    (
                        "fields",
                        "id,messages(id,threadId,labelIds,internalDate,snippet,payload/headers)",
                    ),
                    *(("metadataHeaders", name) for name in METADATA_HEADERS),
                ]
            ),
            missing_ok=True,
        )

    async def message(self, identifier: str) -> dict[str, object] | None:
        """One message's labels and MIME structure; attachment data stays behind attachment
        IDs."""
        return await self._get(
            MESSAGES_URL + "/" + message_id(identifier),
            httpx.QueryParams([("format", "full"), ("fields", "id,labelIds,payload")]),
            missing_ok=True,
        )

    async def part_data(self, identifier: str, attachment: str) -> dict[str, object] | None:
        """A separately stored text part. Callers must never use this for file attachments."""
        return await self._get(
            f"{MESSAGES_URL}/{message_id(identifier)}/attachments/{attachment_id(attachment)}",
            httpx.QueryParams([("fields", "size,data")]),
            missing_ok=True,
        )

    async def _get(
        self, url: str, params: httpx.QueryParams, *, missing_ok: bool
    ) -> dict[str, object] | None:
        def special(
            status: int, current: httpx.QueryParams, attempt: int, _payload: object
        ) -> Retry | Missing | None:
            if status == 400 and url == MESSAGES_URL and "pageToken" in current:
                raise ProviderContinuationError("Gmail rejected the Inbox pagination cursor.")
            if status == 404 and missing_ok:
                return MISSING
            if status == 204 and url == MESSAGES_URL and "fields" in current and attempt < 3:
                # An empty partial response is ambiguous. Retry the same listing without
                # field selection; never reconcile the Inbox based on a bare 204.
                # Unfiltered messages.list still returns IDs, not message bodies.
                return Retry(current.remove("fields"))
            return None

        return await bounded_get(
            self._http,
            self._auth,
            self._sleep,
            url,
            params,
            messages=_MESSAGES,
            response_error=response_error,
            permission=permission_guidance,
            special=special,
        )
