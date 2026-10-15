"""The command palette (Ctrl+K): commands, local search, and opening what is chosen."""

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import ActionEdit, ActionFilter
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.cached_mail import InboxPage
from mailbrief.domain.drafts import DraftEdit, DraftKind
from mailbrief.domain.events import CalendarEvent
from mailbrief.domain.google_calendar import GoogleEvent
from mailbrief.domain.messages import AccountIdentity, ProviderKind
from mailbrief.domain.preferences import OwnerPreferences
from mailbrief.domain.search import HITS_PER_KIND, SearchHit, SearchKind, SearchResults
from mailbrief.storage.repositories import AccountRepository, MessageRepository
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.palette import (
    COMMANDS,
    DEBOUNCE_MS,
    FAILED,
    HINT,
    NO_MATCHES,
    SEARCHING,
    CommandPalette,
    hit_text,
    matching_commands,
)
from mailbrief.ui.runtime import DesktopRuntime
from mailbrief.ui.theme import ThemeMode, apply_theme
from tests.factories import make_action, make_message
from tests.ui.test_main_window_layout import save_shot
from tests.ui.test_workflow import FakeBackend, finish

ZONE = ZoneInfo("America/Toronto")
TODAY = date(2026, 10, 13)
NOW = datetime(2026, 10, 13, 16, tzinfo=UTC)
ACTION_ID = "00000000-0000-4000-8000-000000000001"
EVENT_ID = "00000000-0000-4000-9000-000000000001"

ACTION_HIT = SearchHit(kind=SearchKind.ACTION, title="Approve the budget", public_id=ACTION_ID)
WAITING_HIT = ACTION_HIT.model_copy(update={"waiting": True})
DONE_HIT = ACTION_HIT.model_copy(update={"open": False})
NOTE_HIT = SearchHit(
    kind=SearchKind.WRITING,
    title="Budget questions",
    public_id="00000000-0000-4000-7000-000000000001",
    draft_kind=DraftKind.NOTE,
)
EVENT_HIT = SearchHit(
    kind=SearchKind.EVENT,
    title="Budget review",
    public_id=EVENT_ID,
    when=datetime(2026, 9, 9, 14, tzinfo=UTC),
)
GOOGLE_HIT = SearchHit(
    kind=SearchKind.GOOGLE_EVENT,
    title="Budget sync",
    detail="Work",
    calendar_id="work@example.com",
    event_id="sync",
    day=date(2026, 9, 10),
)
MAIL_HIT = SearchHit(
    kind=SearchKind.MAIL,
    title="Budget figures",
    detail="Priya Shah",
    message_id="m150",
    web_link="https://mail.google.com/mail/u/0/#all/m150",
    when=datetime(2026, 10, 12, 13, tzinfo=UTC),
)
OLD_MAIL_HIT = MAIL_HIT.model_copy(update={"open": False, "message_id": "gone"})
RESULTS = SearchResults(
    hits=(ACTION_HIT, NOTE_HIT, EVENT_HIT, GOOGLE_HIT, MAIL_HIT), more=frozenset()
)


def texts(palette: CommandPalette) -> list[str]:
    widget = palette.results
    return [widget.item(number).text() for number in range(widget.count())]


def test_commands_match_every_word_of_the_query() -> None:
    assert matching_commands("") == COMMANDS
    assert [command.key for command in matching_commands("go cal")] == ["page:calendar"]
    assert [command.key for command in matching_commands("NEW")] == [
        "new:task",
        "new:event",
        "new:note",
    ]
    assert matching_commands("budget") == ()


def test_hits_read_as_one_line_each() -> None:
    lines = [hit_text(hit, ZONE, TODAY) for hit in RESULTS.hits]
    assert lines == [
        "Approve the budget · open",
        "Budget questions · Note",
        "Budget review · Wed Sep 9, 10:00",
        "Budget sync · Work · Thu Sep 10",
        "Budget figures · Priya Shah · Mon Oct 12, 09:00",
    ]
    assert hit_text(WAITING_HIT, ZONE, TODAY) == "Approve the budget · waiting for others"
    assert hit_text(DONE_HIT, ZONE, TODAY) == "Approve the budget · done"
    assert hit_text(OLD_MAIL_HIT, ZONE, TODAY).startswith(
        "Budget figures · Priya Shah, not in the Inbox"
    )


@pytest.fixture
def palette(qtbot: QtBot) -> CommandPalette:
    result = CommandPalette()
    qtbot.addWidget(result)
    result.open_fresh(ZONE, TODAY)
    return result


