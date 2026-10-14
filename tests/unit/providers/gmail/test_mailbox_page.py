"""Interactive pages stay bounded, read-only and independent of daily sync."""

import asyncio
from datetime import timedelta

import httpx
import pytest
import respx

from mailbrief.ports.errors import AuthenticationRequiredError, ProviderResponseError
from mailbrief.providers.gmail.client import MESSAGES_URL, GmailClient
from mailbrief.providers.gmail.provider import GmailProvider
from tests.unit.providers.gmail.metadata_fixtures import NOW, FakeSession, metadata


@pytest.mark.parametrize("size,query", [(50, ""), (100, "from:sender@example.com")])
async def test_one_page_and_metadata_only(
    respx_mock: respx.MockRouter, size: int, query: str
) -> None:
    identifiers = [f"m{i}" for i in range(size)]
    listing = respx_mock.get(MESSAGES_URL).respond(
        json={"messages": [{"id": key} for key in identifiers], "nextPageToken": "older"}
    )
    for key in identifiers:
        respx_mock.get(f"{MESSAGES_URL}/{key}").respond(
            json=metadata(key, received=NOW - timedelta(days=400), inbox=not query)
        )
    async with httpx.AsyncClient() as http:
        provider = GmailProvider(FakeSession(), GmailClient(http, FakeSession()))
        await provider.connect()
        page = await provider.fetch_mailbox_page(page_size=size, query=query)
    assert listing.call_count == 1
    params = listing.calls[0].request.url.params
    assert params["maxResults"] == str(size) and params["q"] == query
    assert ("labelIds" in params) == (not query)
    assert page.continuation == "older" and page.attempted == size
    assert len(page.messages) == size
    assert all(call.request.url.params.get("format") == "metadata" for call in respx_mock.calls[1:])


async def test_page_keeps_observed_archives_and_skips_discarded_and_bad_items(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(MESSAGES_URL).respond(
        json={"messages": [{"id": key} for key in ("a", "a", "archived", "gone", "trash", "bad")]}
    )
    respx_mock.get(MESSAGES_URL + "/a").respond(json=metadata("a"))
    respx_mock.get(MESSAGES_URL + "/archived").respond(json=metadata("archived", inbox=False))
    respx_mock.get(MESSAGES_URL + "/gone").respond(404)
    trash = metadata("trash")
    trash["labelIds"] = ["TRASH"]
    respx_mock.get(MESSAGES_URL + "/trash").respond(json=trash)
    respx_mock.get(MESSAGES_URL + "/bad").respond(json=metadata("wrong"))
    async with httpx.AsyncClient() as http:
        provider = GmailProvider(FakeSession(), GmailClient(http, FakeSession()))
        await provider.connect()
        page = await provider.fetch_mailbox_page()
    assert page.attempted == 5 and page.failed == 1
    assert [m.provider_message_id for m in page.messages] == ["a", "archived"]
    assert page.discarded_ids == {"gone", "trash"}


@pytest.mark.parametrize(
    "payload",
    [
        {"messages": "bad"},
        {"messages": [None]},
        {"messages": [{"id": "../bad"}]},
        {"nextPageToken": ""},
        {"nextPageToken": "same"},
        {"messages": [{"id": str(i)} for i in range(51)]},
    ],
)
async def test_rejects_invalid_pages_without_metadata_requests(
    respx_mock: respx.MockRouter, payload: dict[str, object]
) -> None:
    respx_mock.get(MESSAGES_URL).respond(json=payload)
    async with httpx.AsyncClient() as http:
        provider = GmailProvider(FakeSession(), GmailClient(http, FakeSession()))
        await provider.connect()
        with pytest.raises(ProviderResponseError):
            await provider.fetch_mailbox_page(continuation="same")
    assert len(respx_mock.calls) == 1


async def test_requires_connection_and_valid_bounds() -> None:
    async with httpx.AsyncClient() as http:
        client = GmailClient(http, FakeSession())
        provider = GmailProvider(FakeSession(), client)
        with pytest.raises(AuthenticationRequiredError):
            await provider.fetch_mailbox_page()
        await provider.connect()
        for size in (0, 101):
            with pytest.raises(ValueError):
                await provider.fetch_mailbox_page(page_size=size)
            with pytest.raises(ValueError):
                await client.list_messages("", None, max_results=size)
        with pytest.raises(ProviderResponseError):
            await provider.fetch_mailbox_page(continuation="x" * 4097)


async def test_cancellation_joins_metadata_tasks(respx_mock: respx.MockRouter) -> None:
    started = asyncio.Event()
    finished = asyncio.Event()
    respx_mock.get(MESSAGES_URL).respond(json={"messages": [{"id": "a"}]})

    async def hold(request: httpx.Request) -> httpx.Response:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.set()
        return httpx.Response(200, json=metadata("a"))

    respx_mock.get(MESSAGES_URL + "/a").mock(side_effect=hold)
    async with httpx.AsyncClient() as http:
        provider = GmailProvider(FakeSession(), GmailClient(http, FakeSession()))
        await provider.connect()
        task = asyncio.create_task(provider.fetch_mailbox_page())
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()


@pytest.mark.parametrize("identifier", [None, "wrong"])
@pytest.mark.parametrize("label", ["TRASH", "SPAM"])
async def test_mismatched_discarded_metadata_cannot_delete_requested_cache_entry(
    respx_mock: respx.MockRouter,
    identifier: str | None,
    label: str,
) -> None:
    respx_mock.get(MESSAGES_URL).respond(json={"messages": [{"id": "requested"}]})
    respx_mock.get(MESSAGES_URL + "/requested").respond(
        json={"id": identifier, "labelIds": [label]}
    )
    async with httpx.AsyncClient() as http:
        provider = GmailProvider(FakeSession(), GmailClient(http, FakeSession()))
        await provider.connect()
        page = await provider.fetch_mailbox_page()
    assert page.failed == 1 and not page.discarded_ids and not page.messages
