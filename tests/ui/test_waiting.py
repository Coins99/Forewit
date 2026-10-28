"""B08 in the desktop: who a task waits on or is for, its wait, and when to follow up."""

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialogButtonBox, QLabel, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.domain.actions import Action, ActionEdit, ActionFilter, ActionSource, ThreadActivity
from mailbrief.domain.analysis import ActionOwnership, DeadlinePrecision
from mailbrief.domain.events import ActionMarker, MarkerKind
from mailbrief.domain.today import AttentionItem, AttentionReason
from mailbrief.ui.action_detail import follow_up_prompt, owner_text
from mailbrief.ui.action_editor import ActionEditor
from mailbrief.ui.action_text import action_details, sender_person
from mailbrief.ui.actions_view import ActionsPanel
from mailbrief.ui.calendar_layout import marker_text
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.today_view import attention_row
from tests.factories import make_action
from tests.ui.test_actions_view import NOW, TODAY, UTC_ZONE, detail_texts, show
from tests.ui.test_workflow import FakeBackend
from tests.ui.window_wait import show as show_window

GMAIL = "https://mail.google.com/mail/u/?authuser=me%40x.com#all/t1"


def email(*, name: str | None = None, address: str = "alex@example.com") -> ActionSource:
    return ActionSource(
        provider_message_id="m1",
        subject="Lease",
        sender_address=address,
        sender_name=name,
        web_link=GMAIL,
        received_at_utc=datetime(2026, 9, 28, 12, tzinfo=UTC),
        available=name is not None,
    )


def waiting(**fields: object) -> Action:
    values: dict[str, object] = {
        "title": "Get the signed lease",
        "ownership": ActionOwnership.WAITING_FOR,
        "created_at_utc": NOW - timedelta(days=3),
        "updated_at_utc": NOW - timedelta(days=3),
    }
    return make_action(**(values | fields))


@pytest.fixture
def editor(qtbot: QtBot) -> Iterator[ActionEditor]:
    parent = QWidget()  # Held by this frame: qtbot keeps widgets only weakly.
    qtbot.addWidget(parent)
    yield ActionEditor(parent)


@pytest.fixture
def panel(qtbot: QtBot) -> ActionsPanel:
    result = ActionsPanel()
    qtbot.addWidget(result)
    return result


def saved_edits(editor: ActionEditor) -> list[ActionEdit]:
    found: list[ActionEdit] = []
    editor.save_requested.connect(lambda _action, edit, *_rest: found.append(edit))
    editor.create_requested.connect(lambda edit, *_rest: found.append(edit))
    return found


def save(editor: ActionEditor) -> None:
    button = editor.buttons.button(QDialogButtonBox.StandardButton.Save)
    assert button is not None
    button.click()


def test_the_person_row_follows_whose_task_it_is(editor: ActionEditor) -> None:
    editor.edit(make_action(person="Sam"), TODAY)

    assert (editor.person_label.text(), editor.person.text()) == ("For", "Sam")
    assert editor.person.placeholderText() == "Who it's for, if anyone"
    assert (editor.target_label.text(), editor.has_target.text()) == ("Target", "Target &date")
    assert editor.person.maxLength() == 120
    names = (editor.person_label.objectName(), editor.target_label.objectName())
    assert names == ("taskPersonLabel", "taskTargetLabel")
    assert editor.person_label.accessibleName() == "For"

    editor.owner.setCurrentIndex(1)

    assert editor.person_label.text() == "Waiting on"
    assert editor.person.placeholderText() == "Who you're waiting on"
    assert editor.person.accessibleName() == "Who the task waits on"
    assert editor.person_label.accessibleName() == "Waiting on"
    assert editor.target_label.accessibleName() == "Follow up"
    assert (editor.target_label.text(), editor.has_target.text()) == (
        "Follow up",
        "Follow-up &date",
    )
    assert editor.has_unsaved_input()


def test_saving_carries_the_person_as_typed_and_tidied(editor: ActionEditor) -> None:
    editor.edit(waiting(person="Alex"), TODAY)
    edits = saved_edits(editor)
    editor.person.setText("  Alex \t Kim ")
    assert editor.has_unsaved_input()

    save(editor)

    assert [edit.person for edit in edits] == ["Alex Kim"]


