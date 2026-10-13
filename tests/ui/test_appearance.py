"""The colour scheme: its file, its writer, the Appearance tab, the window applying it and
the colours kept by widgets that follow the theme."""

import threading
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPalette
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel
from pytestqt.qtbot import QtBot

from mailbrief.app import apply_saved_scheme
from mailbrief.ui.appearance import (
    APPEARANCE_FILE,
    Appearance,
    AppearanceStore,
    AppearanceWriter,
)
from mailbrief.ui.appearance_view import SWATCH_PX, AppearancePanel, swatch
from mailbrief.ui.glass.preferences import Transparency
from mailbrief.ui.labels import outline_button
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.theme import (
    ColorScheme,
    ThemeMode,
    apply_theme,
    current_scheme,
    current_tokens,
    tokens_for,
)
from tests.ui.test_workflow import FakeBackend, finish


def broken_stylesheet(*args: object) -> str:
    raise RuntimeError("Theme stylesheet failed.")


def test_a_missing_file_gives_the_default(tmp_path: Path) -> None:
    assert AppearanceStore(tmp_path / APPEARANCE_FILE).load() == Appearance()
    assert Appearance().color_scheme is ColorScheme.SLATE


@pytest.mark.parametrize("raw", ["not json", '{"color_scheme": "neon"}', "\udcff"])
def test_an_unreadable_or_unknown_choice_gives_the_default(tmp_path: Path, raw: str) -> None:
    path = tmp_path / APPEARANCE_FILE
    path.write_bytes(raw.encode("utf-8", "surrogateescape"))
    assert AppearanceStore(path).load().color_scheme is ColorScheme.SLATE


def test_a_saved_choice_reads_back_and_unknown_fields_are_ignored(tmp_path: Path) -> None:
    store = AppearanceStore(tmp_path / APPEARANCE_FILE)
    store.save(Appearance(color_scheme=ColorScheme.PLUM))
    assert store.load().color_scheme is ColorScheme.PLUM
    store.path.write_text('{"color_scheme": "sage", "from_a_newer_build": 1}', "utf-8")
    assert store.load().color_scheme is ColorScheme.SAGE


