"""Canonical opening rejects stale results and preserves the owner's current work."""

import asyncio
from datetime import date

import pytest
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import Action, ActionFilter, ActionStatus
from mailbrief.domain.cached_mail import InboxPage
from mailbrief.domain.drafts import Draft, DraftEdit, DraftKind
from mailbrief.domain.routes import CalendarRoute, MailRoute, MessageRoute, PageRoute, TaskRoute
from mailbrief.domain.search import SearchHit, SearchKind
from mailbrief.ui.navigation import legacy_page_route, search_route
from tests.factories import make_action
from tests.ui.test_palette import ACTION_ID, MAIL_HIT, NOW, ZONE, window_for
from tests.ui.test_workflow import FakeBackend, finish


@pytest.mark.parametrize(
    ("legacy", "route"),
    [
        ("today", PageRoute(page="today")),
        ("actions", TaskRoute()),
        ("tasks", TaskRoute()),
        ("waiting", TaskRoute(view=ActionFilter.WAITING)),
        ("calendar", PageRoute(page="calendar")),
        ("notes", PageRoute(page="notes")),
        ("projects", PageRoute(page="projects")),
        ("inbox", MailRoute()),
        ("mail", MailRoute()),
        ("briefs", MailRoute(view="history")),
        ("drafts", MailRoute(view="drafts")),
        ("unknown", None),
    ],
)
def test_legacy_commands_map_to_canonical_destinations(legacy: str, route: object) -> None:
    assert legacy_page_route(legacy) == route


def test_search_routes_require_complete_identity_and_bind_mail_account() -> None:
    assert search_route(MAIL_HIT, ZONE, None) is None
    route = search_route(MAIL_HIT, ZONE, "owner@example.com")
    assert isinstance(route, MessageRoute)
    assert route.account_email == "owner@example.com"
    incomplete = SearchHit(kind=SearchKind.GOOGLE_EVENT, title="Meeting", day=date(2026, 11, 2))
    assert search_route(incomplete, ZONE, "owner@example.com") is None
    event = incomplete.model_copy(update={"calendar_id": "work", "event_id": "meeting"})
    assert search_route(event, ZONE, None) == CalendarRoute(
        day=date(2026, 11, 2), entry=("google", "work", "meeting")
    )


@pytest.mark.parametrize("switch_account", [False, True])
async def test_late_mail_open_cannot_replace_a_new_destination(
    qtbot: QtBot, switch_account: bool
) -> None:
    backend = FakeBackend()
    window = await window_for(qtbot, backend)
    started, release = asyncio.Event(), asyncio.Event()

    async def messages(
        account_email: str, offset: int = 0, *, query: str = "", cached_page: bool = False
    ) -> InboxPage:
        started.set()
        await release.wait()
        return InboxPage(account_email=account_email, offset=offset, messages=())

    backend.inbox_positions = {"m150": 150}
    backend.inbox_messages = messages  # type: ignore[method-assign]
    window._open_hit(MAIL_HIT)
    await started.wait()
    window._request_page("calendar")
    if switch_account:
        window._set_account("other@example.com")
    release.set()
    await finish(window)
    await window.drain_calendar_reads()
    assert window.workspace.current_page() == "calendar"
    assert window.inbox_panel.open_id() is None


async def test_message_route_refuses_a_different_account(qtbot: QtBot) -> None:
    window = await window_for(qtbot, FakeBackend())
    window._navigate(MessageRoute(account_email="other@example.com", message_id="m150"))
    assert window.workspace.current_page() == "today"
    assert "account changed" in window.status.text()


async def test_late_note_load_does_not_replace_the_current_editor(qtbot: QtBot) -> None:
    backend = FakeBackend()
    first = backend._add(DraftKind.NOTE, DraftEdit(body="Current writing"))
    second = backend._add(DraftKind.NOTE, DraftEdit(body="Another note"))
    window = await window_for(qtbot, backend)
    window._edit_draft(first)
    started, release = asyncio.Event(), asyncio.Event()

    async def get_draft(public_id: str) -> Draft:
        started.set()
        await release.wait()
        return second

    backend.get_draft = get_draft  # type: ignore[method-assign]
    window._open_hit(
        SearchHit(kind=SearchKind.WRITING, title="Another note", public_id=second.public_id)
    )
    await started.wait()
    window._request_page("today")
    release.set()
    await finish(window)
    assert window.workspace.current_page() == "today"
    assert window.draft_editor.draft is not None
    assert window.draft_editor.draft.public_id == first.public_id
    window.draft_editor.force_close()


async def test_task_outside_the_completed_limit_is_revealed(qtbot: QtBot) -> None:
    backend = FakeBackend()
    backend.actions[ActionFilter.COMPLETED] = tuple(
        make_action(
            public_id=f"00000000-0000-4000-8000-{index:012d}",
            status=ActionStatus.COMPLETED,
            completed_at_utc=NOW,
        )
        for index in range(2, 202)
    )
    backend.action_counts[ActionFilter.COMPLETED] = 500
    window = await window_for(qtbot, backend)
    action = make_action(public_id=ACTION_ID, status=ActionStatus.COMPLETED, completed_at_utc=NOW)

    async def get_action(public_id: str) -> Action:
        return action

    backend.get_action = get_action  # type: ignore[method-assign]
    window._open_action(ACTION_ID, ActionFilter.OPEN)  # The old hit's status is stale.
    await finish(window)
    assert window.actions_panel.view() is ActionFilter.COMPLETED
    selected = window.actions_panel.selected()
    assert selected is not None and selected.public_id == ACTION_ID
    assert "201+" in window.actions_panel.tabs.tabText(
        list(ActionFilter).index(ActionFilter.COMPLETED)
    )  # Revealing one extra row must not imply that the bounded list is complete.


async def test_missing_task_leaves_the_workspace_usable(qtbot: QtBot) -> None:
    window = await window_for(qtbot, FakeBackend())
    window._open_action(ACTION_ID, ActionFilter.OPEN)
    await finish(window)
    assert "no longer available" in window.status.text()
    window._request_page("today")
    assert window.workspace.current_page() == "today"


async def test_task_load_cannot_select_after_navigation_away_and_back(qtbot: QtBot) -> None:
    backend = FakeBackend()
    window = await window_for(qtbot, backend)
    started, release = asyncio.Event(), asyncio.Event()

    async def get_action(public_id: str) -> Action:
        started.set()
        await release.wait()
        return make_action(public_id=public_id)

    backend.get_action = get_action  # type: ignore[method-assign]
    window._open_action(ACTION_ID, ActionFilter.OPEN)
    await started.wait()
    window._request_page("calendar")
    window._request_page("actions")
    release.set()
    await finish(window)
    await window.drain_calendar_reads()
    assert window.actions_panel.selected() is None