def test_clearing_the_person_names_no_one(editor: ActionEditor) -> None:
    editor.edit(waiting(person="Alex"), TODAY)
    edits = saved_edits(editor)
    editor.person.clear()

    save(editor)

    assert [edit.person for edit in edits] == [None]


@pytest.mark.parametrize(
    ("source", "named"),
    [
        (email(name="Alex Kim"), "Alex Kim"),  # The cached message's display name.
        (email(), "alex@example.com"),  # Gone from local mail: the snapshot's address.
        (email(name="boss@corp.example"), "alex@example.com"),  # A name posing as an address.
        (email(name="  "), "alex@example.com"),
        # The email's text is cleaned as a typed name is: no bidi override, one line,
        # at most 120 characters.
        (email(name="Alex\u202eKim"), "AlexKim"),
        (email(name="Alex\nKim"), "Alex Kim"),
        (email(name="A" * 200), "A" * 120),
    ],
)
def test_use_sender_names_the_email_s_sender(
    editor: ActionEditor, source: ActionSource, named: str
) -> None:
    editor.edit(waiting(sources=(source,)), TODAY)
    assert not editor.use_sender.isHidden()

    editor.use_sender.click()

    assert editor.person.text() == named
    assert sender_person(waiting(sources=(source,))) == named


def test_a_manual_task_offers_no_sender(editor: ActionEditor) -> None:
    editor.edit(waiting(), TODAY)
    assert editor.use_sender.isHidden()
    assert sender_person(waiting()) is None


def test_a_new_task_on_waiting_starts_as_someone_else_s(editor: ActionEditor) -> None:
    editor.edit(make_action(person="Sam"), TODAY)
    edits = saved_edits(editor)

    editor.new_task(TODAY, waiting=True)
    editor.title.setText("Hear back about the quote")
    editor.person.setText("Priya")
    save(editor)

    assert editor.owner.currentText() == "Waiting for someone"
    assert editor.person_label.text() == "Waiting on"
    assert editor.use_sender.isHidden()
    ((edit),) = edits
    assert (edit.ownership, edit.person) == (ActionOwnership.WAITING_FOR, "Priya")
    editor.new_task(TODAY)
    assert (editor.owner.currentText(), editor.person.text()) == ("Mine", "")


def test_the_detail_names_whom_it_waits_on_and_for_how_long(panel: ActionsPanel) -> None:
    show(panel, ActionFilter.WAITING, waiting(person="Alex Kim", target_date=TODAY + timedelta(2)))
    panel.show_view(ActionFilter.WAITING)

    assert detail_texts(panel) == [
        "Waiting on Alex Kim · open · carried over",
        "Get the signed lease",
        "Follow up Mon Oct 5",
        "WAITING ON",
        "Alex Kim",
        "WAITING SINCE",
        "Wed Sep 30 · 3 days",
        "FOLLOW-UP",
        "Mon Oct 5",
        "Plan",  # An open task's field to add a step (B04).
    ]
    assert panel.complete_button.text() == "Mark received"
    assert panel.draft_button.text() == "Draft follow-up"


def test_the_detail_of_the_owner_s_task_names_whom_it_is_for(panel: ActionsPanel) -> None:
    show(
        panel,
        ActionFilter.OPEN,
        make_action(title="Send the slides", person="Sam", created_at_utc=NOW, updated_at_utc=NOW),
    )

    texts = detail_texts(panel)
    assert texts[:2] == ["Yours, for Sam · open", "Send the slides"]
    assert texts[texts.index("OWNER") + 1] == "You, for Sam"


def test_a_name_stays_plain_text(panel: ActionsPanel) -> None:
    hostile = "<b>Alex</b> <a href='https://example.com'>x</a>"
    show(panel, ActionFilter.WAITING, waiting(person=hostile, target_date=TODAY))
    panel.show_view(ActionFilter.WAITING)
    content = panel.detail.widget()
    assert content is not None

    shown = [label for label in content.findChildren(QLabel) if hostile in label.text()]

    assert len(shown) >= 3  # The state line, the fact and the prompt.
    assert all(label.textFormat() is Qt.TextFormat.PlainText for label in shown)
    assert all(not label.toolTip() for label in content.findChildren(QLabel))


def prompt(action: Action) -> tuple[str, str] | None:
    return follow_up_prompt(action, action_details(action, today=TODAY, zone=UTC_ZONE, now=NOW))


