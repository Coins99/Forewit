"""The glass pill button: made like every outline button, the same size, and painted for
hover, press, focus, disabled and the accessibility modes."""

import pytest
from PySide6.QtCore import QEasingCurve, QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QEnterEvent, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from mailbrief.ui.glass.button import LIFT, PRESS_MS, PRESSED_SCALE, RELEASE_MS, GlassButton
from mailbrief.ui.glass.painter import GlassMode
from mailbrief.ui.glass.preferences import (
    GlassPreferences,
    glass_preferences,
    set_glass_preferences,
)
from mailbrief.ui.labels import outline_button
from mailbrief.ui.theme import apply_theme, current_tokens


@pytest.fixture
def button(qtbot: QtBot, qapp: QApplication, themed: None) -> GlassButton:
    apply_theme(qapp)
    made = outline_button("&Sync and review", "generateButton", mnemonic=True, glass=True)
    assert isinstance(made, GlassButton)
    qtbot.addWidget(made)
    made.resize(made.sizeHint())
    made.show()
    return made


def hover(button: GlassButton, x: float = 10.0) -> None:
    point = QPointF(x, button.height() / 2)
    button.enterEvent(QEnterEvent(point, point, QPointF(button.mapToGlobal(QPoint(int(x), 0)))))


def picture(button: GlassButton) -> QImage:
    return button.grab().toImage()


def test_outline_button_makes_glass_pills(qtbot: QtBot, qapp: QApplication, themed: None) -> None:
    apply_theme(qapp)
    primary = outline_button("Send & go", "sendButton", glass=True, primary=True)
    assert isinstance(primary, GlassButton) and primary.primary
    assert primary.objectName() == "sendButton"
    assert primary.property("variant") == "glass"
    assert primary.text() == "Send && go"  # Escaped like any outline button's text.
    assert not primary.autoDefault()
    plain = outline_button("Dismiss", "dismissButton")
    assert not isinstance(plain, GlassButton)
    assert plain.property("variant") == "outline"
    with pytest.raises(ValueError, match="glass"):
        outline_button("Go", "goButton", primary=True)


def test_a_glass_pill_is_no_taller_than_an_outline_button(
    qtbot: QtBot, qapp: QApplication, themed: None
) -> None:
    """So the header keeps its height."""
    apply_theme(qapp)
    glass = outline_button("&Cancel", "a", mnemonic=True, glass=True)
    outline = outline_button("&Cancel", "b", mnemonic=True)
    for made in (glass, outline):
        qtbot.addWidget(made)
    assert glass.sizeHint().height() <= outline.sizeHint().height() + 2 * LIFT
    assert glass.minimumSizeHint() == glass.sizeHint()
    with_icon = outline_button("Open", "c", glass=True, icon_name="refresh")
    qtbot.addWidget(with_icon)
    assert with_icon.sizeHint().width() > outline_button("Open", "d", glass=True).sizeHint().width()


def test_space_presses_it(button: GlassButton, qtbot: QtBot) -> None:
    with qtbot.waitSignal(button.clicked):
        QTest.keyClick(button, Qt.Key.Key_Space)


def leave(button: GlassButton) -> None:
    button.leaveEvent(QEvent(QEvent.Type.Leave))


def test_hovering_lifts_it_and_lights_its_rim(button: GlassButton) -> None:
    leave(button)  # Shown offscreen at the pointer, it may start hovered.
    before = picture(button)
    resting = button._pill()
    hover(button)
    assert button._pill().top() == resting.top() - LIFT
    assert picture(button) != before
    leave(button)
    assert button._pill() == resting
    assert picture(button) == before


def test_reduced_motion_never_lifts_or_animates(button: GlassButton) -> None:
    set_glass_preferences(GlassPreferences(reduce_motion=True))
    leave(button)
    resting = button._pill()
    hover(button)
    assert button._pill() == resting
    QTest.mousePress(button, Qt.MouseButton.LeftButton)
    assert button.press_animation.duration() == 0
    assert button.scale() == PRESSED_SCALE
    QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
    assert button.scale() == 1.0


def test_pressing_shrinks_it_and_releasing_springs_back(button: GlassButton, qtbot: QtBot) -> None:
    QTest.mousePress(button, Qt.MouseButton.LeftButton)
    animation = button.press_animation
    assert animation.duration() == PRESS_MS
    assert animation.endValue() == PRESSED_SCALE
    qtbot.waitUntil(lambda: button.scale() == PRESSED_SCALE)
    pressed = button._pill()
    assert pressed.width() < button.width()
    QTest.mouseRelease(button, Qt.MouseButton.LeftButton)
    assert animation.duration() == RELEASE_MS
    assert animation.easingCurve().type() == QEasingCurve.Type.OutBack
    qtbot.waitUntil(lambda: button.scale() == 1.0)


def test_focus_draws_an_accent_ring(button: GlassButton, qtbot: QtBot) -> None:
    with qtbot.waitActive(button, timeout=5000):
        button.activateWindow()
    button.clearFocus()  # A lone button window takes focus when it activates.
    qtbot.waitUntil(lambda: not button.hasFocus())
    unfocused = picture(button)
    button.setFocus()
    qtbot.waitUntil(button.hasFocus)
    focused = picture(button)
    assert focused != unfocused
    dpr = focused.devicePixelRatio()
    middle = round(button.height() / 2 * dpr)
    accent = current_tokens().accent_fg
    row = [focused.pixelColor(x, middle).name() for x in range(round(button.width() * dpr))]
    assert accent in row


def test_disabled_and_primary_paint_differently(button: GlassButton, qtbot: QtBot) -> None:
    normal = picture(button)
    button.setEnabled(False)
    assert picture(button) != normal
    button.setEnabled(True)
    button.primary = True
    assert picture(button) != normal


@pytest.mark.parametrize("mode", [GlassMode.REDUCED_TRANSPARENCY, GlassMode.INCREASED_CONTRAST])
def test_accessibility_modes_paint_no_rim_light(button: GlassButton, mode: GlassMode) -> None:
    set_glass_preferences(GlassPreferences(mode=mode))
    assert glass_preferences().mode is mode
    leave(button)
    resting = picture(button)
    hover(button)
    lifted = picture(button)
    # The pill rises a pixel, but no light is added to its rim.
    lit = sum(
        1 for y in range(lifted.height()) if lifted.pixelColor(lifted.width() // 2, y).alpha()
    )
    assert lit == sum(
        1 for y in range(resting.height()) if resting.pixelColor(resting.width() // 2, y).alpha()
    )
