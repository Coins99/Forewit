"""The writing page: plain rows, New, Open (Return or double-click), Delete and busy state,
and its Notes view with a project filter (ADR 0027)."""

from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from pytestqt.qtbot import QtBot

from mailbrief.domain.drafts import Draft, DraftKind, DraftSummary, WritingView
from mailbrief.domain.projects import Project
from mailbrief.ui.drafts_view import DELETE, NEW, OPEN, DraftsPanel, describe
from mailbrief.ui.list_rows import DISPLAY_ROW_ROLE, DisplayRow

TORONTO = ZoneInfo("America/Toronto")
TODAY = date(2026, 9, 30)


def summary(number: int, **overrides: Any) -> DraftSummary:
    values: dict[str, Any] = {
        "public_id": f"00000000-0000-4000-8000-{number:012d}",
        "kind": DraftKind.MESSAGE,
        "display_title": f"Draft {number}",
        "updated_at_utc": datetime(2026, 9, 28, 14, 5, tzinfo=UTC),
        "placeholder_count": 0,
        "revision": 1,
    }
    values.update(overrides)
    return DraftSummary.model_validate(values)


@pytest.fixture
def panel(qtbot: QtBot) -> DraftsPanel:
    result = DraftsPanel()
    qtbot.addWidget(result)
    return result


def requests(panel: DraftsPanel) -> list[tuple[str, object]]:
    found: list[tuple[str, object]] = []
    panel.draft_requested.connect(lambda kind, value: found.append((kind, value)))
    return found


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        ({}, "Message · Draft 1 — updated Mon Sep 28, 10:05"),
        (
            {"kind": DraftKind.NOTE, "project_name": "Home", "action_title": "Paint"},
            "Note · Draft 1 — updated Mon Sep 28, 10:05 · in Home · for “Paint”",
        ),
        (
            {"kind": DraftKind.REPLY, "placeholder_count": 1, "action_title": "Send it"},
            "Reply · Draft 1 — updated Mon Sep 28, 10:05 · 1 placeholder · for “Send it”",
        ),
        (
            {"kind": DraftKind.MESSAGE, "placeholder_count": 3},
            "Message · Draft 1 — updated Mon Sep 28, 10:05 · 3 placeholders",
        ),
    ],
)
def test_describe(fields: dict[str, Any], expected: str) -> None:
    assert describe(summary(1, **fields), TORONTO, TODAY) == expected


def test_rows_are_plain_text_and_keep_the_selection(panel: DraftsPanel) -> None:
    assert not panel.empty.isHidden()
    assert not panel.open_button.isEnabled()
    first, second = summary(1, display_title="<b>bold</b>"), summary(2)
    panel.show_drafts((first, second), TORONTO, TODAY)
    panel.list.setCurrentRow(1)

    panel.show_drafts((summary(3), first, second), TORONTO, TODAY)

    assert panel.empty.isHidden()
    assert panel.selected() == second
    assert panel.list.item(1).text().startswith("Message · <b>bold</b>")
    panel.show_drafts((summary(3),), TORONTO, TODAY)
    assert panel.selected() == summary(3)
    panel.show_drafts((), TORONTO, TODAY)
    assert panel.selected() is None and not panel.delete_button.isEnabled()


def test_new_offers_email_and_message(panel: DraftsPanel) -> None:
    found = requests(panel)
    names = [action.text() for action in panel.new_menu.actions()]
    assert names == ["Email", "Message"]  # Notes start from the Notes view.

    for action in panel.new_menu.actions():
        action.trigger()

    assert found == [(NEW, DraftKind.EMAIL), (NEW, DraftKind.MESSAGE)]


def test_open_by_button_return_or_double_click_and_delete(panel: DraftsPanel, qtbot: QtBot) -> None:
    found = requests(panel)
    panel.show_drafts((summary(1),), TORONTO, TODAY)

    panel.open_button.click()
    QTest.keyClick(panel.list, Qt.Key.Key_Return)
    item = panel.list.item(0)
    panel.list.itemActivated.emit(item)
    panel.delete_button.click()
    QTest.keyClick(panel.list, Qt.Key.Key_Down)  # Other keys still move the selection.

    assert found == [(OPEN, summary(1))] * 3 + [(DELETE, summary(1))]


def test_busy_disables_everything(panel: DraftsPanel) -> None:
    found = requests(panel)
    panel.show_drafts((summary(1),), TORONTO, TODAY)

    panel.set_busy(True)
    panel.new_menu.actions()[0].trigger()
    panel.list.itemActivated.emit(panel.list.item(0))

    assert found == []
    assert not any(
        button.isEnabled() for button in (panel.new_button, panel.open_button, panel.delete_button)
    )
    panel.set_busy(False)
    assert panel.open_button.isEnabled()


def test_without_drafts_a_centred_message_replaces_the_list(panel: DraftsPanel) -> None:
    assert panel.list.isHidden() and not panel.empty.isHidden()
    assert panel.empty.property("tone") == "muted"
    assert panel.empty.alignment() & Qt.AlignmentFlag.AlignCenter
    assert panel.empty.wordWrap()
    panel.show_drafts((summary(1),), TORONTO, TODAY)
    assert not panel.list.isHidden() and panel.empty.isHidden()
    panel.show_drafts((), TORONTO, TODAY)
    assert panel.list.isHidden() and not panel.empty.isHidden()


