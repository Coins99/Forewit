"""Bounded runtime requests, cache, independent search and retry behavior."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
import respx
from pytestqt.qtbot import QtBot
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.config import Settings
from mailbrief.domain.cached_mail import InboxPage
from mailbrief.ports.errors import (
    AuthenticationRequiredError,
    ProviderContinuationError,
    ProviderError,
    ProviderResponseError,
)
from mailbrief.providers.gmail.client import MESSAGES_URL, GmailClient
from mailbrief.providers.gmail.provider import GmailProvider
from mailbrief.storage.repositories import AccountRepository, MessageRepository
from mailbrief.ui import runtime
from mailbrief.ui.inbox_view import InboxPanel
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.runtime import DesktopRuntime
from mailbrief.ui.theme import apply_theme
from mailbrief.ui.theme.tokens import ThemeMode
from tests.factories import make_message
from tests.ui.test_workflow import FakeBackend, finish
from tests.unit.providers.gmail.metadata_fixtures import ACCOUNT, NOW, FakeSession, metadata


@pytest.fixture
async def backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[DesktopRuntime]:
    @asynccontextmanager
    async def provider(settings: Settings, *, silent_only: bool) -> AsyncIterator[GmailProvider]:
        async with httpx.AsyncClient() as http:
            yield GmailProvider(FakeSession(), GmailClient(http, FakeSession()))

    monkeypatch.setattr(runtime, "gmail_provider", provider)
    backend = DesktopRuntime(tmp_path / "mailbrief.sqlite3")
    await backend.load_saved()
    try:
        yield backend
    finally:
        await backend.close()


async def test_initial_50_then_100_search_refresh_and_restart(
    backend: DesktopRuntime,
    respx_mock: respx.MockRouter,
) -> None:
    listing = respx_mock.get(MESSAGES_URL).mock(
        side_effect=[
            httpx.Response(200, json={"messages": [{"id": "a"}], "nextPageToken": "older"}),
            httpx.Response(200, json={"messages": [{"id": "a"}, {"id": "b"}]}),
            httpx.Response(200, json={"messages": [{"id": "outside"}]}),
            httpx.Response(200, json={"messages": [{"id": "a"}], "nextPageToken": "refresh"}),
        ]
    )
    respx_mock.get(MESSAGES_URL + "/a").respond(json=metadata("a"))
    respx_mock.get(MESSAGES_URL + "/b").respond(
        json=metadata("b", received=NOW - timedelta(days=500))
    )
    respx_mock.get(MESSAGES_URL + "/outside").respond(json=metadata("outside", inbox=False))
    cancel = asyncio.Event()
    first = await backend.browse_inbox("me@example.com", cancel)
    assert len(first.messages) == 1 and first.has_more and not first.cached
    second = await backend.browse_inbox("me@example.com", cancel, load_more=True)
    assert [m.provider_message_id for m in second.messages] == ["a", "b"]
    assert not second.has_more
    await backend.browse_inbox("me@example.com", cancel, load_more=True)
    assert listing.call_count == 2
    search = await backend.browse_inbox("me@example.com", cancel, query="old invoice")
    assert [m.provider_message_id for m in search.messages] == ["outside"]
    assert (await backend.inbox_messages("me@example.com")).messages == second.messages
    refresh = await backend.browse_inbox("me@example.com", cancel)
    assert [m.provider_message_id for m in refresh.messages] == ["a", "b"]
    assert refresh.has_more
    params = [call.request.url.params for call in listing.calls]
    assert [p["maxResults"] for p in params] == ["50", "100", "50", "50"]
    assert params[1]["pageToken"] == "older" and "pageToken" not in params[3]
    assert "labelIds" not in params[2] and params[2]["q"] == "old invoice"
    assert not (await backend.inbox_messages("someone@example.com")).messages
    reopened = DesktopRuntime(backend._path)
    await reopened.load_saved()
    try:
        saved = await reopened.inbox_messages("me@example.com")
        assert saved.cached and {m.provider_message_id for m in saved.messages} == {"a", "b"}
    finally:
        await reopened.close()


async def test_failed_metadata_retries_same_page_without_duplicates(
    backend: DesktopRuntime,
    respx_mock: respx.MockRouter,
) -> None:
    listing = respx_mock.get(MESSAGES_URL).respond(
        json={"messages": [{"id": "a"}, {"id": "bad"}], "nextPageToken": "older"}
    )
    respx_mock.get(MESSAGES_URL + "/a").respond(json=metadata("a"))
    respx_mock.get(MESSAGES_URL + "/bad").mock(
        side_effect=[
            httpx.Response(200, json={}),
            httpx.Response(200, json=metadata("bad")),
        ]
    )
    first = await backend.browse_inbox("me@example.com", asyncio.Event())
    assert first.error_code == "METADATA_ITEMS_FAILED" and first.has_more
    retry = await backend.browse_inbox("me@example.com", asyncio.Event(), load_more=True)
    assert len(retry.messages) == 2 and retry.error_code is None
    assert all("pageToken" not in call.request.url.params for call in listing.calls)
    assert all(call.request.url.params["maxResults"] == "50" for call in listing.calls)


async def test_invalid_cursor_does_not_advance_and_refresh_restarts(
    backend: DesktopRuntime,
    respx_mock: respx.MockRouter,
) -> None:
    listing = respx_mock.get(MESSAGES_URL).mock(
        side_effect=[
            httpx.Response(200, json={"messages": [{"id": "a"}], "nextPageToken": "expired"}),
            httpx.Response(400),
            httpx.Response(200, json={"messages": []}),
        ]
    )
    respx_mock.get(MESSAGES_URL + "/a").respond(json=metadata("a"))
    await backend.browse_inbox("me@example.com", asyncio.Event())
    with pytest.raises(ProviderContinuationError):
        await backend.browse_inbox("me@example.com", asyncio.Event(), load_more=True)
    assert (await backend.inbox_messages("me@example.com")).has_more
    await backend.browse_inbox("me@example.com", asyncio.Event())
    assert "pageToken" not in listing.calls[2].request.url.params


async def test_cached_display_precedes_network_and_load_more_preserves_selection(
    qtbot: QtBot,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    calls: list[tuple[bool, str]] = []

    class Backend(FakeBackend):
        async def inbox_messages(
            self, account_email: str, offset: int = 0, *, query: str = "", cached_page: bool = False
        ) -> InboxPage:
            return InboxPage(
                account_email=account_email,
                messages=(make_message(provider_message_id="a"),),
                has_more=True,
            )

        async def browse_inbox(
            self,
            account_email: str,
            cancel: asyncio.Event,
            *,
            load_more: bool = False,
            query: str = "",
        ) -> InboxPage:
            calls.append((load_more, query))
            started.set()
            await release.wait()
            return InboxPage(
                account_email=account_email,
                query=query,
                cached=False,
                messages=(
                    make_message(provider_message_id="a"),
                    make_message(provider_message_id="b"),
                ),
                has_more=True,
            )

    window = MainWindow(Backend())
    qtbot.addWidget(window)
    window._set_account("owner@example.com")
    assert window.start(window._refresh_inbox)
    await started.wait()
    assert window.inbox_panel.messages.message_count() == 1
    assert not window.inbox_panel.next.isEnabled()
    release.set()
    await finish(window)
    window.inbox_panel.open_message("b")
    window.inbox_panel.next.click()
    await finish(window)
    assert window.inbox_panel.open_id() == "b"
    assert calls == [(False, ""), (True, "")]
    window.inbox_panel.mail_search.setText("from:old")
    window.inbox_panel.search_button.click()
    await finish(window)
    assert calls[-1] == (False, "from:old")
    window.inbox_panel.clear_search.click()
    await finish(window)
    assert calls[-1] == (False, "")


async def test_network_failure_keeps_rows(qtbot: QtBot) -> None:
    class Backend(FakeBackend):
        async def browse_inbox(
            self,
            account_email: str,
            cancel: asyncio.Event,
            *,
            load_more: bool = False,
            query: str = "",
        ) -> InboxPage:
            raise ProviderError("synthetic")

    window = MainWindow(Backend())
    qtbot.addWidget(window)
    window._set_account("owner@example.com")
    window.inbox_panel.show_page(
        InboxPage(account_email="owner@example.com", has_more=True, messages=(make_message(),))
    )
    window.inbox_panel.next.click()
    await finish(window)
    assert window.inbox_panel.messages.message_count() == 1
    assert "Couldn't load" in window.workspace.header.alert.accessibleDescription()
    assert window.inbox_panel.next.isEnabled()


def test_search_results_mark_non_inbox_and_render_plain_text(qtbot: QtBot) -> None:
    panel = InboxPanel()
    qtbot.addWidget(panel)
    panel.show_page(
        InboxPage(
            account_email="me@example.com",
            query="old",
            cached=False,
            messages=(make_message(subject="<b>Invoice</b>", is_in_inbox=False),),
        )
    )
    assert panel.open_message("message-1")
    assert "<b>Invoice</b>" in panel.reader.subject.text()
    assert any("Outside Inbox" in row.title for row in panel.messages.inbox_model.rows())
    assert not panel.next.isEnabled()
    assert not panel.clear_search.isHidden()


async def test_wrong_account_and_pre_cancel_do_not_list_mail(
    backend: DesktopRuntime,
    respx_mock: respx.MockRouter,
) -> None:
    with pytest.raises(AuthenticationRequiredError):
        await backend.browse_inbox("someone@example.com", asyncio.Event())
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(asyncio.CancelledError):
        await backend.browse_inbox("me@example.com", cancel)
    assert not respx_mock.calls
    assert not backend._inbox_browse
    with pytest.raises(ValueError, match="1,000"):
        await backend.browse_inbox("me@example.com", asyncio.Event(), query="x" * 1001)


async def test_commit_failure_cannot_advance_cursor(
    backend: DesktopRuntime,
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listing = respx_mock.get(MESSAGES_URL).respond(
        json={"messages": [{"id": "a"}], "nextPageToken": "older"}
    )
    respx_mock.get(MESSAGES_URL + "/a").respond(json=metadata("a"))
    original = AsyncSession.commit
    calls = 0

    async def commit(session: AsyncSession) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic commit failure")
        await original(session)

    monkeypatch.setattr(AsyncSession, "commit", commit)
    with pytest.raises(RuntimeError, match="commit failure"):
        await backend.browse_inbox("me@example.com", asyncio.Event())
    assert not backend._inbox_browse
    assert not (await backend.inbox_messages("me@example.com")).messages
    page = await backend.browse_inbox("me@example.com", asyncio.Event(), load_more=True)
    assert len(page.messages) == 1
    assert all("pageToken" not in call.request.url.params for call in listing.calls)


@pytest.mark.parametrize("cleanup", [False, True])
async def test_reset_during_fetch_cannot_restore_browsing_state(
    backend: DesktopRuntime,
    respx_mock: respx.MockRouter,
    cleanup: bool,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    respx_mock.get(MESSAGES_URL).respond(json={"messages": [{"id": "a"}]})

    async def hold(request: httpx.Request) -> httpx.Response:
        started.set()
        await release.wait()
        return httpx.Response(200, json=metadata("a"))

    respx_mock.get(MESSAGES_URL + "/a").mock(side_effect=hold)
    task = asyncio.create_task(backend.browse_inbox("me@example.com", asyncio.Event()))
    await started.wait()
    if cleanup:
        backend.reset_inbox_browsing()
    else:
        await backend._signed_out()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not backend._inbox_browse


async def test_initial_display_stays_50_with_large_cache_and_new_arrivals(
    backend: DesktopRuntime,
    respx_mock: respx.MockRouter,
) -> None:
    async with backend._storage().session() as session:
        account = await AccountRepository(session).upsert(ACCOUNT)
        await MessageRepository(session).upsert_messages(
            account.id,
            [
                make_message(
                    provider_message_id=f"old{i}", received_at_utc=NOW - timedelta(days=90)
                )
                for i in range(200)
            ],
        )
        await session.commit()
    assert len((await backend.inbox_messages("me@example.com")).messages) == 50
    listing = respx_mock.get(MESSAGES_URL).respond(
        json={
            "messages": [{"id": f"new{i}"} for i in range(50)],
            "nextPageToken": "older",
        }
    )
    for i in range(50):
        respx_mock.get(f"{MESSAGES_URL}/new{i}").respond(json=metadata(f"new{i}"))
    fresh = await backend.browse_inbox("me@example.com", asyncio.Event())
    assert len(fresh.messages) == 50
    assert all(message.provider_message_id.startswith("new") for message in fresh.messages)
    assert listing.call_count == 1
    async with backend._storage().session() as session:
        stored_account = await AccountRepository(session).get_by_id(account.id)
        assert stored_account is not None and stored_account.last_sync_at_utc is None
    older = await backend.inbox_messages("me@example.com", offset=100)
    assert len(older.messages) == 100 and older.has_more
    assert len((await backend.inbox_messages("me@example.com")).messages) == 150


@pytest.mark.parametrize("failed", [False, True])
async def test_stale_newer_cache_cannot_hide_fresh_first_page(
    backend: DesktopRuntime,
    respx_mock: respx.MockRouter,
    failed: bool,
) -> None:
    async with backend._storage().session() as session:
        account = await AccountRepository(session).upsert(ACCOUNT)
        await MessageRepository(session).upsert_messages(
            account.id,
            [make_message(provider_message_id=f"stale{i}", received_at_utc=NOW) for i in range(50)],
        )
        await session.commit()
    listing = respx_mock.get(MESSAGES_URL).mock(
        side_effect=[
            httpx.Response(200, json={"messages": [{"id": "fresh"}], "nextPageToken": "older"}),
            httpx.Response(200, json={"messages": [{"id": "fresh"}], "nextPageToken": "older"}),
        ]
    )
    respx_mock.get(MESSAGES_URL + "/fresh").mock(
        side_effect=[
            httpx.Response(
                200, json={} if failed else metadata("fresh", received=NOW - timedelta(days=90))
            ),
            httpx.Response(200, json=metadata("fresh", received=NOW - timedelta(days=90))),
        ]
    )
    if failed:
        with pytest.raises(ProviderResponseError):
            await backend.browse_inbox("me@example.com", asyncio.Event())
        first = await backend.browse_inbox("me@example.com", asyncio.Event(), load_more=True)
        assert "pageToken" not in listing.calls[1].request.url.params
    else:
        first = await backend.browse_inbox("me@example.com", asyncio.Event())
    assert [message.provider_message_id for message in first.messages] == ["fresh"]
    async with backend._storage().session() as session:
        rows, more = await MessageRepository(session).get_inbox_page(account.id)
        assert len(rows) == 51 and not more  # Cache retention is independent of display selection.


async def test_empty_fresh_first_page_does_not_present_cached_ghosts_as_results(
    backend: DesktopRuntime,
    respx_mock: respx.MockRouter,
) -> None:
    async with backend._storage().session() as session:
        account = await AccountRepository(session).upsert(ACCOUNT)
        await MessageRepository(session).upsert_messages(account.id, [make_message()])
        await session.commit()
    respx_mock.get(MESSAGES_URL).respond(json={"messages": []})
    fresh = await backend.browse_inbox("me@example.com", asyncio.Event())
    assert not fresh.messages and not fresh.has_more
    async with backend._storage().session() as session:
        rows, _ = await MessageRepository(session).get_inbox_page(account.id)
        assert len(rows) == 1


async def test_only_active_search_and_inbox_retain_pagination_history(
    backend: DesktopRuntime,
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(MESSAGES_URL).respond(json={"messages": []})
    await backend.browse_inbox("me@example.com", asyncio.Event())
    for query in ("first", "second", "third"):
        await backend.browse_inbox("me@example.com", asyncio.Event(), query=query)
        assert set(backend._inbox_browse) == {("me@example.com", ""), ("me@example.com", query)}
    # A background Inbox refresh must retain the visible search's continuation.
    await backend.browse_inbox("me@example.com", asyncio.Event())
    assert ("me@example.com", "third") in backend._inbox_browse


async def test_failed_search_can_return_to_inbox(qtbot: QtBot) -> None:
    class Backend(FakeBackend):
        async def browse_inbox(
            self,
            account_email: str,
            cancel: asyncio.Event,
            *,
            load_more: bool = False,
            query: str = "",
        ) -> InboxPage:
            if query:
                raise ProviderError("synthetic failure")
            return await super().browse_inbox(account_email, cancel)

    window = MainWindow(Backend())
    qtbot.addWidget(window)
    window._set_account("owner@example.com")
    window.inbox_panel.show_page(InboxPage(account_email="owner@example.com"))
    window.inbox_panel.mail_search.setText("invoice")
    window.inbox_panel.search_button.click()
    await finish(window)
    assert "Couldn't load mail" in window.workspace.header.alert.accessibleDescription()
    assert not window.inbox_panel.clear_search.isHidden()
    window.inbox_panel.clear_search.click()
    await finish(window)
    assert window.inbox_panel.mail_search.text() == ""
    assert window.inbox_panel.clear_search.isHidden()


def test_clear_resets_search_and_retry_button_labels(qtbot: QtBot) -> None:
    panel = InboxPanel()
    qtbot.addWidget(panel)
    panel.show_page(
        InboxPage(
            account_email="me@example.com",
            query="invoice",
            has_more=True,
            error_code="METADATA_ITEMS_FAILED",
        )
    )
    assert (
        panel.refresh.accessibleName() == "Refresh search"
        and panel.next.accessibleName() == "Retry page"
    )
    panel.clear()
    assert (
        panel.refresh.accessibleName() == "Refresh Inbox"
        and panel.next.accessibleName() == "Load 100 more"
    )
    assert panel.clear_search.isHidden()


async def test_reconnect_discards_old_browsing_cursors(
    backend: DesktopRuntime,
    respx_mock: respx.MockRouter,
) -> None:
    listing = respx_mock.get(MESSAGES_URL).respond(
        json={
            "messages": [{"id": "a"}],
            "nextPageToken": "older",
        }
    )
    respx_mock.get(MESSAGES_URL + "/a").respond(json=metadata("a"))
    await backend.browse_inbox("me@example.com", asyncio.Event())
    generation = backend._inbox_generation
    backend._legacy_checked = True  # Never touch the real OS vault in a test.
    assert await backend.connect(silent_only=False) == "me@example.com"
    assert not backend._inbox_browse and backend._inbox_generation > generation
    await backend.browse_inbox("me@example.com", asyncio.Event(), load_more=True)
    assert "pageToken" not in listing.calls[1].request.url.params
    assert listing.calls[1].request.url.params["maxResults"] == "50"


async def test_authentication_loss_clears_runtime_browsing_and_calendar_access(
    backend: DesktopRuntime,
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.get(MESSAGES_URL).mock(
        side_effect=[
            httpx.Response(200, json={"messages": [{"id": "a"}], "nextPageToken": "older"}),
            httpx.Response(401),
            httpx.Response(401),
        ]
    )
    respx_mock.get(MESSAGES_URL + "/a").respond(json=metadata("a"))
    await backend.browse_inbox("me@example.com", asyncio.Event())
    backend._calendar_allowed = True
    with pytest.raises(AuthenticationRequiredError):
        await backend.browse_inbox("me@example.com", asyncio.Event(), load_more=True)
    assert not backend._inbox_browse and await backend.calendar_allowed() is None
    saved = await backend.inbox_messages("me@example.com")
    assert saved.cached and len(saved.messages) == 1


@pytest.mark.parametrize("operation", ["connect", "startup", "automatic"])
async def test_workflows_stop_after_inbox_authentication_loss(qtbot: QtBot, operation: str) -> None:
    class Backend(FakeBackend):
        async def browse_inbox(
            self,
            account_email: str,
            cancel: asyncio.Event,
            *,
            load_more: bool = False,
            query: str = "",
        ) -> InboxPage:
            raise AuthenticationRequiredError("synthetic expiration")

    backend = Backend()
    window = MainWindow(backend)
    qtbot.addWidget(window)
    if operation == "connect":
        window.start(window._connect)
    elif operation == "startup":
        window.start(window.initialize)
    else:
        window._set_account("owner@example.com")
        window.start(window._automatic_refresh)
    await finish(window)
    await window.drain_calendar_reads()
    assert window._account_email is None
    assert window._calendar_allowed is None
    assert backend.automatic_calls == 0
    assert not backend.permission_accounts
    assert window.inbox_panel.messages.message_count() == 0


@pytest.mark.parametrize("continuation", [False, True])
async def test_all_metadata_failures_preserve_cached_display_and_retry_cursor(
    backend: DesktopRuntime,
    respx_mock: respx.MockRouter,
    continuation: bool,
) -> None:
    async with backend._storage().session() as session:
        account = await AccountRepository(session).upsert(ACCOUNT)
        await MessageRepository(session).upsert_messages(
            account.id, [make_message(provider_message_id="saved")]
        )
        await session.commit()
    initial = {"messages": [{"id": "a"}], "nextPageToken": "older"}
    subsequent = {"messages": [{"id": "b"}]}
    listing = respx_mock.get(MESSAGES_URL).mock(
        side_effect=[
            httpx.Response(200, json=initial),
            httpx.Response(200, json=subsequent if continuation else initial),
            *([httpx.Response(200, json=subsequent)] if continuation else []),
        ]
    )
    respx_mock.get(MESSAGES_URL + "/a").mock(
        side_effect=(
            [httpx.Response(200, json=metadata("a"))]
            if continuation
            else [httpx.Response(200, json={}), httpx.Response(200, json=metadata("a"))]
        )
    )
    if continuation:
        respx_mock.get(MESSAGES_URL + "/b").mock(
            side_effect=[
                httpx.Response(200, json={}),
                httpx.Response(200, json=metadata("b")),
            ]
        )
        await backend.browse_inbox("me@example.com", asyncio.Event())
    before = await backend.inbox_messages("me@example.com")
    with pytest.raises(ProviderResponseError, match="Retry the page"):
        await backend.browse_inbox("me@example.com", asyncio.Event(), load_more=continuation)
    assert await backend.inbox_messages("me@example.com") == before
    retry = await backend.browse_inbox("me@example.com", asyncio.Event(), load_more=True)
    assert {message.provider_message_id for message in retry.messages} == (
        {"a", "b"} if continuation else {"a"}
    )
    assert listing.calls[-1].request.url.params.get("pageToken") == (
        "older" if continuation else None
    )


@pytest.mark.parametrize("mode", [ThemeMode.LIGHT, ThemeMode.DARK])
def test_mail_search_gallery(qtbot: QtBot, themed: None, mode: ThemeMode) -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    assert isinstance(app, QApplication)
    apply_theme(app, mode=mode)
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    window.inbox_panel.show_page(
        InboxPage(
            account_email="owner@example.com",
            query="from:billing@example.com invoice",
            cached=False,
            has_more=True,
            messages=(
                make_message(
                    provider_message_id="invoice",
                    subject="Your September invoice",
                    is_in_inbox=False,
                ),
                make_message(provider_message_id="receipt", subject="Receipt for your payment"),
            ),
        )
    )
    window._show_page("inbox")
    window.resize(1100, 720)
    window.show()
    qtbot.waitExposed(window)
    directory = os.environ.get("MAILBRIEF_UI_SHOTS")
    if directory:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        assert window.grab().save(
            str(target / f"mail-search-{mode}-{window.devicePixelRatioF():g}x.png")
        )


@pytest.mark.parametrize("position", [0, 49, 50, 99, 100, 150])
@pytest.mark.parametrize("browsed", [False, True])
async def test_palette_jump_selects_actual_cached_message(
    backend: DesktopRuntime,
    qtbot: QtBot,
    position: int,
    browsed: bool,
) -> None:
    async with backend._storage().session() as session:
        account = await AccountRepository(session).upsert(ACCOUNT)
        await MessageRepository(session).upsert_messages(
            account.id,
            [
                make_message(
                    provider_message_id=f"m{i}", received_at_utc=NOW - timedelta(minutes=i)
                )
                for i in range(160)
            ],
        )
        await session.commit()
    if browsed:
        backend._inbox_browse[("me@example.com", "")] = runtime.InboxBrowse(
            identifiers={"m159"}, continuation="older"
        )
    window = MainWindow(backend)
    qtbot.addWidget(window)
    window._set_account("me@example.com")
    await window._open_mail(f"m{position}", None)
    page = window.inbox_panel.page
    assert page is not None
    assert window.inbox_panel.open_id() == f"m{position}"
    selected = window.inbox_panel.messages.current_message()
    assert selected is not None and selected.provider_message_id == f"m{position}"
    assert len(page.messages) == (100 if position < 100 else 60)
    if browsed:
        assert backend._inbox_browse[("me@example.com", "")].continuation == "older"


@pytest.mark.parametrize("kind", ["all-cache", "account"])
@pytest.mark.parametrize("refresh_fails", [False, True])
async def test_live_cleanup_restarts_inbox_and_search_from_first_provider_page(
    backend: DesktopRuntime,
    qtbot: QtBot,
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    refresh_fails: bool,
) -> None:
    listing = respx_mock.get(MESSAGES_URL).respond(
        json={"messages": [{"id": "a"}], "nextPageToken": "old-cursor"}
    )
    respx_mock.get(MESSAGES_URL + "/a").respond(json=metadata("a"))
    for query in ("", "invoice"):
        await backend.browse_inbox("me@example.com", asyncio.Event(), query=query)
    window = MainWindow(backend, database_path=backend._path)
    qtbot.addWidget(window)
    window._set_account("me@example.com")
    window.inbox_panel.show_page(await backend.inbox_messages("me@example.com"))
    window._ready = True
    await window._open_data()
    dialog = window.data_dialog
    assert dialog is not None
    dialog.kind.setCurrentIndex(dialog.kind.findData(kind))
    monkeypatch.setattr(dialog, "confirm", AsyncMock(return_value=True))
    if refresh_fails:
        monkeypatch.setattr(backend, "load_saved", AsyncMock(side_effect=RuntimeError("synthetic")))
    generation = backend._inbox_generation
    await dialog.cleanup()
    assert not backend._inbox_browse
    assert backend._inbox_generation > generation
    assert window.inbox_panel.page is None
    for query in ("", "invoice"):
        await backend.browse_inbox("me@example.com", asyncio.Event(), load_more=True, query=query)
    for call in listing.calls[-2:]:
        assert "pageToken" not in call.request.url.params
        assert call.request.url.params["maxResults"] == "50"
    dialog.reject()


@pytest.mark.parametrize("queries", [("", "invoice"), ("invoice", "receipt"), ("invoice", "")])
def test_query_changes_reset_selection_and_scroll(qtbot: QtBot, queries: tuple[str, str]) -> None:
    panel = InboxPanel()
    qtbot.addWidget(panel)
    panel.resize(700, 500)
    panel.show()
    messages = tuple(make_message(provider_message_id=f"m{i}") for i in range(80))
    panel.show_page(InboxPage(account_email="me@example.com", query=queries[0], messages=messages))
    panel.open_message("m70")
    panel.messages.scrollToBottom()
    assert panel.messages.verticalScrollBar().value() > 0
    panel.show_page(InboxPage(account_email="me@example.com", query=queries[1], messages=messages))
    assert panel.open_id() is None
    assert panel.messages.current_message() is None
    assert panel.messages.verticalScrollBar().value() == 0
    panel.open_message("m50")
    panel.messages.scrollToBottom()
    scroll = panel.messages.verticalScrollBar().value()
    panel.show_page(InboxPage(account_email="me@example.com", query=queries[1], messages=messages))
    assert panel.open_id() == "m50"
    assert panel.messages.verticalScrollBar().value() == scroll
