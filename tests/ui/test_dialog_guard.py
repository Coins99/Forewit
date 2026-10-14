"""Dialogs keep the window to themselves without a modal session, so quitting works with one
open on macOS: the cover takes the window's input and activation brings the dialog back."""

import gc
import weakref

import pytest
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QHideEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog, QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.ui.dialog_guard import DialogGuard
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.theme import ThemeMode, apply_theme
from tests.ui.test_calendar import NOW
from tests.ui.test_workflow import FakeBackend, finish


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
    assert QApplication.activeModalWidget() is not dialog  # Nothing for macOS to block on.
    assert window.dialog_guard.isVisibleTo(window)
    assert window.dialog_guard.geometry() == window.rect()
    dialog.reject()
    assert not window.dialog_guard.isVisibleTo(window)

    window.calendar_panel.new_button.click()
    assert window.event_editor.isVisible()
    assert window.event_editor.windowModality() == Qt.WindowModality.NonModal
    assert QApplication.activeModalWidget() is not window.event_editor
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


def test_the_cover_paints_nothing(themed: None, qapp: QApplication, qtbot: QtBot) -> None:
    apply_theme(qapp, ThemeMode.DARK)  # The real stylesheet, which must not paint it.
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    window.resize(900, 600)
    before = window.grab().toImage()
    window.dialog_guard.setGeometry(window.rect())
    window.dialog_guard.show()
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


def test_a_dropped_window_with_a_guard_is_freed_without_the_cycle_collector(
    qtbot: QtBot,
) -> None:
    """The guard finds its window as its parent rather than keeping it, so a dropped window
    (a dialog with its own guard) is freed at once, in Qt's order, never by the cycle
    collector, which deletes Qt widgets in no safe order."""

    def dropped() -> weakref.ref[QWidget]:
        window = QWidget()
        qtbot.addWidget(window)
        guard = DialogGuard(window)
        guard.show_dialog(QDialog(window))
        return weakref.ref(window)

    gc.collect()
    gc.disable()  # Only reference counting may free it.
    try:
        window = dropped()
        assert window() is None
    finally:
        gc.enable()


class _SpontaneousHide(QHideEvent):
    """What the window system sends when it hides a window itself, such as Windows hiding
    a minimized window's dialogs."""

    def spontaneous(self) -> bool:
        return True


def test_a_spontaneous_hide_keeps_the_cover(qtbot: QtBot) -> None:
    window = QWidget()
    qtbot.addWidget(window)
    guard = DialogGuard(window)
    dialog = QDialog(window)
    guard.show_dialog(dialog)
    assert not guard.eventFilter(dialog, _SpontaneousHide())
    assert guard.isVisibleTo(window)  # Still open: back when the window is restored.
    dialog.reject()
    assert not guard.isVisibleTo(window)


def test_showing_an_open_dialog_again_brings_it_forward(qtbot: QtBot) -> None:
    window = QWidget()
    qtbot.addWidget(window)
    window.show()
    guard = DialogGuard(window)
    first, second = QDialog(window), QDialog(window)
    guard.show_dialog(first)
    guard.show_dialog(second)
    guard.show_dialog(first)  # Shown again, so now the newest.
    window.activateWindow()  # A click on the window brings the newest back, not second.
    QApplication.sendEvent(window, QEvent(QEvent.Type.WindowActivate))
    qtbot.wait(50)
    qtbot.waitUntil(lambda: QApplication.activeWindow() is first, timeout=2000)


async def test_quitting_with_unsaved_input_asks_first(window: MainWindow) -> None:
    await window.initialize()
    closing: list[bool] = []
    window.closing.connect(lambda: closing.append(True))
    window.page_shortcuts["calendar"].activated.emit()
    window.calendar_panel.new_button.click()
    editor = window.event_editor
    assert not window.ask_before_quitting()  # Nothing typed yet: quitting just closes it.
    editor.title.setText("Dentist")

    window.close()  # The window's own close button.
    box = window.findChild(QMessageBox, "quitConfirmation")
    assert box is not None and box.isVisible() and editor.isVisible() and not closing
    assert box.text() == "Quit and discard your unsaved changes to this event?"
    assert box.windowModality() == Qt.WindowModality.NonModal  # So Cmd+Q still arrives,
    assert window.ask_before_quitting()  # and only brings the question back.
    cancel = box.button(QMessageBox.StandardButton.Cancel)
    assert cancel is not None
    cancel.click()
    assert not box.isVisible() and editor.isVisible() and not closing
    assert editor.title.text() == "Dentist"

    window.close()
    discard = next(
        button
        for button in box.buttons()
        if box.buttonRole(button) == QMessageBox.ButtonRole.DestructiveRole
    )
    discard.click()
    assert closing and not editor.isVisible()
    await window.shutdown()


async def test_a_dialog_without_unsaved_input_closes_with_the_window(window: MainWindow) -> None:
    await window.initialize()
    closing: list[bool] = []
    window.closing.connect(lambda: closing.append(True))
    window.page_shortcuts["calendar"].activated.emit()
    window.calendar_panel.new_button.click()
    window.close()
    assert closing and not window.event_editor.isVisible()
    assert window.findChild(QMessageBox, "quitConfirmation") is None
    await window.shutdown()


async def test_pickers_inside_dialogs_open_without_a_modal_session(window: MainWindow) -> None:
    await window.initialize()
    window.settings_button.click()
    await finish(window)
    settings = window.settings_dialog
    settings.browse_button.click()
    picker = settings.findChild(QFileDialog)
    assert picker is not None and picker.isVisible()
    assert picker.windowModality() == Qt.WindowModality.NonModal
    assert QApplication.activeModalWidget() is not picker
    assert settings.dialog_guard.isVisibleTo(settings)
    picker.reject()
    assert not settings.dialog_guard.isVisibleTo(settings)
