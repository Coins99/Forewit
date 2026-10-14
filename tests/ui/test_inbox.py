"""Sign-in sync, all-date metadata, account isolation and safe Inbox rendering: the list
across the page, the list beside the reading pane, and the reading pane alone."""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QBoxLayout
from pytestqt.qtbot import QtBot

from mailbrief.config import Settings
from mailbrief.domain.cached_mail import InboxPage
from mailbrief.domain.digests import SyncProgress, SyncResult, SyncStatus
from mailbrief.domain.messages import EmailContact, NormalizedMessage, ProviderKind
from mailbrief.domain.preferences import OwnerPreferences
from mailbrief.errors import ConfigurationError
from mailbrief.ports.errors import AuthenticationRequiredError
from mailbrief.providers.gmail.client import MESSAGES_URL, GmailClient
from mailbrief.providers.gmail.provider import GmailProvider
from mailbrief.ui import runtime
from mailbrief.ui.brief_list import ROW_ROLE
from mailbrief.ui.inbox_list import Density, InboxRow
from mailbrief.ui.inbox_view import (
    INCOMPLETE,
    LIST_MIN,
    NO_MESSAGES,
    READER_MIN,
    UNWANT,
    WANT,
    WANTED,
    InboxPanel,
    range_text,
    wanted_line,
    wanted_short,
)
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.runtime import DesktopRuntime
from mailbrief.ui.theme import apply_theme
from mailbrief.ui.theme.tokens import ThemeMode
from tests.factories import make_message
from tests.ui.test_main_window_layout import save_shot
from tests.ui.test_workflow import FakeBackend, finish
from tests.unit.providers.gmail.metadata_fixtures import FakeSession, metadata

# 17:00 in Toronto on Tue Oct 13, 2026.
NOW = datetime(2026, 10, 13, 21, 0, tzinfo=UTC)
TORONTO = ZoneInfo("America/Toronto")


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
        assert page.total == 2
        other = await backend.inbox_messages("another@example.com")
        assert not other.messages and other.total == 0
        later = await backend.inbox_messages(result.account_id, 100)
        assert later.offset == 100 and not later.messages and later.total == 2
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
                total=1,
            )

    window = MainWindow(Backend())
    qtbot.addWidget(window)
    window.connect_button.click()
    await finish(window)
    assert calls == ["inbox", "owner@example.com"]
    assert window.workspace.current_page() == "inbox"
    panel = window.inbox_panel
    assert panel.mode() == "list"  # The Inbox starts on its list, nothing open.
    assert panel.messages.message_count() == 1
    rows = panel.messages.inbox_model.rows()
    assert [row.kind for row in rows] == ["header", "message"]
    assert rows[1].title == "<b>Old message</b>"  # Plain text, drawn as is.
    assert panel.open_message("message-1")
    assert panel.reader.subject.text() == "<b>Old message</b>"
    window.disconnect_button.click()
    await finish(window)
    assert panel.messages.message_count() == 0 and panel.open_id() is None
    assert "Sign in" in panel.empty_message()


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
    assert window.inbox_panel.messages.message_count() == 0
    # The guidance shows once, where the list would be; the header has no alert.
    assert window.inbox_panel.empty_message() == window.status.text()
    assert window.workspace.header.alert.isHidden()


def incomplete(code: str | None) -> SyncResult:
    return SyncResult(
        account_id="owner@example.com",
        range_start_utc=datetime(2026, 9, 1, tzinfo=UTC),
        range_end_utc=datetime(2026, 10, 1, tzinfo=UTC),
        status=SyncStatus.PARTIAL,
        page_count=1,
        message_count=0,
        error_code=code,
    )


