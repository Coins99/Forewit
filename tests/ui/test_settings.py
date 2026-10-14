"""Settings interaction, secret-field lifetime and shutdown during durable writes."""

import asyncio

import pytest
from pydantic import SecretStr
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QScrollArea, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.preferences import DesktopPreferences
from mailbrief.ui.settings_view import SettingsDialog
from mailbrief.ui.theme import apply_theme
from tests.ui.test_workflow import FakeBackend, finish


async def test_settings_save_key_clear_and_revoke(qtbot: QtBot) -> None:
    backend = FakeBackend()
    window = MainWindow(backend)
    qtbot.addWidget(window)
    window.settings_button.click()
    await finish(window)
    editor = window.settings_dialog
    assert editor.isVisible()
    editor.model.setText("test-model")
    editor.save_button.click()
    assert not window.cancel_button.isEnabled()
    await finish(window)
    assert backend.preferences.groq_model == "test-model"
    assert "Settings saved" in editor.status.text()
    assert editor.key.echoMode() == QLineEdit.EchoMode.Password
    editor.key.setText("synthetic-key-not-a-real-credential")
    editor.key_button.click()
    assert editor.key.text() == ""
    await finish(window)
    saved_key = backend.key_value
    assert saved_key == SecretStr("synthetic-key-not-a-real-credential")
    assert "synthetic-key" not in editor.status.text()
    editor.remove_key_button.click()
    await finish(window)
    assert backend.key_value is None
    editor.revoke_button.click()
    await finish(window)
    assert backend.revoked
    editor.key.setText("unsaved-secret")
    editor.reject()
    assert editor.key.text() == ""


async def test_invalid_key_never_reaches_backend(qtbot: QtBot) -> None:
    backend = FakeBackend()
    window = MainWindow(backend)
    qtbot.addWidget(window)
    window.settings_button.click()
    await finish(window)
    editor = window.settings_dialog
    editor.key.setText("tiny")
    editor.key_button.click()
    assert backend.key_value is None
    assert "invalid" in editor.status.text()
    assert editor.key.text() == ""


async def test_close_waits_for_settings_write(qtbot: QtBot) -> None:
    started = asyncio.Event()
    finish_write = asyncio.Event()

    class SlowBackend(FakeBackend):
        async def save_preferences(self, preferences: DesktopPreferences) -> None:
            started.set()
            await finish_write.wait()
            await super().save_preferences(preferences)

    backend = SlowBackend()
    window = MainWindow(backend)
    qtbot.addWidget(window)
    window._request_save_preferences(DesktopPreferences(groq_model="new-model"))
    await started.wait()
    window.close()
    shutdown = asyncio.create_task(window.shutdown())
    await asyncio.sleep(0)
    assert not shutdown.done()
    assert not backend.closed
    finish_write.set()
    await shutdown
    assert backend.preferences.groq_model == "new-model"
    assert backend.closed


async def test_unsaved_settings_are_noticed(qtbot: QtBot) -> None:
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    window.settings_button.click()
    await finish(window)
    dialog = window.settings_dialog
    assert not dialog.has_unsaved_input()  # What was loaded counts as saved.
    dialog.model.setText("another-model")
    assert dialog.has_unsaved_input()
    dialog.save_button.click()  # Asked to save: no longer unsaved.
    await finish(window)
    assert not dialog.has_unsaved_input()
    dialog.key.setText("synthetic-key-not-a-real-credential")  # A typed, unsaved key.
    assert dialog.has_unsaved_input()
    dialog.key.clear()
    panel = dialog.preferences_panel
    panel.refresh_on_launch.setChecked(not panel.refresh_on_launch.isChecked())
    assert dialog.has_unsaved_input()


@pytest.mark.parametrize("size", [(640, 620), (500, 420)])
def test_no_settings_tab_squeezes_its_rows_into_each_other(
    qtbot: QtBot, qapp: QApplication, themed: None, size: tuple[int, int]
) -> None:
    """A tab taller than the dialog scrolls: every wrapped note keeps the lines it needs
    (a squeezed note's last line hid behind the next row), and the excluded-senders box
    shows at least three lines."""
    apply_theme(qapp)
    parent = QWidget()
    qtbot.addWidget(parent)
    dialog = SettingsDialog(parent)  # The parent owns it, and closes it with itself.
    dialog.resize(*size)
    dialog.show()
    for index in range(dialog.tabs.count()):
        dialog.tabs.setCurrentIndex(index)
        area = dialog.tabs.currentWidget()
        assert isinstance(area, QScrollArea)
        assert area.focusPolicy() == Qt.FocusPolicy.NoFocus  # Tab skips it.
        assert area.objectName().endswith("Scroll") and area.accessibleName()
        page = area.widget()
        assert page is not None
        qtbot.waitUntil(page.isVisible)
        for label in page.findChildren(QLabel):
            if label.isVisible() and label.wordWrap() and label.text():
                assert label.height() >= label.heightForWidth(label.width()), label.text()
    senders = dialog.preferences_panel.senders
    assert senders.height() >= 3 * senders.fontMetrics().lineSpacing()
    preferences = dialog.tabs.widget(1)
    assert isinstance(preferences, QScrollArea)
    assert preferences.verticalScrollBar().maximum() > 0  # Taller than the dialog.