def test_a_fresh_palette_lists_every_command_with_the_first_selected(
    palette: CommandPalette,
) -> None:
    assert texts(palette) == ["Commands", *(command.label for command in COMMANDS)]
    assert palette.results.currentRow() == 1  # The heading can't be selected.
    assert palette.note.text() == HINT


async def test_typing_searches_once_it_pauses_and_shows_sections(
    qtbot: QtBot, palette: CommandPalette
) -> None:
    asked: list[str] = []
    palette.query_changed.connect(asked.append)
    palette.show()

    palette.field.setText("q")  # Too short to search, and in no command.
    assert palette.note.text() == NO_MATCHES and asked == []
    palette.field.setText("budget")
    assert palette.note.text() == SEARCHING
    qtbot.waitUntil(lambda: asked == ["budget"])
    palette.show_results("budget", RESULTS)

    assert texts(palette) == [
        "Actions",
        "Approve the budget · open",
        "Drafts and notes",
        "Budget questions · Note",
        "Your events",
        "Budget review · Wed Sep 9, 10:00",
        "Google Calendar",
        "Budget sync · Work · Thu Sep 10",
        "Mail",
        "Budget figures · Priya Shah · Mon Oct 12, 09:00",
    ]
    assert palette.results.currentRow() == 1
    assert palette.note.text() == HINT


def test_closing_before_typing_pauses_starts_no_search(
    qtbot: QtBot, palette: CommandPalette
) -> None:
    asked: list[str] = []
    palette.query_changed.connect(asked.append)
    palette.show()
    palette.field.setText("budget")

    palette.reject()
    qtbot.wait(DEBOUNCE_MS * 2)

    assert asked == []
    palette._query()  # Even called directly, a closed palette asks for nothing.
    assert asked == []


def test_an_older_search_s_results_are_dropped(palette: CommandPalette) -> None:
    palette.field.setText("budget figures")
    palette.show_results("budget", RESULTS)
    assert "Mail" not in texts(palette)
    palette.show_failure("budget")
    assert palette.note.text() == SEARCHING
    palette.show_failure("budget figures")
    assert palette.note.text() == FAILED


def test_nothing_found_and_more_found_say_so(palette: CommandPalette) -> None:
    palette.field.setText("zzz")
    palette.show_results("zzz", SearchResults())
    assert palette.note.text() == NO_MATCHES and texts(palette) == []
    palette.field.setText("budget")
    palette.show_results("budget", RESULTS.model_copy(update={"more": {SearchKind.MAIL}}))
    assert str(HITS_PER_KIND) in palette.note.text()


def test_the_field_moves_through_rows_and_return_opens_one(palette: CommandPalette) -> None:
    chosen: list[object] = []
    palette.hit_chosen.connect(chosen.append)
    palette.command_chosen.connect(chosen.append)
    palette.show()
    palette.field.setText("budget")
    palette.show_results("budget", RESULTS)

    for _ in range(2):
        QTest.keyClick(palette.field, Qt.Key.Key_Down)  # Past the headings between.
    assert palette.results.currentItem().text() == "Budget review · Wed Sep 9, 10:00"
    QTest.keyClick(palette.field, Qt.Key.Key_Up)
    assert palette.results.currentItem().text() == "Budget questions · Note"
    QTest.keyClick(palette.field, Qt.Key.Key_Return)

    assert chosen == [NOTE_HIT]
    assert not palette.isVisible()


def test_a_heading_is_never_chosen(palette: CommandPalette) -> None:
    chosen: list[object] = []
    palette.command_chosen.connect(chosen.append)
    palette.results.itemActivated.emit(palette.results.item(0))
    assert chosen == []
    palette.results.itemActivated.emit(palette.results.item(1))
    assert chosen == ["page:today"]


@pytest.fixture
def backend() -> FakeBackend:
    fake = FakeBackend()
    fake.actions = {
        ActionFilter.OPEN: (make_action(public_id=ACTION_ID, title="Approve the budget"),),
        ActionFilter.WAITING: (),
        ActionFilter.COMPLETED: (),
    }
    fake.search_results = RESULTS
    return fake


async def window_for(qtbot: QtBot, backend: FakeBackend) -> MainWindow:
    window = MainWindow(backend)
    qtbot.addWidget(window)
    window.now = lambda: NOW
    window._apply_owner_preferences(OwnerPreferences(revision=1, time_zone=ZONE.key))
    window.start(window.initialize)
    await finish(window)
    return window