async def test_an_incomplete_sync_shows_once_in_the_header(qtbot: QtBot) -> None:
    results = [incomplete("TIMEOUT"), incomplete("TIMEOUT")]

    class Backend(FakeBackend):
        async def sync_inbox(
            self, cancel: asyncio.Event, progress: Callable[[SyncProgress], None]
        ) -> SyncResult:
            return results.pop(0) if results else await super().sync_inbox(cancel, progress)

    window = MainWindow(Backend())
    qtbot.addWidget(window)
    window._set_account("owner@example.com")
    alert = window.workspace.header.alert
    window.status.setText("Ready.")

    await window._refresh_inbox(quiet=True)  # At launch: the header alone says so.
    assert not alert.isHidden() and alert.text.text() == INCOMPLETE
    assert alert.accessibleDescription().startswith("Inbox sync incomplete. Cached")
    assert alert.toolTip() == alert.accessibleDescription()
    assert window.status.text() == "Ready."

    await window._refresh_inbox()  # The owner asked: the status strip answers too.
    assert window.status.text() == alert.accessibleDescription()

    await window._refresh_inbox()  # Complete: the alert goes.
    assert alert.isHidden()
    assert window.status.text().startswith("Inbox synced:")


async def test_a_lost_sign_in_clears_the_alert_and_says_so_in_the_page(qtbot: QtBot) -> None:
    class Backend(FakeBackend):
        async def sync_inbox(
            self, cancel: asyncio.Event, progress: Callable[[SyncProgress], None]
        ) -> SyncResult:
            return incomplete("AUTH_REQUIRED")

    window = MainWindow(Backend())
    qtbot.addWidget(window)
    window._set_account("owner@example.com")
    window.workspace.header.set_alert(INCOMPLETE, "Earlier.")
    await window._refresh_inbox(quiet=True)
    assert window._account_email is None
    assert window.workspace.header.alert.isHidden()
    assert window.inbox_panel.empty_message() == (
        "session expired. Sign in with Google to continue."
    )
    assert window.status.text().startswith("Inbox sync incomplete.")


def gallery_mail(
    key: str,
    name: str,
    address: str,
    subject: str,
    received: datetime,
    preview: str,
    **extra: object,
) -> NormalizedMessage:
    values: dict[str, object] = {
        "provider": ProviderKind.GMAIL,
        "provider_message_id": key,
        "sender": EmailContact(name=name, address=address),
        "subject": subject,
        "received_at_utc": received,
        "body_preview": preview,
        "web_link": f"https://mail.google.com/mail/u/0/#inbox/{key}",
        "is_read": True,
    }
    values.update(extra)
    return make_message(**values)