def test_the_prompt_comes_only_when_something_calls_for_it() -> None:
    assert prompt(waiting()) is None  # No date and no reply: nothing to do yet.
    assert prompt(waiting(target_date=TODAY + timedelta(days=1))) is None
    assert prompt(make_action(target_date=TODAY)) is None  # The owner's own task.
    assert prompt(waiting(person="Alex", target_date=TODAY)) == (
        "Follow up with Alex",
        "It's your follow-up date. Draft a short check-in.",
    )
    assert prompt(waiting(target_date=TODAY - timedelta(days=2))) == (
        "Time to follow up",
        "Your follow-up date was Thu Oct 1.",
    )
    overdue = waiting(
        person="Alex",
        deadline_text="by Friday",
        deadline_precision=DeadlinePrecision.DATE,
        deadline_date=TODAY - timedelta(days=1),
        deadline_timezone="UTC",
    )
    assert prompt(overdue) == (
        "Follow up with Alex",
        "The deadline has passed. Draft a short check-in.",
    )
    replied = waiting(
        person="Alex",
        target_date=TODAY,
        thread=ThreadActivity(
            new_messages=1, latest_at_utc=NOW - timedelta(hours=1), latest_sender="Alex Kim"
        ),
    )
    assert prompt(replied) == (
        "Alex Kim replied",
        "If it's what you were waiting for, mark it received.",
    )


def test_a_follow_up_the_owner_made_quiets_the_prompt() -> None:
    """A message the owner sent, or marked seen, on or after the day a follow-up fell due
    means it has been followed up; one before that doesn't."""
    due = waiting(person="Alex", target_date=TODAY - timedelta(days=2))  # Thu Oct 1.

    def replied(at: datetime) -> Action:
        return due.model_copy(update={"thread": ThreadActivity(owner_replied_at_utc=at)})

    assert prompt(replied(NOW - timedelta(days=1))) is None  # Fri Oct 2, after Oct 1.
    assert prompt(replied(datetime(2026, 10, 1, 8, tzinfo=UTC))) is None  # On the day.
    assert prompt(replied(datetime(2026, 9, 30, 8, tzinfo=UTC))) == (
        "Follow up with Alex",
        "Your follow-up date was Thu Oct 1.",
    )
    seen = due.model_copy(update={"thread_seen_until_utc": NOW - timedelta(hours=2)})
    assert prompt(seen) is None  # Marking a later message seen counts too.
    late = waiting(
        deadline_text="by Thursday",
        deadline_precision=DeadlinePrecision.DATE,
        deadline_date=date(2026, 10, 1),
        deadline_timezone="UTC",
    )
    assert prompt(late) is not None
    assert prompt(late.model_copy(update={"thread_seen_until_utc": NOW})) is None


def test_the_owner_line_says_whose_it_is() -> None:
    assert owner_text(make_action(), mine="Yours") == "Yours"
    assert owner_text(make_action(person="Sam"), mine="You") == "You, for Sam"
    assert owner_text(waiting(), mine="Yours") == "Waiting for someone"
    assert owner_text(waiting(person="Alex"), mine="Yours") == "Waiting on Alex"


def test_your_day_says_follow_up_and_names_the_person() -> None:
    follow = AttentionItem(
        action=waiting(person="Alex", target_date=TODAY), reason=AttentionReason.PLANNED_TODAY
    )
    earlier = AttentionItem(
        action=waiting(target_date=date(2026, 9, 30)), reason=AttentionReason.PLANNED_EARLIER
    )
    owed = AttentionItem(
        action=make_action(person="Sam", target_date=TODAY), reason=AttentionReason.PLANNED_TODAY
    )

    assert attention_row(follow, UTC_ZONE, TODAY).note == "Follow up today · waiting on Alex"
    assert attention_row(earlier, UTC_ZONE, TODAY).note == "Follow-up date Wed Sep 30 · waiting"
    assert attention_row(owed, UTC_ZONE, TODAY).note == "Today · for Sam"


