"""The Actions page: painted rows with plain item text, counts, the detail pane, buttons,
requests and safe source links."""

from datetime import UTC, date, datetime, timedelta
from unittest.mock import Mock
from zoneinfo import ZoneInfo

import pytest
from PySide6.QtCore import QPoint, QRect, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QImage, QPainter, QStandardItem, QStandardItemModel
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QStyle,
    QStyleOptionViewItem,
    QTabBar,
    QWidget,
)
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import (
    Action,
    ActionActivity,
    ActionActivityKind,
    ActionFilter,
    ActionPriority,
    ActionProposal,
    ActionSource,
    ActionStatus,
    ActionStep,
    ThreadActivity,
)
from mailbrief.domain.analysis import ActionOwnership, DeadlinePrecision, TargetReason
from mailbrief.domain.drafts import DraftKind
from mailbrief.ui.action_detail import EMPTY_TEXT, state_text
from mailbrief.ui.action_text import action_details
from mailbrief.ui.actions_view import (
    COMPLETE,
    DELETE,
    EDIT,
    NO_DATES,
    REOPEN,
    SEEN,
    ActionRow,
    ActionRowDelegate,
    ActionsPanel,
    action_row,
    describe,
)
from mailbrief.ui.brief_list import ROW_ROLE, Chip, ChipTone, row_height
from mailbrief.ui.theme import ThemeMode, apply_theme, current_tokens
from tests.factories import make_action, make_proposal
from tests.ui.window_wait import show as show_window

UTC_ZONE = ZoneInfo("UTC")
NOW = datetime(2026, 10, 3, 9, 0, tzinfo=UTC)
TODAY = date(2026, 10, 3)
GMAIL = "https://mail.google.com/mail/u/?authuser=me%40x.com#all/t1"


def source(link: str = GMAIL, *, available: bool = True) -> ActionSource:
    return ActionSource(
        provider_message_id="m1",
        subject="Q3 deck",
        sender_address="alex@example.com",
        web_link=link,
        received_at_utc=datetime(2026, 9, 28, 12, tzinfo=UTC),
        available=available,
        in_inbox=True if available else None,
    )


def action(number: int, **overrides: object) -> Action:
    values: dict[str, object] = {
        "public_id": f"00000000-0000-4000-8000-{number:012d}",
        "title": f"Action {number}",
        "created_at_utc": datetime(2026, 10, 3, 8, tzinfo=UTC),
        "updated_at_utc": datetime(2026, 10, 3, 8, tzinfo=UTC),
    }
    values.update(overrides)
    return make_action(**values)


@pytest.fixture
def panel(qtbot: QtBot) -> ActionsPanel:
    result = ActionsPanel()
    qtbot.addWidget(result)
    return result


def show(panel: ActionsPanel, view: ActionFilter, *actions: Action) -> None:
    panel.show_actions(view, actions, today=TODAY, zone=UTC_ZONE, now=NOW)


def row_text(panel: ActionsPanel, view: ActionFilter, row: int = 0) -> str:
    item = panel.lists[view].item(row)
    assert item is not None
    return item.text()


def test_an_exact_deadline_reads_in_the_owner_s_zone(panel: ActionsPanel) -> None:
    friday_night = action(
        4,
        deadline_text="Friday 11 PM Pacific",
        deadline_precision=DeadlinePrecision.DATETIME,
        deadline_date=date(2026, 10, 2),
        deadline_at_utc=datetime(2026, 10, 3, 6, 0, tzinfo=UTC),
        deadline_timezone="America/Los_Angeles",
    )
    toronto = ZoneInfo("America/Toronto")

    panel.show_actions(
        ActionFilter.OPEN,
        (friday_night,),
        today=date(2026, 10, 2),
        zone=toronto,
        now=datetime(2026, 10, 2, 16, tzinfo=UTC),
    )

    assert row_text(panel, ActionFilter.OPEN) == "Action 4 — due Sat Oct 3, 02:00"  # Saturday.


def test_rows_are_plain_text_with_dates_progress_and_state(panel: ActionsPanel) -> None:
    late = action(
        1,
        title="<b>Send</b> the deck",
        target_date=date(2026, 9, 30),
        deadline_text="by Friday",
        deadline_precision=DeadlinePrecision.DATE,
        deadline_date=date(2026, 10, 2),
        deadline_timezone="UTC",
        created_at_utc=datetime(2026, 9, 28, 12, tzinfo=UTC),
        steps=(
            ActionStep(step_id=1, position=0, text="Draft", done=True),
            ActionStep(step_id=2, position=1, text="Review", done=False),
        ),
    )
    show(panel, ActionFilter.OPEN, late)

    assert row_text(panel, ActionFilter.OPEN) == (
        "<b>Send</b> the deck — Target Wed Sep 30 · due Fri Oct 2 · 1/2 steps · overdue · "
        "carried over"
    )
    assert panel.tabs.tabText(0) == "Open (1)"