def gallery_page() -> InboxPage:
    """A page with mail in every section, read and unread, with each mark."""
    messages = (
        gallery_mail(
            "budget",
            "Priya Shah",
            "priya@example.com",
            "Q3 budget: approval needed by Friday",
            datetime(2026, 10, 13, 20, 25, tzinfo=UTC),
            "Hi, the revised Q3 budget is attached. Two line items changed since last "
            "month, so I need your approval before Friday's finance review. The biggest "
            "change is travel, which moved to the events line.",
            is_read=False,
            has_attachments=True,
        ),
        gallery_mail(
            "ci",
            "Build bot",
            "ci@example.com",
            "[MailBrief] Run failed: CI - main (c57d30e)",
            datetime(2026, 10, 13, 15, 26, tzinfo=UTC),
            "CI workflow run: some jobs were not successful. Lint, type and test "
            "(macos-latest) failed in 10 minutes and 48 seconds.",
        ),
        gallery_mail(
            "renewal",
            "Lena Park",
            "lena@example.com",
            "Contract renewal options",
            datetime(2026, 10, 13, 13, 11, tzinfo=UTC),
            "Following our call, here are the three renewal options with pricing for "
            "each. Happy to walk through them on Thursday.",
            is_read=False,
        ),
        gallery_mail(
            "closed",
            "Facilities",
            "facilities@example.com",
            "Office closed Monday",
            datetime(2026, 10, 12, 21, 40, tzinfo=UTC),
            "The office is closed on Monday for the holiday. Badge access stays on.",
            is_starred=True,
        ),
        gallery_mail(
            "deck",
            "Marco Diaz",
            "marco@example.com",
            "Re: Conference deck",
            datetime(2026, 10, 12, 14, 5, tzinfo=UTC),
            "Thanks! I tightened slides 4 to 7 and attached the new version. Can you "
            "check the timeline on slide 9?",
            has_attachments=True,
        ),
        gallery_mail(
            "invoice",
            "Sam Okafor",
            "sam@example.com",
            "Invoice 2041",
            datetime(2026, 10, 10, 18, 30, tzinfo=UTC),
            "Invoice 2041 for September is attached. Payment is due on October 20.",
            has_attachments=True,
        ),
        gallery_mail(
            "jobs",
            "Job alerts",
            "jobs@example.com",
            "New engineering job matches",
            datetime(2026, 10, 8, 12, 0, tzinfo=UTC),
            "Electrical component engineering intern, renewable energy engineer and "
            "four more roles near you.",
        ),
        gallery_mail(
            "sale",
            "Air travel deals",
            "deals@example.com",
            "Sunshine is on sale",
            datetime(2026, 10, 1, 14, 0, tzinfo=UTC),
            "Save on flights south this winter when you book by Sunday.",
        ),
        gallery_mail(
            "plans",
            "Taylor Brooks",
            "taylor@example.com",
            "Weekend plans?",
            datetime(2026, 9, 20, 16, 45, tzinfo=UTC),
            "Are you around on Saturday? A few of us are heading to the market.",
        ),
        gallery_mail(
            "statement",
            "Bank",
            "statements@example.com",
            "Your September statement is ready",
            datetime(2026, 9, 5, 11, 0, tzinfo=UTC),
            "Your statement for the period ending September 4 is ready to view.",
        ),
        gallery_mail(
            "library",
            "Library",
            "library@example.com",
            "Your loan is due soon",
            datetime(2025, 12, 2, 15, 0, tzinfo=UTC),
            "The book you borrowed is due back on December 9.",
        ),
    )
    return InboxPage(
        account_email="owner@example.com",
        messages=messages,
        total=11,
        wanted_ids=frozenset({"budget", "invoice"}),
        wanted_recently=2,
    )


def gallery_window(qtbot: QtBot) -> MainWindow:
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    window.now = lambda: NOW
    window._apply_owner_preferences(OwnerPreferences(revision=0, time_zone="America/Toronto"))
    window._set_account("owner@example.com")
    window.inbox_panel.show_page(gallery_page())
    window._show_page("inbox")
    return window


@pytest.mark.parametrize("mode", [ThemeMode.DARK, ThemeMode.LIGHT])
def test_inbox_gallery(qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode) -> None:
    apply_theme(qapp, mode=mode)
    window = gallery_window(qtbot)
    panel = window.inbox_panel
    window.resize(1100, 720)
    window.show()
    qtbot.waitExposed(window)
    assert panel.mode() == "list"
    assert panel.messages.density() is Density.WIDE
    save_shot(window.grab(), f"inbox-list-{mode}")

    panel.open_message("budget")
    window.workspace.header.set_alert(
        INCOMPLETE, "Inbox sync incomplete. Cached messages may be stale."
    )
    qtbot.waitUntil(lambda: panel.reader.isVisible())
    split = panel.width() >= LIST_MIN + READER_MIN + panel.split.handleWidth()
    assert panel.mode() == ("split" if split else "reader")
    save_shot(window.grab(), f"inbox-split-{mode}")

    window.resize(680, 520)
    qtbot.waitUntil(lambda: panel.width() < 700)
    assert panel.mode() == "reader"  # Too narrow for both: the reading pane takes the page.
    assert not panel.list_column.isVisible()
    assert panel.reader.width() == panel.width()
    save_shot(window.grab(), f"inbox-reader-{mode}")


