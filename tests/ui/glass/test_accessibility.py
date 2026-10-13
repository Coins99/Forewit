"""Glass follows the system's accessibility settings and the owner's choices: reading the
settings, combining them, applying them live, the Appearance tab and saving."""

import ctypes.util
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from shiboken6 import Shiboken

from mailbrief.ui.appearance import APPEARANCE_FILE, Appearance, AppearanceStore
from mailbrief.ui.appearance_view import AppearancePanel, system_note
from mailbrief.ui.glass import system as glass_system
from mailbrief.ui.glass.accessibility import GlassAccessibility
from mailbrief.ui.glass.painter import GlassMode
from mailbrief.ui.glass.preferences import (
    GlassPreferences,
    Motion,
    Transparency,
    effective_preferences,
    glass_preferences,
)
from mailbrief.ui.glass.system import SystemAccessibility, macos_flags, read_system
from mailbrief.ui.main_window import MainWindow
from mailbrief.ui.theme import ColorScheme, apply_theme
from tests.ui.test_workflow import FakeBackend, finish

NONE = SystemAccessibility()
CONTRAST = SystemAccessibility(increase_contrast=True, reduce_transparency=True)
CLEAR = SystemAccessibility(reduce_transparency=True)
STILL = SystemAccessibility(reduce_motion=True)


@pytest.mark.parametrize(
    ("system", "transparency", "motion", "expected"),
    [
        (NONE, Transparency.SYSTEM, Motion.SYSTEM, GlassPreferences()),
        (NONE, Transparency.SOLID, Motion.SYSTEM, GlassPreferences(GlassMode.REDUCED_TRANSPARENCY)),
        (
            CLEAR,
            Transparency.SYSTEM,
            Motion.SYSTEM,
            GlassPreferences(GlassMode.REDUCED_TRANSPARENCY),
        ),
        (
            CONTRAST,
            Transparency.SOLID,
            Motion.SYSTEM,
            GlassPreferences(GlassMode.INCREASED_CONTRAST),
        ),
        (STILL, Transparency.SYSTEM, Motion.SYSTEM, GlassPreferences(reduce_motion=True)),
        (NONE, Transparency.SYSTEM, Motion.REDUCED, GlassPreferences(reduce_motion=True)),
    ],
)
def test_the_system_and_the_owner_combine(
    system: SystemAccessibility,
    transparency: Transparency,
    motion: Motion,
    expected: GlassPreferences,
) -> None:
    """The owner can make glass solid or still when the system doesn't, never the
    reverse, and increased contrast always wins."""
    assert effective_preferences(system, transparency, motion) == expected


def test_reading_combines_qt_and_the_platform() -> None:
    assert read_system(lambda: (False, True, True), lambda: False) == SystemAccessibility(
        increase_contrast=False, reduce_transparency=True, reduce_motion=True
    )
    assert read_system(lambda: (False, False, False), lambda: True).increase_contrast
    assert read_system(lambda: (True, False, False), lambda: False).increase_contrast


def test_a_failed_read_counts_as_no_preference() -> None:
    def broken_flags() -> tuple[bool, bool, bool]:
        raise OSError("unreadable")

    def broken_contrast() -> bool:
        raise RuntimeError("no hints")

    assert read_system(broken_flags, broken_contrast) == NONE


@pytest.mark.skipif(sys.platform != "darwin", reason="NSWorkspace is macOS only")
def test_macos_answers_with_three_flags() -> None:
    flags = macos_flags()
    assert len(flags) == 3 and all(isinstance(flag, bool) for flag in flags)


@pytest.mark.skipif(sys.platform != "win32", reason="The registry is Windows only")
def test_windows_answers_with_three_flags() -> None:
    flags = glass_system.platform_flags()
    assert len(flags) == 3 and all(isinstance(flag, bool) for flag in flags)
    assert flags[0] is False  # Qt reports Windows high contrast.


def test_without_the_objective_c_runtime_macos_reports_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    glass_system._workspace_query.cache_clear()
    monkeypatch.setattr(ctypes.util, "find_library", lambda name: None)
    try:
        assert macos_flags() == (False, False, False)
    finally:
        glass_system._workspace_query.cache_clear()


def test_the_settings_apply_at_once_and_follow_changes(qapp: QApplication) -> None:
    reads: list[SystemAccessibility] = [CLEAR]
    seen: list[SystemAccessibility] = []
    controller = GlassAccessibility(read=lambda: reads[-1])
    controller.changed.connect(seen.append)
    assert glass_preferences().mode is GlassMode.REDUCED_TRANSPARENCY
    reads.append(NONE)  # The owner turns Reduce transparency off in System Settings…
    qapp.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)  # …and returns.
    assert glass_preferences() == GlassPreferences()
    assert seen[-1] == NONE
    reads.append(CONTRAST)
    contrast = qapp.styleHints().accessibility().contrastPreferenceChanged
    contrast.emit(Qt.ContrastPreference.HighContrast)
    assert glass_preferences().mode is GlassMode.INCREASED_CONTRAST
    reads.append(NONE)
    qapp.applicationStateChanged.emit(Qt.ApplicationState.ApplicationInactive)
    assert glass_preferences().mode is GlassMode.INCREASED_CONTRAST  # Only on activation.
    controller.set_choices(Transparency.SOLID, Motion.REDUCED)  # Reads nothing new.
    assert glass_preferences() == GlassPreferences(GlassMode.INCREASED_CONTRAST, True)
    controller.refresh()
    assert glass_preferences() == GlassPreferences(GlassMode.REDUCED_TRANSPARENCY, True)