def test_calendar_markers_say_follow_up_and_name_the_person() -> None:
    def marker(kind: MarkerKind, *, waits: bool, person: str | None) -> str:
        return marker_text(
            ActionMarker(
                kind=kind,
                day=TODAY,
                action_public_id="0c5e2c1d-6b8e-4f55-9d0e-2a7f3b9c1e44",
                action_title="Lease",
                waiting=waits,
                person=person,
            ),
            UTC_ZONE,
        )

    assert (
        marker(MarkerKind.TARGET, waits=True, person="Alex")
        == "Follow up · Lease · waiting on Alex"
    )
    assert marker(MarkerKind.TARGET, waits=True, person=None) == (
        "Follow up · Lease · waiting for others"
    )
    assert marker(MarkerKind.DEADLINE, waits=False, person="Sam") == "Deadline · Lease · for Sam"
    assert marker(MarkerKind.TARGET, waits=False, person=None) == "Target · Lease"


def test_the_filter_offers_each_person(panel: ActionsPanel) -> None:
    panel.show_groups((), (), ("Home",), ("Alex Kim", "Sam"))

    entries = [
        (panel.group.itemText(n), panel.group.itemData(n)) for n in range(panel.group.count())
    ]

    assert entries[-3:] == [
        ("Tag: Home", "tag:home"),
        ("Person: Alex Kim", "person:alex kim"),
        ("Person: Sam", "person:sam"),
    ]
    panel.group.setCurrentIndex(panel.group.count() - 1)
    assert panel.grouping() == ("person", "sam")
    assert panel.group.accessibleName() == "Filter tasks by project, life area, tag or person"


def test_new_task_inline_on_waiting_shows_whom_at_once(editor: ActionEditor, qtbot: QtBot) -> None:
    """The inline form hides Whose; a task started on Waiting still names its person."""
    host = QWidget()
    qtbot.addWidget(host)
    editor.set_embedded(host, window=host)
    form = editor._form

    editor.new_task(TODAY, waiting=True)

    assert form.isRowVisible(editor.person_row) and not form.isRowVisible(editor.owner)
    editor.more_options.setChecked(True)
    editor.more_options.setChecked(False)
    assert form.isRowVisible(editor.person_row)
    editor.new_task(TODAY)
    assert not form.isRowVisible(editor.person_row)  # A task of yours: with More options.
    editor.more_options.setChecked(True)
    assert form.isRowVisible(editor.person_row) and editor.person_label.text() == "For"


def ids(number: int) -> str:
    return f"0c5e2c1d-6b8e-4f55-9d0e-{number:012d}"


async def test_new_task_on_the_waiting_page_stays_there_and_waits(qtbot: QtBot) -> None:
    """New task, Ctrl+N or the palette's New task on Waiting starts a waiting task there; on
    Actions, one of the owner's own."""
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    await window.initialize()
    show_window(qtbot, window)
    try:
        window._show_page("waiting")
        window._request_new_task()
        assert (window._sidebar_key(), window.workspace.header.title.text()) == (
            "waiting",
            "Waiting",
        )
        editor = window.action_editor
        assert (editor.owner.currentText(), editor.person_label.text()) == (
            "Waiting for someone",
            "Waiting on",
        )
        editor.force_close()  # New task waits while the inline editor is open.
        window._show_page("actions")
        window._request_new_task()
        assert window._sidebar_key() == "actions"
        assert editor.owner.currentText() == "Mine"
    finally:
        window.action_editor.force_close()
        window.close()
        await window.shutdown()


async def test_a_filter_on_actions_never_narrows_waiting(qtbot: QtBot) -> None:
    """Waiting shows no filter, so a person (or any) filter chosen on Actions leaves it whole."""
    backend = FakeBackend()
    backend.actions = {
        ActionFilter.OPEN: (
            make_action(public_id=ids(1), title="Send Sam the slides", person="Sam"),
            make_action(public_id=ids(2), title="Book flights"),
        ),
        ActionFilter.WAITING: (
            waiting(public_id=ids(3), person="Alex"),
            waiting(public_id=ids(4), title="Keys from Sam", person="Sam"),
        ),
    }
    window = MainWindow(backend)
    qtbot.addWidget(window)
    await window.initialize()
    show_window(qtbot, window)
    try:
        group = window.actions_panel.group
        group.setCurrentIndex(group.findData("person:sam"))
        assert window.task is not None
        await window.task

        lists = window.actions_panel.lists
        assert lists[ActionFilter.OPEN].count() == 1
        assert lists[ActionFilter.WAITING].count() == 2
        assert window.workspace.sidebar.count_text("waiting") == "2"
    finally:
        window.close()
        await window.shutdown()