def two_messages(*, wanted: frozenset[str] = frozenset(), count: int = 0) -> InboxPage:
    return InboxPage(
        account_email="owner@example.com",
        offset=100,
        messages=(
            make_message(provider_message_id="one", subject="First"),
            make_message(provider_message_id="two", subject="Second"),
        ),
        has_more=True,
        total=202,
        wanted_ids=wanted,
        wanted_recently=count,
    )


def shown_panel(qtbot: QtBot, width: int = 1000) -> InboxPanel:
    panel = InboxPanel(clock=lambda: NOW)
    qtbot.addWidget(panel)
    panel.resize(width, 600)
    panel.show()
    qtbot.waitExposed(panel)
    return panel


def row_of(panel: InboxPanel, key: str) -> int:
    number = panel.messages.inbox_model.row_of(key)
    assert number is not None
    return number


def test_the_inbox_opens_a_message_beside_the_list_and_closes_it(qtbot: QtBot) -> None:
    panel = shown_panel(qtbot)
    panel.show_page(two_messages())
    assert panel.mode() == "list" and not panel.reader.isVisible()
    assert panel.messages.density() is Density.WIDE

    index = panel.messages.inbox_model.index(row_of(panel, "two"))
    QTest.mouseClick(
        panel.messages.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
        panel.messages.visualRect(index).center(),
    )
    assert panel.open_id() == "two" and panel.mode() == "split"
    assert panel.reader.isVisible() and panel.list_column.isVisible()
    assert panel.messages.density() is Density.COMPACT
    assert LIST_MIN <= panel.split.sizes()[0] <= 400
    assert panel.reader.subject.text() == "Second"

    # The reading pane follows the list's current row.
    panel.messages.setFocus()
    QTest.keyClick(panel.messages, Qt.Key.Key_Up)
    assert panel.open_id() == "one" and panel.reader.subject.text() == "First"

    # Escape goes back to the list, its place kept.
    QTest.keyClick(panel.messages, Qt.Key.Key_Escape)
    assert panel.mode() == "list" and panel.open_id() is None
    assert not panel.reader.isVisible()
    assert panel.messages.density() is Density.WIDE
    current = panel.messages.current_message()
    assert current is not None and current.provider_message_id == "one"

    # On the list, moving opens nothing; Return opens the current message.
    QTest.keyClick(panel.messages, Qt.Key.Key_Down)
    assert panel.open_id() is None
    QTest.keyClick(panel.messages, Qt.Key.Key_Return)
    assert panel.open_id() == "two"

    panel.reader.close_button.click()
    assert panel.mode() == "list"


def test_a_narrow_page_gives_the_reading_pane_the_page(qtbot: QtBot) -> None:
    panel = shown_panel(qtbot, width=LIST_MIN + READER_MIN - 20)
    panel.show_page(two_messages())
    assert panel.open_message("one")
    assert panel.mode() == "reader"
    assert panel.reader.isVisible() and not panel.list_column.isVisible()
    assert panel.reader.width() == panel.width()  # The whole page, no empty list slot.
    panel.resize(1000, 600)
    qtbot.waitUntil(lambda: panel.mode() == "split")
    assert panel.list_column.isVisible()
    panel.resize(LIST_MIN + READER_MIN - 20, 600)
    qtbot.waitUntil(lambda: panel.mode() == "reader")
    panel.reader.source.setFocus()
    QTest.keyClick(panel.reader.source, Qt.Key.Key_Escape)
    assert panel.mode() == "list" and panel.list_column.isVisible()
    assert panel.window().focusWidget() is panel.messages


def test_the_owner_s_divider_position_is_kept(qtbot: QtBot) -> None:
    panel = shown_panel(qtbot, width=1200)
    panel.show_page(two_messages())
    panel.open_message("one")
    default = panel.split.sizes()[0]
    assert default == 400  # 38% of 1200 px, at most 400.
    panel.split.moveSplitter(560, 1)
    panel._divider_moved(560, 1)
    chosen = panel.split.sizes()[0]
    assert chosen != default
    panel.close_message()
    panel.open_message("two")
    assert panel.split.sizes()[0] == chosen