def test_waiting_unresolved_and_unavailable_details(panel: ActionsPanel) -> None:
    waiting = action(
        2,
        ownership=ActionOwnership.WAITING_FOR,
        deadline_text="soon",
        deadline_precision=DeadlinePrecision.UNRESOLVED,
        sources=(source(available=False),),
    )
    show(panel, ActionFilter.WAITING, waiting)

    assert row_text(panel, ActionFilter.WAITING) == (
        "Action 2 — due “soon” · waiting for someone, since today · source no longer in local mail"
    )


def test_a_completed_action_is_never_shown_overdue_or_carried_over(panel: ActionsPanel) -> None:
    done = action(
        3,
        status=ActionStatus.COMPLETED,
        completed_at_utc=NOW,
        deadline_text="by Friday",
        deadline_precision=DeadlinePrecision.DATE,
        deadline_date=date(2026, 9, 1),
        deadline_timezone="UTC",
        created_at_utc=datetime(2026, 8, 1, tzinfo=UTC),
    )
    show(panel, ActionFilter.COMPLETED, done)

    assert row_text(panel, ActionFilter.COMPLETED) == "Action 3 — due Tue Sep 1"


def test_buttons_follow_the_selection_the_tab_and_busy_state(panel: ActionsPanel) -> None:
    assert not panel.edit_button.isEnabled()
    show(panel, ActionFilter.OPEN, action(1))
    show(
        panel,
        ActionFilter.COMPLETED,
        action(2, status=ActionStatus.COMPLETED, completed_at_utc=NOW),
    )

    assert panel.edit_button.isEnabled() and panel.complete_button.text() == "Com&plete"
    panel.tabs.setCurrentIndex(2)
    assert panel.complete_button.text() == "Re&open"
    panel.set_busy(True)
    assert not panel.complete_button.isEnabled()
    panel.set_busy(False)
    assert panel.complete_button.isEnabled()


def test_buttons_and_activation_request_changes_for_the_selected_action(
    panel: ActionsPanel,
) -> None:
    first, completed = action(1), action(2, status=ActionStatus.COMPLETED, completed_at_utc=NOW)
    show(panel, ActionFilter.OPEN, first)
    show(panel, ActionFilter.COMPLETED, completed)
    requests: list[tuple[str, Action]] = []
    panel.action_requested.connect(lambda kind, item: requests.append((kind, item)))

    panel.edit_button.click()
    panel.complete_button.click()
    panel.delete_button.click()
    item = panel.lists[ActionFilter.OPEN].item(0)
    panel.lists[ActionFilter.OPEN].itemActivated.emit(item)
    panel.tabs.setCurrentIndex(2)
    panel.complete_button.click()
    panel.set_busy(True)
    panel.delete_button.click()  # Disabled while busy.
    panel._request(DELETE)  # Refused even when called directly.

    assert requests == [
        (EDIT, first),
        (COMPLETE, first),
        (DELETE, first),
        (EDIT, first),
        (REOPEN, completed),
    ]


def test_a_refresh_keeps_the_selected_action_selected(panel: ActionsPanel) -> None:
    show(panel, ActionFilter.OPEN, action(1), action(2), action(3))
    panel.lists[ActionFilter.OPEN].setCurrentRow(2)

    show(panel, ActionFilter.OPEN, action(3), action(1))

    assert panel.selected() == action(3)
    show(panel, ActionFilter.OPEN)
    assert panel.selected() is None
    assert panel.tabs.tabText(0) == "Open (0)"


