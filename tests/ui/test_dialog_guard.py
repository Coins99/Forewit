"""Dialogs keep the window to themselves without a modal session, so quitting works with one
open on macOS: the cover takes the window's input and activation brings the dialog back."""

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QPushButton, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.ui.dialog_guard import DialogGuard
from mailbrief.ui.main_window import MainWindow
from tests.ui.test_calendar import NOW
from tests.ui.test_workflow import FakeBackend


@pytest.fixture
def window(qtbot: QtBot) -> MainWindow:
    result = MainWindow(FakeBackend())
    result.now = lambda: NOW
    qtbot.addWidget(result)
    return result


def centre(widget: QWidget, window: QWidget) -> QPoint:
    return widget.mapTo(window, widget.rect().center())


async def test_dialogs_open_without_a_modal_session(window: MainWindow) -> None:
    await window.initialize()
    window.page_shortcuts["calendar"].activated.emit()
    window.calendar_panel.plan_button.click()
    dialog = window.planning_dialog
    assert dialog.isVisible() and dialog.windowModality() == Qt.WindowModality.NonModal
    assert QApplication.activeModalWidget() is None  # Nothing for macOS to block a quit on.
    assert window.dialog_guard.isVisibleTo(window)
    assert window.dialog_guard.geometry() == window.rect()
    dialog.reject()
    assert not window.dialog_guard.isVisibleTo(window)

    window.calendar_panel.new_button.click()
    assert window.event_editor.isVisible() and QApplication.activeModalWidget() is None
    window.event_editor.reject()
    assert not window.dialog_guard.isVisibleTo(window)


async def test_the_cover_takes_the_windows_clicks_and_shortcuts(window: MainWindow) -> None:
    await window.initialize()
    window.page_shortcuts["calendar"].activated.emit()
    window.show()
    window.calendar_panel.plan_button.click()
    guard = window.dialog_guard
    point = centre(window.calendar_panel.new_button, window)
    assert window.childAt(point) is guard
    handle = window.windowHandle()
    assert handle is not None
    QTest.mouseClick(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    assert not window.event_editor.isVisible()  # New event, under the cover, wasn't pressed.
    QTest.keyClick(guard, Qt.Key.Key_1, Qt.KeyboardModifier.ControlModifier)
    assert window.workspace.current_page() == "calendar"  # Ctrl+1 (Today) didn't fire.

    window.planning_dialog.reject()
    assert window.childAt(point) is not guard
    QTest.mouseClick(handle, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, point)
    assert window.event_editor.isVisible()  # The same click, with no dialog, presses it.


async def test_activating_the_window_brings_the_dialog_back(
    window: MainWindow, qtbot: QtBot
) -> None:
    await window.initialize()
    window.show()
    window.page_shortcuts["calendar"].activated.emit()
    window.calendar_panel.plan_button.click()
    dialog = window.planning_dialog
    window.activateWindow()
    QApplication.sendEvent(window, QEvent(QEvent.Type.WindowActivate))
    qtbot.waitUntil(lambda: QApplication.activeWindow() is dialog, timeout=2000)


def test_the_cover_paints_nothing(qtbot: QtBot) -> None:
    window = QWidget()
    qtbot.addWidget(window)
    window.resize(240, 160)
    QPushButton("Under the cover", window).move(20, 20)
    guard = DialogGuard(window)
    before = window.grab().toImage()
    guard.setGeometry(window.rect())
    guard.show()
    assert window.grab().toImage() == before


def test_the_cover_stays_until_the_last_dialog_closes(qtbot: QtBot) -> None:
    window = QWidget()
    qtbot.addWidget(window)
    guard = DialogGuard(window)
    first, second = QDialog(window), QDialog(window)
    guard.show_dialog(first)
    guard.show_dialog(second)
    assert guard.isVisibleTo(window)
    second.hide()  # Hidden any way at all, not only through reject().
    assert guard.isVisibleTo(window)
    first.reject()
    assert not guard.isVisibleTo(window)
    guard.show_dialog(first)  # A dialog shown again is watched again.
    assert guard.isVisibleTo(window)
    first.done(QDialog.DialogCode.Accepted)
    assert not guard.isVisibleTo(window)