def test_a_draft_from_another_year_names_it(qtbot: QtBot) -> None:
    next_year = date(2027, 1, 4)
    assert describe(summary(1), TORONTO, next_year).startswith(
        "Message · Draft 1 — updated Mon Sep 28, 2026, 10:05"
    )
    panel = DraftsPanel()
    qtbot.addWidget(panel)
    panel.show_drafts((summary(1),), TORONTO, next_year)
    assert "updated Mon Sep 28, 2026, 10:05" in panel.list.item(0).text()


HOME = "11111111-1111-4111-8111-111111111111"
OLD = "22222222-2222-4222-8222-222222222222"


def project(public_id: str, name: str, *, archived: bool = False) -> Project:
    moment = datetime(2026, 9, 1, tzinfo=UTC)
    return Project(
        public_id=public_id,
        name=name,
        revision=1,
        created_at_utc=moment,
        updated_at_utc=moment,
        archived_at_utc=moment if archived else None,
    )


def ids(panel: DraftsPanel) -> list[str]:
    """The titles listed, in order."""
    return [
        panel.list.item(row).text().split(" · ")[1].split(" — ")[0]
        for row in range(panel.list.count())
    ]


def notes_panel(panel: DraftsPanel) -> DraftsPanel:
    panel.show_projects((project(HOME, "Home"), project(OLD, "Old", archived=True)))
    panel.show_drafts(
        (
            summary(1, kind=DraftKind.NOTE, display_title="Paint", project_public_id=HOME),
            summary(2, display_title="Reply text"),
            summary(3, kind=DraftKind.NOTE, display_title="Loose"),
            summary(4, kind=DraftKind.NOTE, display_title="Old plan", project_public_id=OLD),
        ),
        TORONTO,
        TODAY,
    )
    return panel


def test_each_view_lists_its_own_kinds_and_counts_all(panel: DraftsPanel) -> None:
    notes_panel(panel)
    assert panel.view() is WritingView.DRAFTS
    assert ids(panel) == ["Reply text"] and panel.project_filter.isHidden()
    assert panel.draft_count.text() == "1 drafts"

    panel.show_view(WritingView.NOTES)

    assert ids(panel) == ["Paint", "Loose", "Old plan"]
    assert panel.draft_count.text() == "3 notes"
    assert not panel.project_filter.isHidden()
    assert panel.search.placeholderText() == "Search notes"
    assert panel.new_button.menu() is None and panel.new_button.text() == "Ne&w note"
    assert panel.totals() == {WritingView.NOTES: 3, WritingView.DRAFTS: 1}
    panel.show_view(WritingView.DRAFTS)
    assert panel.new_button.menu() is panel.new_menu
    assert ids(panel) == ["Reply text"]


def test_the_project_filter_narrows_notes(panel: DraftsPanel) -> None:
    notes_panel(panel)
    panel.show_view(WritingView.NOTES)
    names = [panel.project_filter.itemText(i) for i in range(panel.project_filter.count())]
    assert names == ["All notes", "No project", "Project: Home", "Project: Old (archived)"]

    panel.filter_project(HOME)
    assert ids(panel) == ["Paint"]
    assert panel.project_for_new_note() == HOME
    panel.filter_project("unassigned")
    assert ids(panel) == ["Loose"]
    assert panel.project_for_new_note() is None
    panel.filter_project(OLD)
    assert ids(panel) == ["Old plan"]
    # An archived project takes no new notes, so a new one shows under All notes.
    assert panel.project_for_new_note() is None
    assert panel.project_filter.currentData() == "all"
    assert len(ids(panel)) == 3


def test_new_note_and_keeping_the_edited_note_shown(panel: DraftsPanel) -> None:
    found = requests(panel)
    notes_panel(panel)
    panel.show_view(WritingView.NOTES)
    panel.new_button.click()
    assert found == [(NEW, DraftKind.NOTE)]

    panel.filter_project(HOME)
    moment = datetime(2026, 9, 28, 14, 5, tzinfo=UTC)
    draft = Draft(  # The Home note, just moved out of its project.
        public_id=summary(1).public_id,
        kind=DraftKind.NOTE,
        title="Paint",
        created_at_utc=moment,
        updated_at_utc=moment,
        revision=2,
    )
    panel.keep_shown(draft)
    assert panel.project_filter.currentData() == "all"
    panel.filter_project(HOME)
    panel.keep_shown(draft.model_copy(update={"project_public_id": HOME}))
    assert panel.project_filter.currentData() == HOME


def test_a_note_row_shows_its_project_and_task(panel: DraftsPanel) -> None:
    panel.show_view(WritingView.NOTES)
    panel.show_drafts(
        (
            summary(
                1,
                kind=DraftKind.NOTE,
                project_public_id=HOME,
                project_name="Home",
                action_title="Paint the hall",
            ),
        ),
        TORONTO,
        TODAY,
    )
    row = panel.list.item(0).data(DISPLAY_ROW_ROLE)
    assert isinstance(row, DisplayRow)
    assert row.preview == "Home · For “Paint the hall”"