def test_a_deleted_controller_is_never_called(qapp: QApplication) -> None:
    """Its window can close; the application's signals must not reach it afterwards."""
    reads: list[SystemAccessibility] = []

    def read() -> SystemAccessibility:
        reads.append(NONE)
        return NONE

    controller = GlassAccessibility(read=read)
    Shiboken.delete(controller)
    qapp.applicationStateChanged.emit(Qt.ApplicationState.ApplicationActive)
    qapp.styleHints().accessibility().contrastPreferenceChanged.emit(
        Qt.ContrastPreference.HighContrast
    )
    assert reads == [NONE]  # Only the read when it was made.


def test_the_panel_offers_glass_and_motion_choices(qtbot: QtBot) -> None:
    panel = AppearancePanel()
    qtbot.addWidget(panel)
    assert panel.chosen_transparency() is Transparency.SYSTEM
    assert panel.chosen_motion() is Motion.SYSTEM
    names = [button.accessibleName() for button in panel.transparency_buttons.values()]
    assert names == ["Glass: Follow the system", "Glass: Always solid"]
    assert panel.motion_buttons[Motion.REDUCED].objectName() == "motion-reduced"
    with qtbot.assertNotEmitted(panel.transparency_chosen):
        panel.set_choices(Transparency.SOLID, Motion.REDUCED)
    assert panel.chosen_transparency() is Transparency.SOLID
    assert panel.chosen_motion() is Motion.REDUCED
    with qtbot.waitSignal(panel.transparency_chosen) as chosen:
        panel.transparency_buttons[Transparency.SYSTEM].click()
    assert chosen.args == [Transparency.SYSTEM]
    with qtbot.waitSignal(panel.motion_chosen) as chosen:
        panel.motion_buttons[Motion.SYSTEM].click()
    assert chosen.args == [Motion.SYSTEM]


def test_the_panel_says_what_the_system_does(qtbot: QtBot) -> None:
    panel = AppearancePanel()
    qtbot.addWidget(panel)
    panel.show()
    assert not panel.system_note.isVisible()
    panel.set_system(SystemAccessibility(reduce_transparency=True, reduce_motion=True))
    assert panel.system_note.isVisible()
    assert panel.system_note.text() == system_note(
        SystemAccessibility(reduce_transparency=True, reduce_motion=True)
    )
    assert "reduces transparency" in panel.system_note.text()
    assert "reduces motion" in panel.system_note.text()
    assert "increases contrast" in system_note(CONTRAST)
    assert "reduces transparency" not in system_note(CONTRAST)
    panel.set_system(NONE)
    assert not panel.system_note.isVisible()


async def test_choices_apply_and_are_saved_with_the_scheme(
    qtbot: QtBot, qapp: QApplication, themed: None, tmp_path: Path
) -> None:
    apply_theme(qapp)
    store = AppearanceStore(tmp_path / APPEARANCE_FILE)
    window = MainWindow(FakeBackend(), appearance=store)
    qtbot.addWidget(window)
    window.glass_accessibility._read = lambda: NONE
    window.glass_accessibility.refresh()
    panel = window.settings_dialog.appearance_panel
    panel.transparency_buttons[Transparency.SOLID].click()
    assert glass_preferences().mode is GlassMode.REDUCED_TRANSPARENCY
    panel.motion_buttons[Motion.REDUCED].click()
    assert glass_preferences().reduce_motion
    panel.buttons[ColorScheme.SAGE].click()  # A new scheme keeps the glass choices.
    assert window.appearance_writes is not None
    await window.appearance_writes.drain()
    assert store.load() == Appearance(
        color_scheme=ColorScheme.SAGE, transparency=Transparency.SOLID, motion=Motion.REDUCED
    )
    window.glass_accessibility._read = lambda: STILL
    window.glass_accessibility.refresh()
    assert "reduces motion" in panel.system_note.text()


async def test_a_new_window_starts_with_the_saved_choices(
    qtbot: QtBot, qapp: QApplication, themed: None, tmp_path: Path
) -> None:
    apply_theme(qapp)
    store = AppearanceStore(tmp_path / APPEARANCE_FILE)
    store.save(Appearance(transparency=Transparency.SOLID, motion=Motion.REDUCED))
    window = MainWindow(FakeBackend(), appearance=store)
    qtbot.addWidget(window)
    assert window.appearance.transparency is Transparency.SOLID
    assert glass_preferences().mode is not GlassMode.NORMAL
    assert glass_preferences().reduce_motion
    panel = window.settings_dialog.appearance_panel
    panel.set_choices(Transparency.SYSTEM, Motion.SYSTEM)
    window.settings_button.click()  # Opening Settings shows the choices in effect.
    await finish(window)
    assert panel.chosen_transparency() is Transparency.SOLID
    assert panel.chosen_motion() is Motion.REDUCED
    window.settings_dialog.reject()


def test_an_unknown_value_resets_only_its_own_field(tmp_path: Path) -> None:
    path = tmp_path / APPEARANCE_FILE
    path.write_text('{"color_scheme": "plum", "transparency": "frosted", "motion": "reduced"}')
    assert AppearanceStore(path).load() == Appearance(
        color_scheme=ColorScheme.PLUM, transparency=Transparency.SYSTEM, motion=Motion.REDUCED
    )
