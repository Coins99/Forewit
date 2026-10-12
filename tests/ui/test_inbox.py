"""Sign-in sync, all-date metadata, account isolation and safe Inbox rendering."""

import asyncio
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
from pytestqt.qtbot import QtBot

from mailbrief.config import Settings
from mailbrief.domain.cached_mail import InboxPage
from mailbrief.domain.digests import SyncProgress, SyncResult
from mailbrief.domain.messages import ProviderKind
from mailbrief.errors import ConfigurationError
from mailbrief.ports.errors import AuthenticationRequiredError
from mailbrief.providers.gmail.client import MESSAGES_URL, GmailClient
from mailbrief.providers.gmail.provider import GmailProvider
from mailbrief.ui import runtime
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.runtime import DesktopRuntime
from mailbrief.ui.theme import apply_theme
from mailbrief.ui.theme.tokens import ThemeMode
from tests.factories import make_message
from tests.ui.test_workflow import FakeBackend, finish
from tests.unit.providers.gmail.metadata_fixtures import FakeSession, metadata


async def test_runtime_full_inbox_sync_has_no_body_or_ai_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, respx_mock: respx.MockRouter
) -> None:
    respx_mock.get(MESSAGES_URL).respond(json={"messages": [{"id": "old"}, {"id": "new"}]})
    respx_mock.get(MESSAGES_URL + "/old").respond(
        json=metadata("old", received=datetime(2024, 1, 1, tzinfo=UTC))
    )
    respx_mock.get(MESSAGES_URL + "/new").respond(
        json=metadata("new", received=datetime(2026, 10, 12, tzinfo=UTC))
    )

    @asynccontextmanager
    async def provider(settings: Settings, *, silent_only: bool) -> AsyncIterator[GmailProvider]:
        assert silent_only
        async with httpx.AsyncClient() as http:
            yield GmailProvider(FakeSession(), GmailClient(http, FakeSession()))

    monkeypatch.setattr(runtime, "gmail_provider", provider)
    backend = DesktopRuntime(tmp_path / "mailbrief.sqlite3")
    await backend.load_saved()
    try:
        result = await backend.sync_inbox(asyncio.Event(), lambda progress: None)
        assert result.message_count == 2
        page = await backend.inbox_messages(result.account_id)
        assert [m.provider_message_id for m in page.messages] == ["new", "old"]
        assert not (await backend.inbox_messages("another@example.com")).messages
        assert (await backend.inbox_messages(result.account_id, 100)).offset == 100
    finally:
        await backend.close()
    assert all(call.request.url.params.get("format") != "full" for call in respx_mock.calls)


async def test_signin_syncs_inbox_and_disconnect_clears_account(qtbot: QtBot) -> None:
    calls: list[str] = []

    class Backend(FakeBackend):
        async def sync_inbox(
            self, cancel: asyncio.Event, progress: Callable[[SyncProgress], None]
        ) -> SyncResult:
            calls.append("inbox")
            return await super().sync_inbox(cancel, progress)

        async def inbox_messages(self, account_email: str, offset: int = 0) -> InboxPage:
            calls.append(account_email)
            return InboxPage(
                account_email=account_email,
                messages=(
                    make_message(
                        provider=ProviderKind.GMAIL,
                        subject="<b>Old message</b>",
                        web_link="https://mail.google.com/mail/u/0/#inbox/old",
                        received_at_utc=datetime(2024, 1, 1, tzinfo=UTC),
                    ),
                ),
            )

    window = MainWindow(Backend())
    qtbot.addWidget(window)
    window.connect_button.click()
    await finish(window)
    assert calls == ["inbox", "owner@example.com"]
    assert window.workspace.current_page() == "inbox"
    assert "<b>Old message</b>" in window.inbox_panel.details.text()
    assert window.inbox_panel.messages.count() == 1
    window.disconnect_button.click()
    await finish(window)
    assert window.inbox_panel.messages.count() == 0
    assert "Sign in" in window.inbox_panel.status.text()


@pytest.mark.parametrize(
    "failure",
    [ConfigurationError("Google setup missing."), AuthenticationRequiredError("synthetic")],
)
async def test_failed_setup_does_not_leave_a_connected_indicator(
    qtbot: QtBot, failure: Exception
) -> None:
    class Backend(FakeBackend):
        async def sync_inbox(
            self, cancel: asyncio.Event, progress: Callable[[SyncProgress], None]
        ) -> SyncResult:
            raise failure

    window = MainWindow(Backend())
    qtbot.addWidget(window)
    window._set_account("owner@example.com")
    await window._refresh_inbox()
    assert window._account_email is None
    assert not window.connect_button.isHidden()
    assert window.inbox_panel.messages.count() == 0
    assert window.inbox_panel.sync_status.text()


@pytest.mark.parametrize("mode", [ThemeMode.DARK, ThemeMode.LIGHT])
def test_inbox_gallery(qtbot: QtBot, themed: None, mode: ThemeMode) -> None:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance()
    assert isinstance(app, QApplication)
    window = MainWindow(FakeBackend())
    apply_theme(app, mode=mode)
    qtbot.addWidget(window)
    window.inbox_panel.show_page(
        InboxPage(
            account_email="owner@example.com",
            messages=(
                make_message(
                    subject="Inbox message received last year",
                    web_link="https://mail.google.com/mail/u/0/#inbox/old",
                ),
            ),
            has_more=True,
        )
    )
    window._show_page("inbox")
    window.resize(1100, 720)
    window.show()
    qtbot.waitExposed(window)
    assert window.inbox_panel.isVisible()
    directory = os.environ.get("MAILBRIEF_UI_SHOTS")
    if directory:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        assert window.grab().save(str(target / f"inbox-{mode}-{window.devicePixelRatioF():g}x.png"))