class RecordingStore(AppearanceStore):
    """Notes each write and the thread it ran on; with a gate, each write waits for it."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.writes: list[ColorScheme] = []
        self.off_main_thread: list[bool] = []
        self.gate: threading.Event | None = None

    def save(self, appearance: Appearance) -> None:
        if self.gate is not None:
            assert self.gate.wait(timeout=5)
        self.writes.append(appearance.color_scheme)
        self.off_main_thread.append(threading.current_thread() is not threading.main_thread())
        super().save(appearance)


async def test_choices_made_during_a_write_become_one_write_of_the_last(tmp_path: Path) -> None:
    store = RecordingStore(tmp_path / APPEARANCE_FILE)
    store.gate = threading.Event()
    writer = AppearanceWriter(store, failed=lambda: None)
    for scheme in (ColorScheme.PLUM, ColorScheme.SAGE, ColorScheme.ROSE):
        writer.save(Appearance(color_scheme=scheme))  # PLUM's write waits at the gate.
    store.gate.set()
    await writer.drain()
    # PLUM may have been taken before SAGE and ROSE arrived; ROSE is always written last.
    assert store.load().color_scheme is ColorScheme.ROSE
    assert ColorScheme.SAGE not in store.writes
    assert all(store.off_main_thread)
    writer.save(Appearance(color_scheme=ColorScheme.OCEAN))  # A new write after the last.
    await writer.drain()
    assert store.writes[-1] is ColorScheme.OCEAN
    assert store.load().color_scheme is ColorScheme.OCEAN


async def test_a_write_that_fails_unexpectedly_never_stops_later_saves(
    qtbot: QtBot, tmp_path: Path
) -> None:
    class Flaky(RecordingStore):
        def save(self, appearance: Appearance) -> None:
            if not self.writes:
                self.writes.append(appearance.color_scheme)
                raise ValueError("unexpected")
            super().save(appearance)

    failures: list[bool] = []
    store = Flaky(tmp_path / APPEARANCE_FILE)
    writer = AppearanceWriter(store, failed=lambda: failures.append(True))
    writer.save(Appearance(color_scheme=ColorScheme.PLUM))
    await writer.drain()
    qtbot.waitUntil(lambda: failures == [True])  # Queued to the Qt thread.
    writer.save(Appearance(color_scheme=ColorScheme.SAGE))
    await writer.drain()
    assert store.load().color_scheme is ColorScheme.SAGE


async def test_a_failed_write_is_reported(qtbot: QtBot, tmp_path: Path) -> None:
    failures: list[bool] = []
    store = AppearanceStore(tmp_path / "missing" / APPEARANCE_FILE)
    writer = AppearanceWriter(store, failed=lambda: failures.append(True))
    await writer.drain()  # Nothing to wait for yet.
    writer.save(Appearance(color_scheme=ColorScheme.PLUM))
    await writer.drain()
    qtbot.waitUntil(lambda: failures == [True])  # Queued to the Qt thread.


def test_the_panel_offers_every_scheme_and_emits_only_on_a_choice(qtbot: QtBot) -> None:
    panel = AppearancePanel()
    qtbot.addWidget(panel)
    assert list(panel.buttons) == list(ColorScheme)
    assert panel.chosen() is ColorScheme.SLATE
    for scheme, button in panel.buttons.items():
        assert button.objectName() == f"scheme-{scheme.value}"
        assert button.accessibleName() == button.text()
    with qtbot.assertNotEmitted(panel.scheme_chosen):
        panel.set_scheme(ColorScheme.PLUM)
    assert panel.chosen() is ColorScheme.PLUM
    with qtbot.waitSignal(panel.scheme_chosen) as blocker:
        panel.buttons[ColorScheme.SAGE].click()
    assert blocker.args == [ColorScheme.SAGE]


def test_arrow_keys_move_between_schemes(qtbot: QtBot) -> None:
    panel = AppearancePanel()
    qtbot.addWidget(panel)
    with qtbot.waitActive(panel, timeout=5000):  # Focus needs an active window.
        panel.show()
        panel.activateWindow()
    panel.buttons[ColorScheme.SLATE].setFocus()
    with qtbot.waitSignal(panel.scheme_chosen) as blocker:
        QTest.keyClick(panel.buttons[ColorScheme.SLATE], Qt.Key.Key_Down)
    assert blocker.args == [ColorScheme.GRAPHITE]


@pytest.mark.parametrize("scheme", list(ColorScheme))
def test_a_swatch_shows_the_scheme_panel_inside_its_accent(
    qapp: QApplication, scheme: ColorScheme
) -> None:
    tokens = tokens_for(ThemeMode.DARK, scheme)
    image = swatch(scheme, 2.0).toImage()
    assert image.width() == SWATCH_PX * 2
    assert image.pixelColor(SWATCH_PX, SWATCH_PX).name() == tokens.panel
    assert image.pixelColor(SWATCH_PX, 2).name() == tokens.accent_fg
    assert image.pixelColor(0, 0).alpha() == 0


def test_icons_set_once_follow_a_new_scheme(qapp: QApplication, themed: None) -> None:
    apply_theme(qapp)
    button = outline_button("&Go", "goButton", mnemonic=True, icon_name="refresh")
    before = button.icon().pixmap(16).toImage()
    apply_theme(qapp, ThemeMode.LIGHT, ColorScheme.ROSE)
    after: QImage = button.icon().pixmap(16).toImage()
    assert after != before
    apply_theme(qapp)
    assert button.icon().pixmap(16).toImage() == before


async def test_choosing_a_scheme_recolours_the_app_and_saves_it(
    qtbot: QtBot, qapp: QApplication, themed: None, tmp_path: Path
) -> None:
    apply_theme(qapp)
    store = AppearanceStore(tmp_path / APPEARANCE_FILE)
    window = MainWindow(FakeBackend(), appearance=store)
    qtbot.addWidget(window)
    refresh = window.findChild(QLabel, "headerRefreshIcon")
    assert refresh is not None
    before = refresh.pixmap().toImage()
    panel = window.settings_dialog.appearance_panel
    panel.buttons[ColorScheme.PLUM].click()
    plum = tokens_for(ThemeMode.DARK, ColorScheme.PLUM)
    assert current_scheme() is ColorScheme.PLUM and current_tokens() == plum
    assert qapp.palette().color(QPalette.ColorRole.Window).name() == plum.panel
    assert refresh.pixmap().toImage() != before
    assert window.appearance_writes is not None
    await window.appearance_writes.drain()
    assert store.load().color_scheme is ColorScheme.PLUM
    panel.set_scheme(ColorScheme.SLATE)  # Opening Settings shows the scheme in effect.
    window.settings_button.click()
    await finish(window)
    assert panel.chosen() is ColorScheme.PLUM


async def test_a_failed_scheme_keeps_the_colours_and_saves_nothing(
    qtbot: QtBot,
    qapp: QApplication,
    themed: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    apply_theme(qapp)
    store = AppearanceStore(tmp_path / APPEARANCE_FILE)
    window = MainWindow(FakeBackend(), appearance=store)
    qtbot.addWidget(window)
    monkeypatch.setattr("mailbrief.ui.theme.style.build_stylesheet", broken_stylesheet)
    panel = window.settings_dialog.appearance_panel
    panel.buttons[ColorScheme.ROSE].click()
    assert current_scheme() is ColorScheme.SLATE
    assert current_tokens() == tokens_for(ThemeMode.DARK)
    assert panel.chosen() is ColorScheme.SLATE
    assert window.settings_dialog.status.text() == "The colours couldn't be changed."
    assert window.appearance_writes is not None
    await window.appearance_writes.drain()
    assert not store.path.exists()


async def test_a_scheme_that_cannot_be_saved_still_applies(
    qtbot: QtBot, qapp: QApplication, themed: None, tmp_path: Path
) -> None:
    apply_theme(qapp)
    window = MainWindow(
        FakeBackend(), appearance=AppearanceStore(tmp_path / "missing" / APPEARANCE_FILE)
    )
    qtbot.addWidget(window)
    window.settings_dialog.appearance_panel.buttons[ColorScheme.OCEAN].click()
    assert window.appearance_writes is not None
    await window.appearance_writes.drain()
    assert current_scheme() is ColorScheme.OCEAN
    # The failure comes back from the worker through a queued signal.
    qtbot.waitUntil(lambda: "couldn't be saved" in window.settings_dialog.status.text())


async def test_without_a_store_a_scheme_applies_unsaved(
    qtbot: QtBot, qapp: QApplication, themed: None
) -> None:
    apply_theme(qapp)
    window = MainWindow(FakeBackend())
    qtbot.addWidget(window)
    assert window.appearance_writes is None
    window.settings_dialog.appearance_panel.buttons[ColorScheme.SAGE].click()
    assert current_scheme() is ColorScheme.SAGE
    await window.shutdown()


def test_startup_applies_the_saved_scheme(qapp: QApplication, themed: None, tmp_path: Path) -> None:
    apply_theme(qapp)
    store = AppearanceStore(tmp_path / APPEARANCE_FILE)
    apply_saved_scheme(store.load(), themed=True)  # Nothing saved: the default stays.
    assert current_scheme() is ColorScheme.SLATE
    store.save(Appearance(color_scheme=ColorScheme.GRAPHITE))
    apply_saved_scheme(store.load(), themed=False)  # An unthemed app stays unthemed.
    assert current_scheme() is ColorScheme.SLATE
    apply_saved_scheme(store.load(), themed=True)
    assert current_scheme() is ColorScheme.GRAPHITE
    assert current_tokens() == tokens_for(ThemeMode.DARK, ColorScheme.GRAPHITE)


def test_startup_survives_a_scheme_that_fails(
    qapp: QApplication, themed: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    apply_theme(qapp)
    store = AppearanceStore(tmp_path / APPEARANCE_FILE)
    store.save(Appearance(color_scheme=ColorScheme.ROSE))
    monkeypatch.setattr("mailbrief.ui.theme.style.build_stylesheet", broken_stylesheet)
    apply_saved_scheme(store.load(), themed=True)
    assert current_scheme() is ColorScheme.SLATE
    assert current_tokens() == tokens_for(ThemeMode.DARK)


async def test_a_window_uses_the_appearance_startup_read(
    qtbot: QtBot, qapp: QApplication, themed: None, tmp_path: Path
) -> None:
    """Startup reads appearance.json once and hands it on; the window doesn't read again."""
    apply_theme(qapp)

    class Unread(AppearanceStore):
        def load(self) -> Appearance:
            raise AssertionError("read again")

    saved = Appearance(color_scheme=ColorScheme.PLUM, transparency=Transparency.SOLID)
    window = MainWindow(
        FakeBackend(), appearance=Unread(tmp_path / APPEARANCE_FILE), saved_appearance=saved
    )
    qtbot.addWidget(window)
    assert window.appearance is saved