def signed_in(window: MainWindow) -> bool:
    return window._account_email is not None


async def test_the_account_card_signs_out_and_in_from_settings(qtbot: QtBot) -> None:
    backend = FakeBackend()
    window = MainWindow(backend)
    qtbot.addWidget(window)
    window.start(window.initialize)
    await finish(window)  # Connected silently at startup.
    editor = window.settings_dialog
    sidebar = window.workspace.sidebar
    assert window.connection.text().startswith("Gmail: connected as")
    assert window.ai.text().startswith("AI:")
    assert not editor.disconnect_button.isHidden() and editor.sign_in_button.isHidden()
    assert window.connect_button.isHidden()  # Signed in: the sidebar offers nothing.
    assert not sidebar.isAncestorOf(window.connection)

    window.settings_button.click()
    await finish(window)
    assert editor.isVisible()
    editor.disconnect_button.click()
    await finish(window)
    assert not signed_in(window)
    assert window.connection.text() == "Gmail: disconnected"
    assert editor.disconnect_button.isHidden() and not editor.sign_in_button.isHidden()
    assert not window.connect_button.isHidden()  # Signed out: the sidebar offers sign-in.

    editor.sign_in_button.click()
    await finish(window)
    assert signed_in(window)
    assert not editor.disconnect_button.isHidden() and editor.sign_in_button.isHidden()
    window.start(lambda: asyncio.sleep(0))
    assert not editor.sign_in_button.isEnabled() and not editor.disconnect_button.isEnabled()
    await finish(window)
    editor.reject()


def test_settings_mnemonics_stay_unique(qtbot: QtBot) -> None:
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    editor = window.settings_dialog
    texts = [
        button.text()
        for button in (
            editor.browse_button,
            editor.save_button,
            editor.key_button,
            editor.remove_key_button,
            editor.revoke_button,
            editor.close_button,
            editor.disconnect_button,
            editor.sign_in_button,
        )
    ]
    letters = [text[text.index("&") + 1].casefold() for text in texts]
    assert len(set(letters)) == len(letters)


@pytest.mark.parametrize("mode", ["dark", "light"])
async def test_account_card_gallery(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: str
) -> None:
    from mailbrief.ui.theme.tokens import ThemeMode
    from tests.ui.test_main_window_layout import save_shot

    apply_theme(qapp, mode=ThemeMode(mode))
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    window.start(window.initialize)
    await finish(window)
    window.resize(1100, 720)
    window.show()
    qtbot.waitExposed(window)
    save_shot(window.grab(), f"window-account-{mode}")
    window.settings_button.click()
    await finish(window)
    dialog = window.settings_dialog
    qtbot.waitExposed(dialog)
    save_shot(dialog.grab(), f"dialog-settings-{mode}")
    dialog.reject()


async def test_settings_open_while_mailbrief_is_busy(qtbot: QtBot) -> None:
    from mailbrief.ui.settings_view import BUSY

    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    window.start(window.initialize)
    await finish(window)
    gate = asyncio.Event()

    async def sync() -> None:
        window.status.setText("Syncing your full Gmail Inbox…")
        await gate.wait()

    assert window.start(sync)  # Say, the long Inbox sync at launch.
    editor = window.settings_dialog
    assert window.settings_button.isEnabled()
    window.settings_button.click()
    await window.drain_settings_reads()
    assert editor.isVisible()
    assert window.status.text() == "Syncing your full Gmail Inbox…"  # The sync's words stay.
    assert editor.status.text() == BUSY
    assert not editor.fields.isEnabled() and not editor.disconnect_button.isEnabled()
    assert editor.appearance_panel.isEnabled()  # Appearance works at once.
    window.settings_button.click()  # Already open: nothing reads again.
    assert not window._settings_reads

    gate.set()
    await finish(window)
    assert editor.fields.isEnabled() and editor.disconnect_button.isEnabled()
    assert editor.status.text() != BUSY
    editor.reject()


async def test_settings_opening_beside_an_operation_reports_a_failure(qtbot: QtBot) -> None:
    class Backend(FakeBackend):
        async def get_preferences(self) -> DesktopPreferences:
            raise RuntimeError("private detail")

    window = MainWindow(Backend())
    qtbot.addWidget(window)
    gate = asyncio.Event()

    async def hold() -> None:
        await gate.wait()

    assert window.start(hold)
    window.settings_button.click()
    await window.drain_settings_reads()
    assert not window.settings_dialog.isVisible()
    assert window.status.text() == "Settings couldn't be opened just now. Try again."
    gate.set()
    await finish(window)
