"""A widget moved to another layout leaves PySide no wrapper of the layout item Qt deleted.

Such a wrapper stays registered at the freed item's address, and a QObject made there later
gets it back as its own: Windows CI crashed in QCompleter's constructor that way."""

from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QLayout, QLayoutItem, QWidget
from pytestqt.qtbot import QtBot
from shiboken6 import Shiboken

from mailbrief.ui.layouts import take_widget
from mailbrief.ui.main_window import MainWindow
from tests.ui.test_workflow import FakeBackend


def stale_item_wrappers() -> list[str]:
    """The layout items PySide still has a wrapper for that no layout holds."""
    app = QApplication.instance()
    assert isinstance(app, QApplication)
    held: set[int] = set()
    for widget in app.allWidgets():
        for layout in widget.findChildren(QLayout):
            for index in range(layout.count()):
                item = layout.itemAt(index)
                assert item is not None
                held.add(Shiboken.getCppPointer(item)[0])
    return [
        type(wrapper).__name__
        for wrapper in Shiboken.getAllValidWrappers()
        if isinstance(wrapper, QLayoutItem)
        and not isinstance(wrapper, QObject)  # A layout is invalidated when deleted.
        and Shiboken.getCppPointer(wrapper)[0] not in held
    ]


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
    item = row.itemAt(0)  # The wrapper addLayout made, not a new one.
    assert item is not None and Shiboken.isValid(item)
    other = QHBoxLayout()
    outer.addLayout(other)
    return page, label, item, other


def test_qt_alone_leaves_the_moved_widget_s_item_wrapper_valid(qtbot: QtBot) -> None:
    _page, label, item, other = filled_row(qtbot)
    other.addWidget(label)  # Qt deletes the row's item for the label; PySide isn't told.
    assert Shiboken.isValid(item)  # What take_widget prevents.


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


def test_the_window_leaves_no_stale_layout_item_wrappers(qtbot: QtBot) -> None:
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    # The header hosts page actions, and the draft editor's buttons change cells.
    reply_card = QWidget(window)
    reply_card.setObjectName("inlineReplyCard")
    for host in (window.drafts_panel.editor_host, None, reply_card, None):
        window.draft_editor.set_embedded(host, window=window)
    for key in ("today", "actions", "drafts", "briefs", "calendar", "inbox", "today"):
        window._show_page(key)
    assert stale_item_wrappers() == []
