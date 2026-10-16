"""The glass pill button: made like every outline button, the same size, and painted for
hover, press, focus, disabled and the accessibility modes."""

import pytest
from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QEnterEvent, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from mailbrief.ui.glass import button as glass_button
from mailbrief.ui.glass.button import (
    GROW_X,
    GROW_Y,
    HOVER_IN_MS,
    HOVER_OUT_MS,
    PRESS_MS,
    PRESSED_SCALE,
    RELEASE_MS,
    ROOM_Y,
    GlassButton,
)
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
    native_primary = outline_button("Go", "goButton", primary=True)
    assert native_primary.property("variant") == "primary"


def test_a_glass_pill_is_no_taller_than_an_outline_button(
    qtbot: QtBot, qapp: QApplication, themed: None
) -> None:
    """So the header keeps its height."""
    apply_theme(qapp)
    glass = outline_button("&Cancel", "a", mnemonic=True, glass=True)
    outline = outline_button("&Cancel", "b", mnemonic=True)
    for made in (glass, outline):
        qtbot.addWidget(made)
    assert glass.sizeHint().height() <= outline.sizeHint().height() + 2 * ROOM_Y
    assert glass.minimumSizeHint() == glass.sizeHint()
    with_icon = outline_button("Open", "c", glass=True, icon_name="refresh")
    qtbot.addWidget(with_icon)
    assert with_icon.sizeHint().width() > outline_button("Open", "d", glass=True).sizeHint().width()


def test_space_presses_it(button: GlassButton, qtbot: QtBot) -> None:
    with qtbot.waitSignal(button.clicked):
        QTest.keyClick(button, Qt.Key.Key_Space)


def leave(button: GlassButton) -> None:
    button.leaveEvent(QEvent(QEvent.Type.Leave))


def test_hovering_animates_in_on_a_spring_and_eases_out(button: GlassButton, qtbot: QtBot) -> None:
    """As Apple's glass: the pill grows a little on a spring and lights from within."""
    leave(button)  # Shown offscreen at the pointer, it may start hovered.
    qtbot.waitUntil(lambda: button.hover() == 0.0)
    before = picture(button)
    resting = button._pill()
    hover(button)
    animation = button.hover_animation
    assert animation.duration() == HOVER_IN_MS
    assert animation.easingCurve().type() == QEasingCurve.Type.OutBack  # A spring.
    qtbot.waitUntil(lambda: animation.state() == QAbstractAnimation.State.Stopped)
    assert button.hover() == 1.0
    grown = button._pill()
    assert grown.width() == pytest.approx(resting.width() + 2 * GROW_X)
    assert grown.height() == pytest.approx(resting.height() + 2 * GROW_Y)
    assert picture(button) != before
    leave(button)
    assert animation.duration() == HOVER_OUT_MS
    assert animation.easingCurve().type() == QEasingCurve.Type.OutCubic
    qtbot.waitUntil(lambda: button.hover() == 0.0)
    assert button._pill() == resting
    assert picture(button) == before


def test_the_spring_overshoot_stays_inside_the_button(button: GlassButton) -> None:
    button._set_hover(1.15)  # Past OutBack's overshoot of about a tenth.
    assert QRectF(button.rect()).contains(button._pill())


def test_reduced_motion_lights_at_once_and_never_moves(button: GlassButton) -> None:
    set_glass_preferences(GlassPreferences(reduce_motion=True))
    leave(button)
    assert button.hover() == 0.0  # At once.
    resting = button._pill()
    before = picture(button)
    hover(button)
    assert button.hover_animation.duration() == 0
    assert button.hover() == 1.0
    assert button._pill() == resting  # No growth…
    assert picture(button) != before  # …but the light shows.
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


def accent_in_middle_row(image: QImage) -> bool:
    middle = round(image.height() / 2)
    accent = current_tokens().accent_fg
    return accent in {image.pixelColor(x, middle).name() for x in range(image.width())}


def test_keyboard_focus_draws_an_accent_ring_and_a_click_does_not(
    button: GlassButton, qtbot: QtBot
) -> None:
    """As the app's other focus rings: shown once the keyboard has moved focus."""
    with qtbot.waitActive(button, timeout=5000):
        button.activateWindow()
    window = button.window()
    window.setAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange, False)
    button.setFocus(Qt.FocusReason.MouseFocusReason)
    qtbot.waitUntil(button.hasFocus)
    assert not accent_in_middle_row(picture(button))  # Focused by a click: no ring.
    window.setAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange, True)  # As Tab sets it.
    assert accent_in_middle_row(picture(button))