def test_only_a_gmail_source_can_be_opened(
    panel: ActionsPanel, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened = Mock(return_value=True)
    monkeypatch.setattr(QDesktopServices, "openUrl", opened)

    show(panel, ActionFilter.OPEN, action(1, sources=(source("https://evil.example/x"),)))
    assert not panel.source_button.isEnabled()
    panel._open_source()
    opened.assert_not_called()

    show(panel, ActionFilter.OPEN, action(1, sources=(source(),)))
    assert panel.source_button.isEnabled()
    panel.source_button.click()
    opened.assert_called_once_with(QUrl(GMAIL))


def test_a_reply_draft_needs_an_email_in_local_mail(qtbot: QtBot) -> None:
    panel = ActionsPanel()
    qtbot.addWidget(panel)
    found: list[tuple[object, object]] = []
    panel.draft_requested.connect(lambda kind, action: found.append((kind, action)))
    action = make_action()
    panel.show_actions(
        ActionFilter.OPEN,
        (action,),
        today=date(2026, 9, 28),
        zone=ZoneInfo("UTC"),
        now=datetime(2026, 9, 28, tzinfo=UTC),
    )

    panel._request_draft(DraftKind.REPLY)
    panel._request_draft(DraftKind.NOTE)

    assert not panel.reply_draft.isEnabled()
    assert found == [(DraftKind.NOTE, action)]


def test_thread_activity_reads_in_the_owner_s_zone(panel: ActionsPanel) -> None:
    recent = action(
        5,
        thread=ThreadActivity(
            new_messages=2,
            latest_at_utc=datetime(2026, 9, 29, 18, 2, tzinfo=UTC),
            latest_sender="Sam <b>Lee</b>",
            owner_replied_at_utc=datetime(2026, 9, 30, 15, tzinfo=UTC),
        ),
    )
    older = action(
        6,
        thread=ThreadActivity(
            new_messages=1,
            latest_at_utc=datetime(2026, 9, 20, 14, 30, tzinfo=UTC),
            latest_sender="sam@example.com",
            owner_replied_at_utc=datetime(2026, 9, 26, 12, tzinfo=UTC),
        ),
    )
    quiet = action(7, thread=ThreadActivity())

    panel.show_actions(
        ActionFilter.OPEN,
        (recent, older, quiet),
        today=TODAY,
        zone=ZoneInfo("America/Toronto"),
        now=NOW,
    )

    assert row_text(panel, ActionFilter.OPEN, 0) == (
        "Action 5 — 2 new in thread, latest Tue 14:02 from Sam <b>Lee</b> · you replied Wed"
    )
    # More than a week back, a weekday would be ambiguous: the date is shown instead.
    assert row_text(panel, ActionFilter.OPEN, 1) == (
        "Action 6 — 1 new in thread, latest Sun Sep 20, 10:30 from sam@example.com · "
        "you replied Sat Sep 26"
    )
    assert row_text(panel, ActionFilter.OPEN, 2) == "Action 7"


def test_mark_seen_is_offered_only_for_an_action_with_activity(panel: ActionsPanel) -> None:
    quiet = action(1, thread=ThreadActivity())
    replied = action(
        2, thread=ThreadActivity(owner_replied_at_utc=datetime(2026, 10, 2, tzinfo=UTC))
    )
    untracked = action(3)
    show(panel, ActionFilter.OPEN, quiet, replied, untracked)
    requests: list[tuple[str, Action]] = []
    panel.action_requested.connect(lambda kind, item: requests.append((kind, item)))

    assert panel.seen_button.text() == "Mar&k seen"
    assert not panel.seen_button.isEnabled()
    panel._mark_seen()  # Refused even when called directly.
    panel.lists[ActionFilter.OPEN].setCurrentRow(2)
    assert not panel.seen_button.isEnabled()
    panel.lists[ActionFilter.OPEN].setCurrentRow(1)
    assert panel.seen_button.isEnabled()
    panel.seen_button.click()
    panel.set_busy(True)
    assert not panel.seen_button.isEnabled()
    panel._mark_seen()

    assert requests == [(SEEN, replied)]


# The rows and the detail pane.

LONG = "x" * 300  # No spaces: it can only wrap anywhere.
HOSTILE = '<b>bold</b> <a href="https://evil.example">link</a> &amp; <img src=x>'


def steps(*done: bool) -> tuple[ActionStep, ...]:
    return tuple(
        ActionStep(step_id=number + 1, position=number, text=f"Step {number + 1}", done=flag)
        for number, flag in enumerate(done)
    )


def pending_proposal(number: int) -> ActionProposal:
    return make_proposal(
        action_public_id=f"00000000-0000-4000-8000-{number:012d}",
        action_title=f"Action {number}",
    )


def detail_texts(panel: ActionsPanel) -> list[str]:
    """The detail's visible label text, top to bottom."""
    content = panel.detail.widget()
    assert content is not None
    return [
        label.text()
        for label in content.findChildren(QLabel)
        if label.isVisibleTo(content) and label.text()
    ]


def shown_buttons(panel: ActionsPanel) -> list[str]:
    return [button.objectName() for button in panel.detail.buttons()]


def test_an_open_action_s_row() -> None:
    open_action = action(
        1,
        target_date=date(2026, 10, 6),
        suggested_target_date=date(2026, 10, 6),
        target_reason=TargetReason.WORKING_DAY_BEFORE,
        deadline_text="by Wednesday",
        deadline_precision=DeadlinePrecision.DATE,
        deadline_date=date(2026, 10, 7),
        deadline_timezone="UTC",
        created_at_utc=datetime(2026, 10, 1, tzinfo=UTC),
        steps=steps(True, True, False, False, False),
        sources=(source(available=False),),
    )

    row = action_row(open_action, today=TODAY, zone=UTC_ZONE, now=NOW)

    assert row == ActionRow(
        title="Action 1",
        meta="Target Tue Oct 6 · Due Wed Oct 7 · 2/5 steps",
        chips=(),
        muted=False,
    )


def test_a_target_the_owner_moved_drops_the_suggested_reason() -> None:
    moved = action(
        1,
        target_date=date(2026, 10, 9),
        suggested_target_date=date(2026, 10, 6),
        target_reason=TargetReason.WORKING_DAY_BEFORE,
    )

    assert action_row(moved, today=TODAY, zone=UTC_ZONE, now=NOW).meta == "Target Fri Oct 9"


def test_a_waiting_action_s_row_says_how_long_it_has_waited() -> None:
    waiting = action(2, ownership=ActionOwnership.WAITING_FOR)

    row = action_row(waiting, today=TODAY, zone=UTC_ZONE, now=NOW)

    assert row == ActionRow(
        title="Action 2", meta="Waiting since today", chips=(), muted=False, avatar="?"
    )
    later = action_row(waiting, today=TODAY + timedelta(days=3), zone=UTC_ZONE, now=NOW)
    assert later.meta == "Waiting 3 days"


def test_a_row_names_whom_it_waits_on_or_is_for() -> None:
    """B08: a waiting row leads with the person and the wait; the owner's own, with whom
    it is for."""
    waiting = action(
        2,
        ownership=ActionOwnership.WAITING_FOR,
        person="Alex Kim",
        waiting_since_utc=NOW - timedelta(days=1),
        target_date=date(2026, 10, 5),
        sources=(source(),),
    )

    row = action_row(waiting, today=TODAY, zone=UTC_ZONE, now=NOW)

    assert (row.meta, row.avatar) == ("Alex Kim · 1 day · Follow up Mon Oct 5", "AK")
    assert describe(waiting, today=TODAY, zone=UTC_ZONE, now=NOW) == (
        "Action 2 — Follow up Mon Oct 5 · waiting on Alex Kim, 1 day"
    )
    mine = action(1, person="Sam")
    assert action_row(mine, today=TODAY, zone=UTC_ZONE, now=NOW).meta == "For Sam"
    assert describe(mine, today=TODAY, zone=UTC_ZONE, now=NOW) == "Action 1 — for Sam"
    one_name = action(3, ownership=ActionOwnership.WAITING_FOR, person="Priya")
    assert action_row(one_name, today=TODAY, zone=UTC_ZONE, now=NOW).avatar == "PR"


def test_a_completed_action_s_row_is_muted_with_its_day_in_the_owner_s_zone() -> None:
    done = action(
        3,
        status=ActionStatus.COMPLETED,
        completed_at_utc=datetime(2026, 10, 7, 2, tzinfo=UTC),  # Oct 6 in Toronto.
        deadline_text="by Friday",
        deadline_precision=DeadlinePrecision.DATE,
        deadline_date=date(2026, 9, 1),
        deadline_timezone="UTC",
        steps=steps(True, True),
    )

    row = action_row(done, today=TODAY, zone=ZoneInfo("America/Toronto"), now=NOW)

    # Never Overdue once completed.
    assert row == ActionRow(
        title="Action 3",
        meta="Completed Tue Oct 6 · Due Tue Sep 1 · 2/2 steps",
        chips=(),
        muted=True,
    )


def test_overdue_activity_and_proposal_chips() -> None:
    busy = action(
        4,
        deadline_text="by Friday",
        deadline_precision=DeadlinePrecision.DATE,
        deadline_date=date(2026, 10, 2),
        deadline_timezone="UTC",
        thread=ThreadActivity(
            new_messages=3, latest_at_utc=NOW, latest_sender="Sam", owner_replied_at_utc=NOW
        ),
        proposals=(pending_proposal(4),),
    )

    row = action_row(busy, today=TODAY, zone=UTC_ZONE, now=NOW)

    assert row.chips == (
        Chip("Overdue", ChipTone.WARNING),
        Chip("3 new in thread", ChipTone.ACCENT),
        Chip("Proposes completing an action", ChipTone.ACCENT),
    )


def test_seen_activity_and_a_reply_alone_have_no_chip() -> None:
    replied = action(5, thread=ThreadActivity(owner_replied_at_utc=NOW))
    quiet = action(6, thread=ThreadActivity())

    for each in (replied, quiet):
        assert action_row(each, today=TODAY, zone=UTC_ZONE, now=NOW).chips == ()


def test_each_item_keeps_describe_s_text_and_carries_its_row(panel: ActionsPanel) -> None:
    shown = action(1, title=HOSTILE, steps=steps(True, False))
    show(panel, ActionFilter.OPEN, shown)

    item = panel.lists[ActionFilter.OPEN].item(0)
    assert item is not None
    assert item.text() == describe(shown, today=TODAY, zone=UTC_ZONE, now=NOW)
    assert item.data(ROW_ROLE) == action_row(shown, today=TODAY, zone=UTC_ZONE, now=NOW)
    assert item.toolTip() == ""


def paint(row: ActionRow, rect: QRect, *, selected: bool, focused: bool = True) -> QImage:
    """``row`` painted at ``rect`` on a transparent 400 × 200 image."""
    image = QImage(400, 200, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    model = QStandardItemModel()
    item = QStandardItem()
    item.setData(row, ROW_ROLE)
    model.appendRow(item)
    option = QStyleOptionViewItem()
    option.rect = rect
    state = QStyle.StateFlag.State_Enabled
    if selected:
        state |= QStyle.StateFlag.State_Selected
    if selected and focused:
        state |= QStyle.StateFlag.State_HasFocus | QStyle.StateFlag.State_KeyboardFocusChange
    option.state = state
    painter = QPainter(image)
    ActionRowDelegate().paint(painter, option, model.index(0, 0))
    painter.end()
    return image


@pytest.mark.parametrize("mode", list(ThemeMode))
@pytest.mark.parametrize("focused", [False, True])
def test_selected_action_circle_fills_without_marking_it_complete(
    qapp: QApplication, themed: None, mode: ThemeMode, focused: bool
) -> None:
    apply_theme(qapp, mode)
    rect = QRect(40, 50, 300, row_height(False))
    row = ActionRow(title="Selected task", meta="", chips=(), muted=False)
    # Sample inside the circle, away from both its outline and a completion checkmark.
    point = QPoint(rect.left() + 27, rect.top() + 18)
    selected = paint(row, rect, selected=True, focused=focused)
    unselected = paint(row, rect, selected=False)
    assert selected.pixelColor(point).name() == current_tokens().accent_fg
    assert unselected.pixelColor(point).alpha() == 0

    completed = ActionRow(title="Completed task", meta="", chips=(), muted=True)
    for chosen in (False, True):
        image = paint(completed, rect, selected=chosen, focused=focused)
        assert image.pixelColor(point).name() == current_tokens().nav_selection


@pytest.mark.parametrize("selected", [False, True])
def test_painting_stays_inside_each_row(qtbot: QtBot, selected: bool) -> None:
    row = ActionRow(
        title=LONG,
        meta=LONG,
        chips=(
            Chip("Overdue", ChipTone.WARNING),
            Chip("12 new in thread", ChipTone.ACCENT),
            Chip("Proposes completing an action", ChipTone.ACCENT),
        ),
        muted=False,
    )
    rect = QRect(40, 50, 160, row_height(True))

    image = paint(row, rect, selected=selected)

    outside = [
        (x, y)
        for y in range(image.height())
        for x in range(image.width())
        if not rect.contains(x, y) and image.pixelColor(x, y).alpha()
    ]
    assert outside == []
    assert any(  # Something was painted.
        image.pixelColor(x, y).alpha()
        for y in range(rect.top(), rect.bottom())
        for x in range(rect.left(), rect.right())
    )


def test_the_delegate_sizes_rows_by_their_chips(panel: ActionsPanel) -> None:
    show(panel, ActionFilter.OPEN, action(1), action(2, proposals=(pending_proposal(2),)))
    listing = panel.lists[ActionFilter.OPEN]
    delegate = listing.itemDelegate()
    option = QStyleOptionViewItem()

    heights = [delegate.sizeHint(option, listing.model().index(row, 0)) for row in (0, 1)]

    assert heights == [QSize(0, row_height(False)), QSize(0, row_height(True))]


def test_the_detail_shows_every_section_with_its_buttons(panel: ActionsPanel) -> None:
    rich = action(
        1,
        target_date=date(2026, 10, 6),
        deadline_text="by Friday",
        deadline_precision=DeadlinePrecision.DATE,
        deadline_date=date(2026, 10, 2),
        deadline_timezone="UTC",
        notes="Bring the printed copy.",
        steps=steps(True, False),
        thread=ThreadActivity(
            new_messages=2,
            latest_at_utc=datetime(2026, 10, 3, 8, 30, tzinfo=UTC),
            latest_sender="Sam",
            owner_replied_at_utc=datetime(2026, 10, 2, 9, tzinfo=UTC),
        ),
        proposals=(pending_proposal(1),),
        sources=(source("https://evil.example/x"), source(), source(available=False)),
    )
    show(panel, ActionFilter.OPEN, rich)

    assert detail_texts(panel) == [
        "Yours · open",
        "Action 1",
        "Due Fri Oct 2 · Overdue",
        "Target Tue Oct 6",
        "Bring the printed copy.",
        "OWNER",
        "You",
        "PROJECT",
        "Unassigned",
        "SOURCE EMAIL",
        "Q3 deck",
        "Plan · 1 of 2 done",
        "✓ Step 1",
        "○ Step 2",
        "Thread",
        "2 new in thread, latest Sat 08:30 from Sam",
        "You replied Fri",
        "A follow-up reply proposes an update to this action.",
        "Sources",
        "Q3 deck",
        "alex@example.com · Received Mon Sep 28",
        "Q3 deck",
        "alex@example.com · Received Mon Sep 28",
        "Q3 deck",
        "alex@example.com · Received Mon Sep 28 · No longer in local mail",
    ]
    deadline = panel.detail.findChild(QLabel, "actionDeadline")
    assert deadline is not None and deadline.property("tone") == "warning"
    content = panel.detail.widget()
    assert content is not None
    done_step = next(label for label in content.findChildren(QLabel) if label.text() == "✓ Step 1")
    assert done_step.property("tone") == "muted"
    assert shown_buttons(panel) == [
        "completeButton",
        "draftButton",
        "editButton",
        "deleteButton",
        "seenButton",
        "proposalsButton",
        "sourceButton",
    ]
    # Open source follows the first Gmail source, the second here.
    panel.resize(900, 900)
    panel.show()
    qt_wait_layout(panel)
    source_row = panel.source_button.mapTo(content, QPoint(0, 0)).y()
    later = [
        label.mapTo(content, QPoint(0, 0)).y()
        for label in content.findChildren(QLabel, "sourceSubject")
    ]
    assert later[1] < source_row < later[2]


def qt_wait_layout(widget: QWidget) -> None:
    QApplication.processEvents()
    layout = widget.layout()
    if layout is not None:
        layout.activate()
    QApplication.processEvents()


def test_a_quiet_action_shows_only_what_it_has(panel: ActionsPanel) -> None:
    show(panel, ActionFilter.WAITING, action(2, ownership=ActionOwnership.WAITING_FOR))
    panel.show_view(ActionFilter.WAITING)

    assert detail_texts(panel) == [
        "Waiting for someone · open",
        "Action 2",
        "WAITING ON",
        "Not set",
        "WAITING SINCE",
        "Sat Oct 3",
        "FOLLOW-UP",
        "No date set",
    ]  # Nothing calls for a follow-up yet, so there is no prompt.
    assert shown_buttons(panel) == ["draftButton", "completeButton", "editButton", "deleteButton"]


def test_a_completed_action_s_detail(panel: ActionsPanel) -> None:
    done = action(
        3,
        status=ActionStatus.COMPLETED,
        completed_at_utc=datetime(2026, 10, 6, 12, tzinfo=UTC),
        steps=steps(True),
    )
    show(panel, ActionFilter.COMPLETED, done)
    panel.show_view(ActionFilter.COMPLETED)

    assert detail_texts(panel)[:2] == ["Completed Tue Oct 6", "Action 3"]
    assert panel.complete_button.text() == "Re&open"


@pytest.mark.parametrize("view", list(ActionFilter))
def test_each_view_has_its_empty_state(panel: ActionsPanel, view: ActionFilter) -> None:
    show(panel, view)
    panel.show_view(view)

    assert detail_texts(panel) == [EMPTY_TEXT[view]]
    assert shown_buttons(panel) == []
    assert EMPTY_TEXT == {
        ActionFilter.OPEN: "No open actions. Create a task or accept a suggestion in a brief.",
        ActionFilter.WAITING: "Nothing you're waiting for.",
        ActionFilter.COMPLETED: "No completed actions yet.",
    }


def test_hostile_text_stays_literal_and_out_of_tooltips(panel: ActionsPanel) -> None:
    hostile = action(
        1,
        title=HOSTILE[:200],
        notes=HOSTILE,
        steps=(ActionStep(step_id=1, position=0, text=HOSTILE, done=False),),
        thread=ThreadActivity(new_messages=1, latest_at_utc=NOW, latest_sender=HOSTILE),
        sources=(
            source().model_copy(
                update={"subject": HOSTILE, "sender_address": "<b>x</b>@evil.example"}
            ),
        ),
    )
    show(panel, ActionFilter.OPEN, hostile)

    content = panel.detail.widget()
    assert content is not None
    labels = [label for label in content.findChildren(QLabel) if label.isVisibleTo(content)]
    texts = [label.text() for label in labels]
    for expected in (
        HOSTILE[:200],
        HOSTILE,
        f"○ {HOSTILE}",
        f"1 new in thread, latest Sat 09:00 from {HOSTILE}",
        "<b>x</b>@evil.example · Received Mon Sep 28",
    ):
        assert expected in texts
    for label in labels:
        assert label.textFormat() is Qt.TextFormat.PlainText
        assert not label.openExternalLinks()
        assert label.toolTip() == ""
    for button in panel.detail.all_buttons():
        assert HOSTILE not in button.toolTip()


def test_show_view_moves_the_tab_bar_and_the_list(panel: ActionsPanel) -> None:
    assert isinstance(panel.tabs, QTabBar)
    assert [panel.tabs.tabText(index) for index in range(panel.tabs.count())] == [
        "Open",
        "Waiting",
        "Completed",
    ]
    for view in (ActionFilter.WAITING, ActionFilter.COMPLETED, ActionFilter.OPEN):
        panel.show_view(view)
        assert panel.view() is view
        assert panel.tabs.currentIndex() == list(ActionFilter).index(view)
        assert panel.stack.currentWidget() is panel.lists[view]
    panel.tabs.setCurrentIndex(1)  # A click on a tab.
    assert panel.stack.currentWidget() is panel.lists[ActionFilter.WAITING]


def test_the_selection_and_its_detail_stay_on_the_same_action(panel: ActionsPanel) -> None:
    show(panel, ActionFilter.OPEN, action(1), action(2), action(3))
    panel.lists[ActionFilter.OPEN].setCurrentRow(1)
    assert detail_texts(panel)[1] == "Action 2"

    show(panel, ActionFilter.OPEN, action(3), action(2, notes="Updated"), action(1))

    assert panel.selected() == action(2, notes="Updated")
    assert panel.lists[ActionFilter.OPEN].currentRow() == 1
    assert "Updated" in detail_texts(panel)
    # Refreshing another view leaves this one's detail alone.
    show(panel, ActionFilter.WAITING, action(9, ownership=ActionOwnership.WAITING_FOR))
    assert detail_texts(panel)[1] == "Action 2"


def test_a_long_unbroken_title_never_widens_the_page(
    qtbot: QtBot, qapp: QApplication, themed: None
) -> None:
    apply_theme(qapp)

    def narrowest(shown: Action) -> int:
        panel = ActionsPanel()
        qtbot.addWidget(panel)
        show(panel, ActionFilter.OPEN, shown)
        panel.resize(680, 520)
        show_window(qtbot, panel)
        content = panel.detail.widget()
        assert content is not None
        assert content.width() <= panel.detail.viewport().width()
        return panel.minimumSizeHint().width()

    # Past the 200-character cap a stored title can have, so nothing shorter can widen it.
    long_title = action(1).model_copy(update={"title": LONG})
    long_everything = long_title.model_copy(
        update={"notes": LONG, "steps": (ActionStep(step_id=1, position=0, text=LONG, done=False),)}
    )
    plain = narrowest(action(1))
    assert narrowest(long_title) == plain
    assert narrowest(long_everything) == plain


def many_steps(number: int) -> Action:
    texts = [f"Step {index + 1}: " + "a long line of plan " * 6 for index in range(30)]
    return action(
        number,
        steps=tuple(
            ActionStep(step_id=index + 1, position=index, text=text, done=False)
            for index, text in enumerate(texts)
        ),
    )


def test_page_keys_scroll_the_detail_while_the_list_fits(qtbot: QtBot) -> None:
    panel = ActionsPanel()
    qtbot.addWidget(panel)
    show(panel, ActionFilter.OPEN, many_steps(1), action(2))
    panel.resize(680, 420)
    show_window(qtbot, panel)
    listing, bar = panel.lists[ActionFilter.OPEN], panel.detail.verticalScrollBar()
    assert listing.verticalScrollBar().maximum() == 0  # Two rows fit.
    assert bar.maximum() > 0  # The first action's plan doesn't.

    QTest.keyClick(listing, Qt.Key.Key_PageDown)
    assert bar.value() > 0
    assert listing.currentRow() == 0  # The selection stays put.
    QTest.keyClick(listing, Qt.Key.Key_PageUp)
    assert bar.value() == 0


def test_page_keys_page_a_list_that_scrolls(qtbot: QtBot) -> None:
    panel = ActionsPanel()
    qtbot.addWidget(panel)
    show(panel, ActionFilter.OPEN, *(many_steps(number) for number in range(1, 21)))
    panel.resize(680, 420)
    show_window(qtbot, panel)
    listing, bar = panel.lists[ActionFilter.OPEN], panel.detail.verticalScrollBar()
    assert listing.verticalScrollBar().maximum() > 0  # 20 rows don't fit.

    QTest.keyClick(listing, Qt.Key.Key_PageDown)

    assert listing.currentRow() > 0  # The list paged…
    assert bar.value() == 0  # …and the new action's detail starts at the top.


def test_the_row_names_the_target_and_the_detail_gives_its_reason(panel: ActionsPanel) -> None:
    suggested = action(
        1,
        target_date=date(2026, 10, 2),
        suggested_target_date=date(2026, 10, 2),
        target_reason=TargetReason.WORKING_DAY_BEFORE,
    )
    show(panel, ActionFilter.OPEN, suggested)

    assert action_row(suggested, today=TODAY, zone=UTC_ZONE, now=NOW).meta == "Target Fri Oct 2"
    assert "Target Fri Oct 2, one working day before the deadline" in detail_texts(panel)


@pytest.mark.parametrize(
    ("minutes", "shown"),
    [
        (1, "Estimate 1 min"),
        (45, "Estimate 45 mins"),
        (120, "Estimate 2 hrs"),
        (90, "Estimate 1 hr 30 mins"),
    ],
)
def test_the_detail_shows_the_owner_s_estimate_under_the_target(
    panel: ActionsPanel, minutes: int, shown: str
) -> None:
    show(
        panel, ActionFilter.OPEN, action(1, target_date=date(2026, 10, 2), estimate_minutes=minutes)
    )

    texts = detail_texts(panel)
    assert texts[texts.index("Target Fri Oct 2") + 1] == shown
    label = panel.detail.findChild(QLabel, "actionEstimate")
    assert label is not None and label.text() == shown


def test_dates_in_another_year_name_it(panel: ActionsPanel) -> None:
    january = date(2027, 1, 8)
    old = action(
        1,
        target_date=date(2027, 1, 4),
        thread=ThreadActivity(
            new_messages=1,
            latest_at_utc=datetime(2026, 12, 20, 15, 30, tzinfo=UTC),
            latest_sender="Sam",
            owner_replied_at_utc=datetime(2026, 12, 21, 12, tzinfo=UTC),
        ),
        sources=(source(),),  # Received Sep 28, 2026.
    )

    panel.show_actions(
        ActionFilter.OPEN,
        (old,),
        today=january,
        zone=UTC_ZONE,
        now=datetime(2027, 1, 8, 9, tzinfo=UTC),
    )

    assert row_text(panel, ActionFilter.OPEN) == (
        "Action 1 — Target Mon Jan 4 · carried over · 1 new in thread, latest Sun Dec 20, "
        "2026, 15:30 from Sam · you replied Mon Dec 21, 2026"
    )
    assert "alex@example.com · Received Mon Sep 28, 2026" in detail_texts(panel)


def test_the_state_line_says_when_an_action_is_carried_over() -> None:
    def state(**fields: object) -> str:
        made = action(1, **fields)
        return state_text(made, action_details(made, today=TODAY, zone=UTC_ZONE, now=NOW))

    earlier = datetime(2026, 10, 1, 9, tzinfo=UTC)  # A day before TODAY.
    assert state() == "Yours · open"
    assert state(created_at_utc=earlier) == "Yours · open · carried over"
    assert state(ownership=ActionOwnership.WAITING_FOR, created_at_utc=earlier) == (
        "Waiting for someone · open · carried over"
    )
    done = state(status=ActionStatus.COMPLETED, completed_at_utc=NOW, created_at_utc=earlier)
    assert done == "Completed Sat Oct 3"  # Never carried over once completed.


def test_the_detail_shows_carried_over(panel: ActionsPanel) -> None:
    show(panel, ActionFilter.OPEN, action(1, created_at_utc=datetime(2026, 10, 1, tzinfo=UTC)))

    assert detail_texts(panel)[:2] == ["Yours · open · carried over", "Action 1"]


@pytest.mark.parametrize(
    ("kind", "label", "undo_label"),
    [
        (ActionActivityKind.SOURCE_ADDED, "Source added", "Undid email addition"),
        (
            ActionActivityKind.SUGGESTION_ACCEPTED,
            "Suggestion accepted",
            "Undid suggestion acceptance",
        ),
    ],
)
def test_activity_distinguishes_new_sources_from_suggestion_decisions(
    qtbot: QtBot, kind: ActionActivityKind, label: str, undo_label: str
) -> None:
    panel = ActionsPanel()
    qtbot.addWidget(panel)
    action = make_action(
        revision=3,
        activity=(
            ActionActivity(revision=2, kind=kind, occurred_at_utc=NOW),
            ActionActivity(
                revision=3, kind=ActionActivityKind.UNDO, occurred_at_utc=NOW, undo_of_revision=2
            ),
        ),
    )
    panel.show_actions(ActionFilter.OPEN, (action,), today=TODAY, zone=UTC_ZONE, now=NOW)
    labels = panel.detail.findChildren(QLabel, "actionActivityEntry")
    assert any(item.text().startswith(label) for item in labels)
    assert any(item.text().startswith(undo_label) for item in labels)


def test_priority_and_tags_read_in_the_row_the_text_and_the_detail(panel: ActionsPanel) -> None:
    tagged = action(
        1,
        target_date=date(2026, 10, 6),
        priority=ActionPriority.HIGH,
        tags=("<b>Home</b>", "errands"),
        steps=steps(True, False),
    )

    row = action_row(tagged, today=TODAY, zone=UTC_ZONE, now=NOW)
    assert row.meta == "Target Tue Oct 6 · High priority · 1/2 steps · Tags: <b>Home</b>, errands"
    assert describe(tagged, today=TODAY, zone=UTC_ZONE, now=NOW) == (
        "Action 1 — Target Tue Oct 6 · High priority · 1/2 steps · tagged <b>Home</b>, errands"
    )
    show(panel, ActionFilter.OPEN, tagged)
    label = panel.detail.findChild(QLabel, "actionPriorityTags")
    assert label is not None
    assert label.text() == "High priority · Tags: <b>Home</b>, errands"
    assert label.textFormat() is Qt.TextFormat.PlainText  # The owner's text, never markup.


def test_an_action_without_priority_or_tags_shows_neither(panel: ActionsPanel) -> None:
    show(panel, ActionFilter.OPEN, action(1))
    assert panel.detail.findChild(QLabel, "actionPriorityTags") is None
    assert action_row(action(1), today=TODAY, zone=UTC_ZONE, now=NOW).meta == NO_DATES


def test_the_filter_offers_tags_by_key(panel: ActionsPanel) -> None:
    panel.show_groups((), (), ("errands", "Home"))
    items = [
        (panel.group.itemText(index), panel.group.itemData(index))
        for index in range(panel.group.count())
    ]
    assert items == [
        ("All projects", "all"),
        ("Unassigned", "unassigned"),
        ("Tag: errands", "tag:errands"),
        ("Tag: Home", "tag:home"),
    ]
    changes: list[None] = []
    panel.group_changed.connect(lambda: changes.append(None))
    panel.group.setCurrentIndex(3)
    assert panel.grouping() == ("tag", "home")
    assert changes == [None]
    panel.show_groups((), (), ("HOME",))  # Respelt: the same tag stays chosen.
    assert panel.grouping() == ("tag", "home") and panel.group.currentText() == "Tag: HOME"
    panel.show_groups((), (), ())  # No task has it any more: back to all tasks.
    assert panel.grouping() == ("all", None)