def test_the_mark_button_asks_to_mark_or_unmark_the_open_message(qtbot: QtBot) -> None:
    panel = InboxPanel()
    qtbot.addWidget(panel)
    asked: list[tuple[str, bool]] = []
    panel.wanted_requested.connect(lambda key, wanted: asked.append((key, wanted)))

    panel.show_page(two_messages(wanted=frozenset({"two"}), count=3))
    assert not panel.wanted.isEnabled()  # Nothing open yet.
    assert panel.wanted_status.text() == wanted_line(3)
    assert panel.wanted_status.label.text() == wanted_short(3)
    assert "last 14 days: 3" in panel.wanted_status.toolTip()

    panel.open_message("one")
    assert panel.wanted.text() == WANT and panel.wanted.isEnabled()
    assert panel.reader.wanted_card.isHidden()
    panel.wanted.click()
    panel.open_message("two")
    assert panel.wanted.text() == UNWANT
    assert not panel.reader.wanted_card.isHidden()
    row = panel.messages.inbox_model.rows()[row_of(panel, "two")]
    assert isinstance(row, InboxRow) and row.wanted
    index = panel.messages.inbox_model.index(row_of(panel, "two"))
    assert WANTED in index.data(Qt.ItemDataRole.AccessibleTextRole)
    assert index.data(ROW_ROLE) is row
    panel.wanted.click()

    assert asked == [("one", True), ("two", False)]
    panel.set_busy(True)
    assert not panel.wanted.isEnabled() and not panel.source.isEnabled()
    assert not panel.refresh.isEnabled() and panel.search.isEnabled()
    panel.set_busy(False)
    assert panel.wanted.isEnabled()
    panel.clear()
    assert not panel.wanted.isEnabled() and not panel.wanted_status.text()
    assert panel.wanted_status.isHidden()


def test_show_page_opens_the_message_asked_for(qtbot: QtBot) -> None:
    panel = InboxPanel()
    qtbot.addWidget(panel)
    panel.show_page(two_messages(), select="two")
    assert panel.open_id() == "two"
    current = panel.messages.current_message()
    assert current is not None and current.provider_message_id == "two"
    panel.show_page(two_messages())  # The same page again: still open.
    assert panel.open_id() == "two"
    panel.show_page(two_messages(), select="gone")
    assert panel.open_id() is None and panel.mode() == "list"


def test_the_page_s_header_and_footer(qtbot: QtBot) -> None:
    panel = InboxPanel()
    qtbot.addWidget(panel)
    pages: list[int] = []
    searches: list[bool] = []
    panel.page_requested.connect(pages.append)
    panel.search_requested.connect(lambda: searches.append(True))
    panel.show_page(two_messages())
    assert panel.title.count() == 202
    assert panel.title.accessibleName() == "Inbox, 202 messages"
    assert panel.range.text() == "101–102 of 202"
    assert panel.previous.isEnabled() and panel.next.isEnabled()
    panel.previous.click()
    panel.next.click()
    panel.search.click()
    assert pages == [0, 200] and searches == [True]
    for button in (panel.search, panel.refresh, panel.previous, panel.next):
        assert button.text() == "" and button.accessibleName()  # Icons, named for readers.

    panel.show_page(InboxPage(account_email="owner@example.com"))
    assert panel.empty_message() == NO_MESSAGES and panel.footer.isHidden()
    assert not panel.previous.isEnabled() and not panel.next.isEnabled()
    panel.clear()
    assert "Sign in" in panel.empty_message() and panel.title.count() is None