async def test_ctrl_k_opens_the_palette_once_and_searches_locally(
    qtbot: QtBot, backend: FakeBackend
) -> None:
    window = await window_for(qtbot, backend)

    window.palette_shortcut.activated.emit()
    assert window.command_palette.isVisible()
    assert window._dialog_open()
    window._open_palette()  # Already open: nothing more happens.
    window.command_palette.field.setText("budget")
    # The pause fires on Qt's loop; the search itself then runs on asyncio's.
    qtbot.waitUntil(lambda: window._search_read == 1)
    await window.drain_calendar_reads()

    assert backend.searches == [("budget", "owner@example.com")]

    assert "Approve the budget · open" in texts(window.command_palette)
    window.command_palette.reject()


async def test_a_failed_search_says_so(qtbot: QtBot, backend: FakeBackend) -> None:
    window = await window_for(qtbot, backend)
    backend.search_fail = RuntimeError("synthetic")
    window._open_palette()
    window.command_palette.field.setText("budget")

    window._search("budget")
    await window.drain_calendar_reads()

    assert window.command_palette.note.text() == FAILED
    window.command_palette.reject()


async def test_commands_go_to_pages_and_start_things(qtbot: QtBot, backend: FakeBackend) -> None:
    window = await window_for(qtbot, backend)

    window._run_command("page:calendar")
    assert window.workspace.current_page() == "calendar"
    window._run_command("page:waiting")
    assert window.actions_panel.view() is ActionFilter.WAITING
    window._run_command("new:task")
    assert window.action_editor.isVisible()
    window.action_editor.reject()
    window._run_command("new:event")
    assert window.event_editor.isVisible()
    window.event_editor.reject()
    window._run_command("new:note")
    await finish(window)
    assert window.draft_editor.isVisible()
    assert window.draft_editor.draft is not None
    assert window.draft_editor.draft.kind is DraftKind.NOTE
    window.draft_editor.force_close()
    window._run_command("unknown")  # Ignored.
    clicked: list[str] = []
    window.settings_button.clicked.disconnect()
    window.settings_button.clicked.connect(lambda: clicked.append("settings"))
    window._run_command("settings")
    assert clicked == ["settings"]


@pytest.mark.parametrize(
    ("hit", "view"),
    [
        (ACTION_HIT, ActionFilter.OPEN),
        (WAITING_HIT, ActionFilter.WAITING),
        (DONE_HIT, ActionFilter.COMPLETED),
    ],
)
async def test_an_action_opens_in_its_view(
    qtbot: QtBot, backend: FakeBackend, hit: SearchHit, view: ActionFilter
) -> None:
    action = make_action(public_id=ACTION_ID, title="Approve the budget")
    backend.actions = {
        ActionFilter.OPEN: (),
        ActionFilter.WAITING: (),
        ActionFilter.COMPLETED: (),
        view: (action,),
    }
    window = await window_for(qtbot, backend)

    window._open_hit(hit)

    assert window.workspace.current_page() == "actions"
    assert window.actions_panel.view() is view
    selected = window.actions_panel.selected()
    assert selected is not None and selected.public_id == ACTION_ID


async def test_an_action_a_filter_hides_shows_after_clearing_it(
    qtbot: QtBot, backend: FakeBackend
) -> None:
    window = await window_for(qtbot, backend)
    window.actions_panel.show_actions(ActionFilter.OPEN, (), today=TODAY, zone=ZONE, now=NOW)

    window._open_hit(ACTION_HIT)
    await finish(window)

    selected = window.actions_panel.selected()
    assert selected is not None and selected.public_id == ACTION_ID


async def test_writing_opens_in_its_editor(qtbot: QtBot, backend: FakeBackend) -> None:
    window = await window_for(qtbot, backend)
    draft = backend._add(DraftKind.NOTE, DraftEdit(body="Budget questions"))

    window._open_hit(NOTE_HIT.model_copy(update={"public_id": draft.public_id}))
    await finish(window)

    assert window.workspace.current_page() == "drafts"
    assert window.draft_editor.isVisible()
    assert window.draft_editor.draft is not None
    assert window.draft_editor.draft.public_id == draft.public_id
    window.draft_editor.force_close()


async def test_events_open_selected_in_their_week(qtbot: QtBot, backend: FakeBackend) -> None:
    start = datetime(2026, 9, 9, 14, tzinfo=UTC)
    backend.events = {
        EVENT_ID: CalendarEvent(
            public_id=EVENT_ID,
            title="Budget review",
            notes="",
            all_day=False,
            starts_at_utc=start,
            ends_at_utc=start.replace(hour=15),
            start_date=None,
            last_date=None,
            time_zone=ZONE.key,
            revision=1,
            created_at_utc=NOW,
            updated_at_utc=NOW,
        )
    }
    google = GoogleEvent(
        calendar_id="work@example.com",
        calendar_name="Work",
        event_id="sync",
        title="Budget sync",
        all_day=True,
        start_date=date(2026, 9, 10),
        last_date=date(2026, 9, 10),
    )
    backend.google_events = [google]
    window = await window_for(qtbot, backend)

    window._open_hit(EVENT_HIT)
    await window.drain_calendar_reads()
    assert window.workspace.current_page() == "calendar"
    assert window._calendar_week == date(2026, 9, 7)
    selected = window.calendar_panel.selected()
    assert selected is not None and selected.public_id == EVENT_ID

    window._open_hit(GOOGLE_HIT)
    await window.drain_calendar_reads()
    assert window.calendar_panel.selected_google() == google


