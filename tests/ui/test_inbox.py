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
from mailbrief.ui.brief_list import ROW_ROLE
from mailbrief.ui.inbox_view import UNWANT, WANT, WANTED, WANTED_NOTE, InboxPanel, wanted_line
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
                make_message(
                    provider_message_id="wanted",
                    subject="Your room booking needs a reply",
                    web_link="https://mail.google.com/mail/u/0/#inbox/wanted",
                ),
            ),
            has_more=True,
            wanted_ids=frozenset({"wanted"}),
            wanted_recently=2,
        )
    )
    window.inbox_panel.messages.setCurrentRow(1)
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


def test_inbox_zone_changes_keep_selection_and_page(qtbot: QtBot) -> None:
    from zoneinfo import ZoneInfo

    from mailbrief.domain.preferences import OwnerPreferences

    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    panel = window.inbox_panel
    window._apply_owner_preferences(OwnerPreferences(revision=0, time_zone="UTC"))
    panel.show_page(
        InboxPage(
            account_email="owner@example.com",
            offset=100,
            has_more=True,
            messages=(
                make_message(provider_message_id="one"),
                make_message(provider_message_id="two"),
            ),
        )
    )
    panel.messages.setCurrentRow(1)
    before = panel.details.text()
    window._apply_owner_preferences(OwnerPreferences(revision=0, time_zone="America/Toronto"))
    assert panel.zone == ZoneInfo("America/Toronto")
    assert panel.messages.currentRow() == 1
    assert panel.details.text() != before
    assert panel._page is not None and panel._page.offset == 100 and panel._page.has_more
    panel.messages.setCurrentRow(0)
    after = panel.details.text()
    panel.set_zone(ZoneInfo("UTC"))
    assert panel.details.text() != after


def two_messages(*, wanted: frozenset[str] = frozenset(), count: int = 0) -> InboxPage:
    return InboxPage(
        account_email="owner@example.com",
        offset=100,
        messages=(
            make_message(provider_message_id="one", subject="First"),
            make_message(provider_message_id="two", subject="Second"),
        ),
        wanted_ids=wanted,
        wanted_recently=count,
    )


def test_the_mark_button_asks_to_mark_or_unmark_the_selected_message(qtbot: QtBot) -> None:
    panel = InboxPanel()
    qtbot.addWidget(panel)
    asked: list[tuple[str, bool]] = []
    panel.wanted_requested.connect(lambda key, wanted: asked.append((key, wanted)))

    panel.show_page(two_messages(wanted=frozenset({"two"}), count=3))

    assert panel.wanted.text() == WANT and panel.wanted.isEnabled()
    assert WANTED_NOTE not in panel.details.text()
    assert panel.wanted_status.text() == wanted_line(3)
    assert "last 14 days: 3" in panel.wanted_status.text()
    panel.wanted.click()
    panel.messages.setCurrentRow(1)
    assert panel.wanted.text() == UNWANT
    assert WANTED_NOTE in panel.details.text()
    assert panel.messages.item(1).text().endswith(f"— {WANTED}")
    row = panel.messages.item(1).data(ROW_ROLE)
    assert [chip.text for chip in row.chips] == [WANTED]
    panel.wanted.click()

    assert asked == [("one", True), ("two", False)]
    panel.set_busy(True)
    assert not panel.wanted.isEnabled()
    panel.clear()
    assert not panel.wanted.isEnabled() and not panel.wanted_status.text()


def test_show_page_selects_the_message_asked_for(qtbot: QtBot) -> None:
    panel = InboxPanel()
    qtbot.addWidget(panel)
    panel.show_page(two_messages(), select="two")
    assert panel.messages.currentRow() == 1
    panel.show_page(two_messages(), select="gone")
    assert panel.messages.currentRow() == 0


async def test_marking_a_message_saves_it_and_keeps_it_selected(qtbot: QtBot) -> None:
    class Backend(FakeBackend):
        async def inbox_messages(self, account_email: str, offset: int = 0) -> InboxPage:
            marked = frozenset(key for _, key, wanted in self.wanted_marks if wanted)
            return two_messages(wanted=marked, count=len(marked)).model_copy(
                update={"offset": offset}
            )

    backend = Backend()
    window = MainWindow(backend)
    qtbot.addWidget(window)
    window._set_account("owner@example.com")
    await window._load_inbox(100)
    window.inbox_panel.messages.setCurrentRow(1)

    window.inbox_panel.wanted.click()
    await finish(window)

    assert backend.wanted_marks == [("owner@example.com", "two", True)]
    assert window.inbox_panel.messages.currentRow() == 1
    assert window.inbox_panel.wanted.text() == UNWANT
    assert window.inbox_panel.page is not None and window.inbox_panel.page.offset == 100
    assert "counts this" in window.status.text()

    window.inbox_panel.wanted.click()
    await finish(window)
    assert backend.wanted_marks[-1] == ("owner@example.com", "two", False)
    assert window.status.text() == "Mark removed."


async def test_runtime_marks_stay_local_and_count_for_the_inbox_page(
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
        async with httpx.AsyncClient() as http:
            yield GmailProvider(FakeSession(), GmailClient(http, FakeSession()))

    monkeypatch.setattr(runtime, "gmail_provider", provider)
    backend = DesktopRuntime(tmp_path / "mailbrief.sqlite3")
    await backend.load_saved()
    try:
        result = await backend.sync_inbox(asyncio.Event(), lambda progress: None)
        calls = len(respx_mock.calls)
        await backend.set_wanted(result.account_id, "old", True)
        page = await backend.inbox_messages(result.account_id)
        assert page.wanted_ids == frozenset({"old"})
        assert page.wanted_recently == 1
        await backend.set_wanted(result.account_id, "old", False)
        page = await backend.inbox_messages(result.account_id)
        assert page.wanted_ids == frozenset() and page.wanted_recently == 0
        with pytest.raises(ValueError):
            await backend.set_wanted("another@example.com", "old", True)
        assert len(respx_mock.calls) == calls  # Gmail is never told.
    finally:
        await backend.close()