def test_range_text_counts_from_one() -> None:
    page = two_messages()
    assert range_text(page) == "101–102 of 202"
    assert range_text(page.model_copy(update={"total": 0})) == "101–102 of 102"
    assert range_text(InboxPage(account_email="owner@example.com")) == ""
    many = InboxPage(
        account_email="owner@example.com",
        offset=1000,
        messages=(make_message(),),
        total=9733,
    )
    assert range_text(many) == "1,001–1,001 of 9,733"


def test_inbox_zone_changes_keep_the_open_message_and_page(qtbot: QtBot) -> None:
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    window.now = lambda: NOW
    panel = window.inbox_panel
    window._apply_owner_preferences(OwnerPreferences(revision=0, time_zone="UTC"))
    panel.show_page(two_messages())
    panel.open_message("two")
    before = panel.reader.meta.text()
    window._apply_owner_preferences(OwnerPreferences(revision=0, time_zone="America/Toronto"))
    assert panel.zone == TORONTO
    assert panel.open_id() == "two"
    assert panel.reader.meta.text() != before
    assert panel.page is not None and panel.page.offset == 100 and panel.page.has_more


async def test_marking_a_message_saves_it_and_keeps_it_open(qtbot: QtBot) -> None:
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
    window.inbox_panel.open_message("two")

    window.inbox_panel.wanted.click()
    await finish(window)

    assert backend.wanted_marks == [("owner@example.com", "two", True)]
    assert window.inbox_panel.open_id() == "two"
    assert window.inbox_panel.wanted.text() == UNWANT
    assert window.inbox_panel.page is not None and window.inbox_panel.page.offset == 100
    assert "counts this" in window.status.text()

    window.inbox_panel.wanted.click()
    await finish(window)
    assert backend.wanted_marks[-1] == ("owner@example.com", "two", False)
    assert window.status.text() == "Mark removed."


async def test_going_to_the_inbox_starts_on_its_list(qtbot: QtBot) -> None:
    class Backend(FakeBackend):
        async def inbox_messages(self, account_email: str, offset: int = 0) -> InboxPage:
            return two_messages().model_copy(update={"offset": offset})

    window = MainWindow(Backend())
    qtbot.addWidget(window)
    window._set_account("owner@example.com")
    await window._load_inbox(100)
    window.inbox_panel.open_message("one")
    window._request_page("today")
    window._request_page("inbox")
    await finish(window)
    assert window.inbox_panel.mode() == "list"
    opened: list[bool] = []
    window.command_palette.open_fresh = lambda zone, today: opened.append(True)  # type: ignore[method-assign]
    window.dialog_guard.show_dialog = lambda dialog: None  # type: ignore[method-assign]
    window.inbox_panel.search.click()
    assert opened == [True]  # The header's search opens the command palette.


def test_safe_sender_lines_in_the_reader(qtbot: QtBot) -> None:
    panel = InboxPanel(clock=lambda: NOW)
    qtbot.addWidget(panel)
    panel.zone = TORONTO
    lookalike = EmailContact(name="ceo@bank.example", address="mallory@example.com")
    page = InboxPage(
        account_email="owner@example.com",
        messages=(
            make_message(provider_message_id="named"),
            make_message(provider_message_id="lookalike", sender=lookalike, body_preview=""),
        ),
    )
    panel.show_page(page, select="named")
    assert panel.reader.from_line.text() == "Alex"
    assert panel.reader.meta.text() == "alex@example.com · Mon Aug 31, 10:30"
    assert panel.reader.monogram.initials() == "A"
    panel.open_message("lookalike")
    # A name that looks like an address is never shown: the real address is.
    assert panel.reader.from_line.text() == "mallory@example.com"
    assert panel.reader.meta.text() == "Mon Aug 31, 10:30"
    assert "Gmail has no preview" in panel.reader.preview.text()