async def test_mail_opens_on_its_inbox_page_or_in_gmail(
    qtbot: QtBot, backend: FakeBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []

    def open_url(url: QUrl) -> bool:
        opened.append(url.toString())
        return True

    monkeypatch.setattr(QDesktopServices, "openUrl", open_url)
    pages: list[int] = []

    async def inbox_messages(
        account_email: str, offset: int = 0, *, query: str = "", cached_page: bool = False
    ) -> InboxPage:
        pages.append(offset)
        return InboxPage(
            account_email=account_email,
            offset=offset,
            messages=(
                make_message(provider=ProviderKind.GMAIL, provider_message_id="m149"),
                make_message(provider=ProviderKind.GMAIL, provider_message_id="m150"),
            ),
        )

    backend.inbox_messages = inbox_messages  # type: ignore[method-assign]
    backend.inbox_positions = {"m150": 150}
    window = await window_for(qtbot, backend)

    window._open_hit(MAIL_HIT)
    await finish(window)
    assert window.workspace.current_page() == "inbox"
    assert pages[-1] == 100
    assert window.inbox_panel.open_id() == "m150"  # Open in the reading pane.
    current = window.inbox_panel.messages.current_message()
    assert current is not None and current.provider_message_id == "m150"

    window._open_hit(MAIL_HIT.model_copy(update={"message_id": "archived"}))
    await finish(window)
    window._open_hit(OLD_MAIL_HIT)
    window._open_hit(OLD_MAIL_HIT.model_copy(update={"web_link": "https://evil.example/x"}))
    assert opened == [MAIL_HIT.web_link, OLD_MAIL_HIT.web_link]  # Only Gmail links.
    window._open_hit("not a hit")  # Ignored.


@pytest.mark.parametrize("mode", [ThemeMode.DARK, ThemeMode.LIGHT])
async def test_palette_gallery(
    qtbot: QtBot, qapp: QApplication, themed: None, backend: FakeBackend, mode: ThemeMode
) -> None:
    apply_theme(qapp, mode=mode)
    window = await window_for(qtbot, backend)
    window.resize(1100, 720)
    window.show()
    qtbot.waitExposed(window)
    window._open_palette()
    window.command_palette.field.setText("budget")
    window.command_palette.show_results("budget", RESULTS)
    await asyncio.sleep(0)
    save_shot(window.command_palette.grab(), f"palette-{mode}")
    window.command_palette.reject()


async def test_runtime_search_and_inbox_position_read_only_local_data(tmp_path: Path) -> None:
    backend = DesktopRuntime(tmp_path / "mailbrief.sqlite3")
    await backend.load_saved()
    try:
        async with backend._storage().session() as session:
            account = await AccountRepository(session).upsert(
                AccountIdentity(
                    provider=ProviderKind.GMAIL,
                    provider_account_id="owner@example.com",
                    email_address="owner@example.com",
                )
            )
            await MessageRepository(session).upsert_messages(
                account.id,
                [
                    make_message(
                        provider=ProviderKind.GMAIL,
                        provider_message_id="m1",
                        subject="Budget figures",
                        web_link="https://mail.google.com/mail/u/0/#all/m1",
                    )
                ],
            )
            await session.commit()
        await backend.create_action(
            ActionEdit(
                title="Approve the budget",
                ownership=ActionOwnership.MINE,
                effort=None,
                priority=None,
                tags=(),
                estimate_minutes=None,
                target_date=None,
                notes="",
            ),
            (),
        )

        found = await backend.search("budget", "owner@example.com")

        assert [(hit.kind, hit.title) for hit in found.hits] == [
            (SearchKind.ACTION, "Approve the budget"),
            (SearchKind.MAIL, "Budget figures"),
        ]
        assert [hit.kind for hit in (await backend.search("budget", None)).hits] == [
            SearchKind.ACTION
        ]
        assert await backend.inbox_position("owner@example.com", "m1") == 0
        assert await backend.inbox_position("other@example.com", "m1") is None
    finally:
        await backend.close()