def test_space_shrinks_it_and_releasing_springs_back(button: GlassButton, qtbot: QtBot) -> None:
    """Pressed by the keyboard as by the mouse."""
    QTest.keyPress(button, Qt.Key.Key_Space)
    assert button.isDown()
    assert button.press_animation.endValue() == PRESSED_SCALE
    qtbot.waitUntil(lambda: button.scale() == PRESSED_SCALE)
    QTest.keyRelease(button, Qt.Key.Key_Space)
    assert button.press_animation.endValue() == 1.0
    qtbot.waitUntil(lambda: button.scale() == 1.0)


def test_disabling_a_held_pill_releases_and_unlights_it(button: GlassButton, qtbot: QtBot) -> None:
    """A disabled button gets no release or Leave, so it would stay shrunk and lit."""
    hover(button)
    QTest.mousePress(button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: button.scale() == PRESSED_SCALE)
    button.setEnabled(False)
    qtbot.waitUntil(lambda: button.scale() == 1.0)  # Qt releases a disabled button.
    assert button.hover() == 0.0
    assert not button.isDown()


def test_disabled_and_primary_paint_differently(button: GlassButton, qtbot: QtBot) -> None:
    normal = picture(button)
    button.setEnabled(False)
    assert picture(button) != normal
    button.setEnabled(True)
    button.primary = True
    assert picture(button) != normal


@pytest.mark.parametrize("mode", [GlassMode.REDUCED_TRANSPARENCY, GlassMode.INCREASED_CONTRAST])
def test_accessibility_modes_paint_no_hover_light(
    button: GlassButton, mode: GlassMode, monkeypatch: pytest.MonkeyPatch
) -> None:
    painted: list[str] = []
    monkeypatch.setattr(glass_button, "paint_glow", lambda *args: painted.append("glow"))
    monkeypatch.setattr(glass_button, "paint_specular", lambda *args: painted.append("rim"))
    button._light_at = QPointF(10, 10)
    button._set_hover(1.0)
    set_glass_preferences(GlassPreferences(mode=mode))
    assert glass_preferences().mode is mode
    picture(button)
    assert painted == []
    set_glass_preferences(GlassPreferences())
    picture(button)
    assert painted == ["glow", "rim"]


def test_a_glass_pill_s_icon_sits_beside_its_label(
    qtbot: QtBot, qapp: QApplication, themed: None
) -> None:
    """A plain icon, in the label's colour, before the label: white on the primary."""
    apply_theme(qapp)
    made = outline_button("Open in Gmail", "openInGmailButton", glass=True, icon_name="mail")
    qtbot.addWidget(made)
    assert isinstance(made, GlassButton)
    assert not made.icon().isNull()
    assert (made.iconSize().width(), made.iconSize().height()) == (14, 14)
    plain = outline_button("Open in Gmail", "plain", glass=True)
    qtbot.addWidget(plain)
    gap = glass_button.ICON_GAP
    assert made.sizeHint().width() == plain.sizeHint().width() + 14 + gap
    assert made.font().weight() == plain.font().weight()  # Both Medium.


def test_an_icon_alone_makes_a_centred_circle(
    qtbot: QtBot, qapp: QApplication, themed: None
) -> None:
    apply_theme(qapp)
    round_ = outline_button(
        "", "searchButton", icon_name="search", accessible_name="Search", glass=True
    )
    labelled = outline_button("Search", "labelled", icon_name="search", glass=True)
    for made in (round_, labelled):
        qtbot.addWidget(made)
    assert isinstance(round_, GlassButton) and round_.is_round()
    assert isinstance(labelled, GlassButton) and not labelled.is_round()
    hint = round_.sizeHint()
    assert hint.width() == hint.height()  # A circle, in the same room as a pill.
    assert hint.height() == labelled.sizeHint().height()
    assert round_.accessibleName() == "Search"
    round_.resize(hint)
    round_.show()
    qtbot.waitExposed(round_)
    # The icon sits in the middle: its ink is as far from the left as from the right.
    image = picture(round_)
    background = image.pixelColor(1, image.height() // 2)
    inked = [
        x
        for x in range(image.width())
        for y in range(image.height() // 2 - 3, image.height() // 2 + 4)
        if image.pixelColor(x, y) != background
    ]
    assert inked
    left, right = min(inked), image.width() - 1 - max(inked)
    assert abs(left - right) <= 2
    # Hovered, it grows evenly, so it stays a circle.
    round_.hover_animation.stop()
    round_._set_hover(1.0)
    pill = round_._pill()
    assert pill.width() == pytest.approx(pill.height())