def test_inbox_rows_name_the_day_and_their_marks(qtbot: QtBot) -> None:
    panel = InboxPanel(clock=lambda: NOW)
    qtbot.addWidget(panel)
    panel.zone = TORONTO
    panel.show_page(gallery_page())
    rows = panel.messages.inbox_model.rows()
    headers = [row.title for row in rows if row.kind == "header"]
    assert headers == [
        "Today",
        "Yesterday",
        "Previous 7 days",
        "Previous 30 days",
        "September",
        "2025",
    ]
    times = {row.message.provider_message_id: row.when for row in rows if row.message}
    assert times["budget"] == "16:25" and times["closed"] == "17:40"
    assert times["invoice"] == "Sat 14:30" and times["sale"] == "Oct 1"
    assert times["library"] == "Dec 2, 2025"
    budget = panel.messages.inbox_model.index(row_of(panel, "budget"))
    spoken = budget.data(Qt.ItemDataRole.AccessibleTextRole)
    assert spoken == (
        "Q3 budget: approval needed by Friday, from Priya Shah, 16:25, unread, "
        f"{WANTED}, has attachments"
    )
    header = panel.messages.inbox_model.index(0)
    assert header.flags() == Qt.ItemFlag.NoItemFlags


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


def test_today_is_the_owner_s_day(qtbot: QtBot) -> None:
    panel = InboxPanel(clock=lambda: datetime(2026, 10, 14, 2, 0, tzinfo=UTC))
    qtbot.addWidget(panel)
    panel.zone = TORONTO  # 22:00 on Oct 13 there.
    assert panel._today() == date(2026, 10, 13)


def test_unbroken_mail_text_never_widens_the_inbox(qtbot: QtBot) -> None:
    token = "y" * 150
    hostile = make_message(
        provider_message_id="hostile",
        subject=f"Subject {token}",
        sender=EmailContact(name=f"Name {token}", address=f"{token}@example.com"),
        body_preview=f"Preview {token}",
    )
    plain = make_message(provider_message_id="plain")

    def narrowest(message: NormalizedMessage) -> tuple[int, int]:
        window = MainWindow(FakeBackend())
        qtbot.addWidget(window)
        window.now = lambda: NOW
        window.inbox_panel.show_page(
            InboxPage(account_email="owner@example.com", messages=(message,))
        )
        window._show_page("inbox")
        window.resize(680, 520)
        window.show()
        qtbot.waitExposed(window)
        listed = window.minimumSizeHint().width()
        window.inbox_panel.open_message(message.provider_message_id)
        reader = window.inbox_panel.reader
        content = reader.body.widget()
        assert content is not None
        assert content.width() <= reader.body.viewport().width()
        return listed, window.minimumSizeHint().width()

    assert narrowest(hostile) == narrowest(plain)


@pytest.mark.parametrize("mode", [ThemeMode.DARK, ThemeMode.LIGHT])
def test_inbox_empty_gallery(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    apply_theme(qapp, mode=mode)
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    window._show_page("inbox")
    window.resize(1100, 720)
    window.show()
    qtbot.waitExposed(window)
    assert "Sign in" in window.inbox_panel.empty_message()
    save_shot(window.grab(), f"inbox-empty-{mode}")


def test_the_reader_s_actions_stack_when_the_pane_is_narrow(qtbot: QtBot) -> None:
    panel = shown_panel(qtbot, width=1000)
    # Unmarked, so the long "Should have been in my brief" is beside Open in Gmail.
    panel.show_page(two_messages(), select="one")
    actions = panel.reader._actions
    assert actions.direction() is QBoxLayout.Direction.LeftToRight
    qtbot.waitUntil(lambda: panel.reader.source.y() == panel.reader.wanted.y())
    panel.resize(READER_MIN, 600)  # The reader alone, at its narrowest.
    qtbot.waitUntil(lambda: actions.direction() is QBoxLayout.Direction.TopToBottom)
    qtbot.waitUntil(lambda: panel.reader.wanted.y() > panel.reader.source.y())
    for button in (panel.reader.source, panel.reader.wanted):
        assert button.width() == button.sizeHint().width()  # Not stretched across.
        assert button.geometry().right() < panel.reader.width()
