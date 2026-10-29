"""A widget moved to another layout leaves PySide no wrapper of the layout item Qt deleted.

Such a wrapper stays registered at the freed item's address, and a QObject made there later
gets it back as its own: Windows CI crashed in QCompleter's constructor that way. Each test
here holds the moved widget's own item wrapper, so Qt reusing the freed memory for another
item can't hide one left valid; ``conftest.py`` also checks every UI test with
``layout_items.stale_item_wrappers``."""

from PySide6.QtWidgets import QHBoxLayout, QLabel, QLayout, QLayoutItem, QPushButton, QWidget
from pytestqt.qtbot import QtBot
from shiboken6 import Shiboken

from mailbrief.ui.layouts import take_widget
from mailbrief.ui.main_window import MainWindow
from tests.ui.layout_items import stale_item_wrappers
from tests.ui.test_draft_editor import make_draft
from tests.ui.test_workflow import FakeBackend


def held_item(layout: QLayout, widget: QWidget) -> QLayoutItem:
    """``widget``'s item in ``layout``, through a wrapper PySide keeps (as ``addLayout``'s)."""
    item = layout.itemAt(layout.indexOf(widget))
    assert item is not None and Shiboken.isValid(item)
    return item


def filled_row(qtbot: QtBot) -> tuple[QWidget, QLabel, QLayoutItem, QHBoxLayout]:
    """A label in a row added to the page's layout once filled, so PySide wraps its item;
    and another row to move it to."""
    page = QWidget()
    qtbot.addWidget(page)
    outer = QHBoxLayout(page)
    row = QHBoxLayout()
    label = QLabel("Moves")
    row.addWidget(label)
    outer.addLayout(row)
    item = held_item(row, label)  # The wrapper addLayout made, not a new one.
    other = QHBoxLayout()
    outer.addLayout(other)
    return page, label, item, other


def test_qt_alone_leaves_the_moved_widget_s_item_wrapper_valid(qtbot: QtBot) -> None:
    _page, label, item, other = filled_row(qtbot)
    other.addWidget(label)  # Qt deletes the row's item for the label; PySide isn't told.
    assert Shiboken.isValid(item)  # What take_widget prevents.
    Shiboken.invalidate(item)  # Not left for the check after the test.


def test_take_widget_invalidates_the_item_wrapper_before_a_move(qtbot: QtBot) -> None:
    _page, label, item, other = filled_row(qtbot)
    take_widget(label)
    other.addWidget(label)
    assert not Shiboken.isValid(item)
    assert other.indexOf(label) == 0


def test_take_widget_finds_a_nested_layout_and_leaves_a_free_widget(qtbot: QtBot) -> None:
    page, label, item, _other = filled_row(qtbot)
    take_widget(label)
    assert not Shiboken.isValid(item)
    assert label.parentWidget() is page  # Still the page's, until another layout takes it.
    loose = QLabel("Loose")
    qtbot.addWidget(loose)
    take_widget(loose)  # No parent, no layout: nothing to do.
    take_widget(label)  # In no layout now.


def test_the_header_takes_a_page_action_out_of_its_layout(qtbot: QtBot) -> None:
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    page = QWidget(window)
    outer = QHBoxLayout(page)
    row = QHBoxLayout()
    button = QPushButton("Act")
    row.addWidget(button)
    outer.addLayout(row)
    item = held_item(row, button)
    window.workspace.add_header_action("today", button)
    assert not Shiboken.isValid(item)
    assert button.parentWidget() is not page


def test_the_draft_editor_s_buttons_change_cells_cleanly(qtbot: QtBot) -> None:
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    editor = window.draft_editor
    reply_card = QWidget(window)
    reply_card.setObjectName("inlineReplyCard")
    grid = editor._buttons
    for host, moved in (
        (window.drafts_panel.editor_host, (editor.checkpoint_button, editor.more_button)),
        (None, (editor.copy_text_button, editor.checkpoint_button, editor.close_button)),
        (reply_card, (editor.checkpoint_button, editor.more_button, editor.close_button)),
        (None, (editor.copy_text_button, editor.ai_button, editor.close_button)),
    ):
        items = [held_item(grid, button) for button in moved]
        editor.set_embedded(host, window=window)
        assert not any(Shiboken.isValid(item) for item in items)


def test_the_drafts_delete_button_moves_into_the_editor_and_back(qtbot: QtBot) -> None:
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    panel = window.drafts_panel
    window._show_page("drafts")
    listed = held_item(panel._list_buttons, panel.delete_button)
    window._edit_draft(make_draft())  # On the Drafts page, so inline.
    assert not Shiboken.isValid(listed)
    in_editor = held_item(window.draft_editor._buttons, panel.delete_button)
    panel.restore_list_controls()
    assert not Shiboken.isValid(in_editor)
    assert panel._list_buttons.indexOf(panel.delete_button) == 1


def test_the_window_leaves_no_stale_layout_item_wrappers(qtbot: QtBot) -> None:
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    assert stale_item_wrappers() == []  # Construction hosts each page's header actions.
    for key in ("today", "actions", "drafts", "briefs", "calendar", "inbox", "today"):
        window._show_page(key)
    assert stale_item_wrappers() == []
