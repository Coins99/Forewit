"""Controls the theme style draws itself: rounded fields with chevrons, and Apple-style
check boxes and radio buttons."""

import pytest
from PySide6.QtCore import QDate, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionSpinBox,
    QVBoxLayout,
    QWidget,
)
from pytestqt.qtbot import QtBot

from mailbrief.ui.labels import outline_button
from mailbrief.ui.theme import (
    DIALOG_GUTTER,
    DIALOG_MARGIN,
    ThemeMode,
    apply_theme,
    current_tokens,
    tokens_for,
)
from mailbrief.ui.theme.indicators import indicator_colors, paint_check_box, paint_radio
from mailbrief.ui.theme.style import CHEVRON_PX, button_colors, paint_chevron
from tests.ui.test_keyboard import activate


def grab(widget: QWidget) -> QImage:
    return widget.grab().toImage()


def colours(image: QImage) -> set[str]:
    return {
        image.pixelColor(x, y).name() for y in range(image.height()) for x in range(image.width())
    }


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_fields_are_rounded_boxes_with_a_chevron(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    apply_theme(qapp, mode)
    tokens = tokens_for(mode)
    combo = QComboBox()
    combo.addItems(["All tasks", "Work"])
    date = QDateEdit(QDate(2026, 10, 13))
    date.setCalendarPopup(True)
    spin = QSpinBox()
    line = QLineEdit("text")
    for widget in (combo, date, spin, line):
        qtbot.addWidget(widget)
        widget.resize(200, widget.sizeHint().height())
        widget.show()
    # Every field is exactly as tall as a text field, on every platform (Fusion's own
    # spin box height differs between them), and drawn in the panel colour.
    for widget in (combo, date, spin):
        assert widget.height() == line.height(), widget
    for widget in (combo, date, spin):
        image = grab(widget)
        ratio = image.devicePixelRatio()
        inside = image.pixelColor(round(40 * ratio), round(4 * ratio)).name()
        assert inside == tokens.panel, widget
        # The corner is outside the rounded box: the parent's colour shows, not the panel's
        # hairline.
        assert image.pixelColor(0, 0).name() != tokens.hairline
        # A chevron in the secondary text colour near the right edge.
        right = image.copy(
            round((widget.width() - 24) * ratio), 0, round(24 * ratio), image.height()
        )
        assert any(
            abs(QColor(c).lightness() - QColor(tokens.text_secondary).lightness()) < 40
            for c in colours(right) - {tokens.panel, tokens.hairline}
        )


def test_the_edit_field_starts_at_a_text_field_s_padding(
    qtbot: QtBot, qapp: QApplication, themed: None
) -> None:
    """A combo box's and a spin box's text starts 8 px inside the border, as a text
    field's does; the sub-control values overlap between controls (a combo box's edit
    field is a spin box's down button), so the spin box keeps both its buttons."""
    apply_theme(qapp)
    style = qapp.style()
    spin = QSpinBox()
    qtbot.addWidget(spin)
    spin.resize(200, spin.sizeHint().height())
    option = QStyleOptionSpinBox()
    spin.initStyleOption(option)
    control = QStyle.ComplexControl.CC_SpinBox
    field = style.subControlRect(control, option, QStyle.SubControl.SC_SpinBoxEditField, spin)
    down = style.subControlRect(control, option, QStyle.SubControl.SC_SpinBoxDown, spin)
    assert field.left() == 9
    assert down.left() > field.right() - 30  # Still a button at the right.


@pytest.mark.parametrize("up", [False, True])
def test_a_chevron_points_the_right_way(qapp: QApplication, up: bool) -> None:
    image = QImage(20, 20, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    paint_chevron(painter, QRectF(0, 0, 20, 20), "#000000", up=up)
    painter.end()
    lit = [(x, y) for y in range(20) for x in range(20) if image.pixelColor(x, y).alpha() > 128]
    assert lit
    xs = [x for x, _ in lit]
    assert max(xs) - min(xs) <= CHEVRON_PX + 2
    tip = [y for x, y in lit if x == 10]
    ends = [y for x, y in lit if x in (min(xs), max(xs))]
    # Down: the tip is below the ends; up: above.
    assert (min(tip) < min(ends)) if up else (max(tip) > max(ends))


@pytest.mark.parametrize("mode", list(ThemeMode))
@pytest.mark.parametrize("checked", [False, True])
def test_check_boxes_and_radios_fill_with_the_primary_colour(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode, checked: bool
) -> None:
    apply_theme(qapp, mode)
    tokens = current_tokens()
    fill = indicator_colors(True, tokens)[1]
    for widget in (QCheckBox("Box"), QRadioButton("Radio")):
        qtbot.addWidget(widget)
        widget.setChecked(checked)
        widget.resize(widget.sizeHint())
        found = colours(grab(widget))
        assert (fill in found) is checked, (widget, mode)


@pytest.mark.parametrize("partial", [False, True])
def test_a_check_box_draws_its_mark_or_dash(qapp: QApplication, partial: bool) -> None:
    tokens = tokens_for(ThemeMode.LIGHT)
    image = QImage(32, 32, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(QColor(tokens.panel))
    painter = QPainter(image)
    paint_check_box(painter, QRectF(0, 0, 32, 32), tokens, checked=not partial, partial=partial)
    painter.end()
    fill = QColor(tokens.primary_fill).lightness()
    # The dash straddles the middle rows; the check mark's middle dips below them.
    middle = [x for x in range(32) if image.pixelColor(x, 15).lightness() > fill + 40]
    if partial:
        assert len(middle) >= 10  # A dash across the middle.
    else:
        assert len(middle) < 10
    assert image.pixelColor(4, 4).name() == tokens.primary_fill


def test_a_disabled_indicator_is_fainter(qapp: QApplication) -> None:
    tokens = tokens_for(ThemeMode.LIGHT)

    def centre(enabled: bool) -> QColor:
        image = QImage(20, 20, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(tokens.panel))
        painter = QPainter(image)
        paint_radio(painter, QRectF(0, 0, 20, 20), tokens, checked=True, enabled=enabled)
        painter.end()
        return image.pixelColor(4, 10)

    assert centre(False).lightness() > centre(True).lightness()


@pytest.mark.parametrize("checked", [False, True])
@pytest.mark.parametrize("mode", list(ThemeMode))
def test_a_check_box_without_text_shows_keyboard_focus_on_its_box(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode, checked: bool
) -> None:
    """A step's box (B04) has no label for the focus to ring, so the ring goes on the box,
    ticked or not, once the keyboard moves the focus to it."""
    apply_theme(qapp, mode)
    host = QWidget()
    qtbot.addWidget(host)
    layout = QVBoxLayout(host)
    first, box = QPushButton("First"), QCheckBox()
    box.setChecked(checked)
    layout.addWidget(first)
    layout.addWidget(box)
    activate(qtbot, host)
    first.setFocus()
    before = grab(box)

    QTest.keyClick(first, Qt.Key.Key_Tab)

    assert box.hasFocus()
    after = grab(box)

    def change(x: int, y: int) -> int:
        was, now = before.pixelColor(x, y), after.pixelColor(x, y)
        return max(
            abs(was.red() - now.red()),
            abs(was.green() - now.green()),
            abs(was.blue() - now.blue()),
        )

    changed = sum(change(x, y) > 40 for y in range(after.height()) for x in range(after.width()))
    assert changed >= 20  # Without the ring, no pixel changes by more than 40.


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_menus_and_combo_lists_have_round_corners(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    """Pop-ups are windows of their own: translucent, so their corners are clear, not
    square in the window's colour; a combo box's list paints as a rounded panel."""
    apply_theme(qapp, mode)
    tokens = tokens_for(mode)
    menu = QMenu()
    qtbot.addWidget(menu)
    menu.addAction("Reply")
    menu.addAction("Email")
    menu.resize(menu.sizeHint())
    menu.show()
    image = grab(menu)
    assert menu.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert image.pixelColor(0, 0).alpha() == 0
    assert image.pixelColor(image.width() // 2, image.height() // 2).alpha() == 255

    host = QWidget()
    qtbot.addWidget(host)
    combo = QComboBox(host)
    combo.addItems(["All tasks", "Work", "Family"])
    host.show()
    assert isinstance(combo.itemDelegate(), QStyledItemDelegate)
    combo.showPopup()
    popup = combo.view().window()
    image = grab(popup)
    assert image.pixelColor(0, 0).alpha() == 0
    ratio = image.devicePixelRatio()
    middle = image.pixelColor(round(4 * ratio), image.height() // 2)
    assert middle.name() in (tokens.panel, tokens.selection)
    combo.hidePopup()


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_every_push_button_is_flat(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    """A plain push button, as Qt's own in a message box, is flat, as the fields are: a
    rounded rectangle in a hairline edge, the size the glass pill was, with a Medium
    label; the primary one `primary_fill`; fainter disabled."""
    apply_theme(qapp, mode)
    tokens = current_tokens()
    host = QWidget()
    qtbot.addWidget(host)
    plain = QPushButton("Save", host)
    primary = QPushButton("Continue", host)
    primary.setProperty("variant", "primary")
    for button in (plain, primary):
        button.ensurePolished()
        button.resize(button.sizeHint())
    plain.move(10, 10)
    primary.move(10, 60)
    host.resize(200, 110)
    host.show()
    assert plain.font().weight() == QFont.Weight.Medium
    text = plain.fontMetrics().horizontalAdvance("Save")
    padding = plain.sizeHint().width() - text  # Qt may round the text up a pixel.
    assert 2 * (10 + 3) <= padding <= 2 * (10 + 3) + 1
    assert plain.sizeHint().height() == plain.fontMetrics().height() + 2 * (6 + 3)

    def at(button: QPushButton, x: float, y: float | None = None) -> QColor:
        image = button.grab().toImage()
        ratio = image.devicePixelRatio()
        row = image.height() // 2 if y is None else round(y * ratio)
        return image.pixelColor(round(x * ratio), row)

    # 3 px of room, then the 1 px edge, then the fill; the corner is rounded off.
    assert at(plain, 3).name() == tokens.hairline
    assert at(plain, 12).name() == tokens.panel
    assert at(plain, 3, 3).name() != tokens.hairline
    assert at(primary, 3).name() == tokens.primary_fill  # No edge: the fill reaches it.
    assert at(primary, 12).name() == tokens.primary_fill
    resting = at(plain, 3)
    plain.setEnabled(False)
    assert at(plain, 3) != resting


S = QStyle.StateFlag


@pytest.mark.parametrize(
    ("state", "primary", "fill", "edge"),
    [
        (S.State_Enabled, False, "panel", "hairline"),
        (S.State_Enabled | S.State_MouseOver, False, "card", "hairline"),
        (S.State_Enabled | S.State_MouseOver | S.State_Sunken, False, "button_pressed", "hairline"),
        (S.State_Enabled | S.State_On, False, "selection", "accent_border"),
        (S.State_MouseOver, False, "panel", "hairline"),  # Disabled: no hover.
        (S.State_Enabled, True, "primary_fill", None),
        (S.State_Enabled | S.State_MouseOver, True, "primary_hover", None),
        (S.State_Enabled | S.State_Sunken, True, "primary_pressed", None),
    ],
)
def test_a_button_s_colours_follow_its_state(
    state: QStyle.StateFlag, primary: bool, fill: str, edge: str | None
) -> None:
    tokens = tokens_for(ThemeMode.LIGHT)
    expected_edge = None if edge is None else getattr(tokens, edge)
    assert button_colors(tokens, state, primary=primary) == (
        getattr(tokens, fill),
        expected_edge,
    )


def test_increased_contrast_edges_every_button_with_the_contrast_rim() -> None:
    tokens = tokens_for(ThemeMode.DARK)
    for primary in (False, True):
        _fill, edge = button_colors(
            tokens, S.State_Enabled, primary=primary, contrast_edge="#a1a1a6"
        )
        assert edge == "#a1a1a6"


def test_keyboard_focus_rings_a_button_and_a_click_does_not(
    qtbot: QtBot, qapp: QApplication, themed: None
) -> None:
    apply_theme(qapp, ThemeMode.LIGHT)
    accent = current_tokens().accent_fg
    host = QWidget()
    qtbot.addWidget(host)
    button = QPushButton("Save", host)
    button.resize(button.sizeHint())
    host.resize(120, 60)
    host.show()
    qtbot.waitExposed(host)

    def ringed() -> bool:
        image = button.grab().toImage()
        ratio = image.devicePixelRatio()
        row = image.height() // 2
        return any(image.pixelColor(round(x * ratio), row).name() == accent for x in (4, 4.5, 5))

    button.setFocus(Qt.FocusReason.MouseFocusReason)
    qtbot.waitUntil(button.hasFocus)
    assert not ringed()
    host.setAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange, True)  # As Tab does.
    button.update()
    assert ringed()


def test_an_icon_alone_makes_a_square_button(
    qtbot: QtBot, qapp: QApplication, themed: None
) -> None:
    apply_theme(qapp, ThemeMode.LIGHT)
    button = outline_button("", "searchButton", icon_name="search", accessible_name="Search")
    qtbot.addWidget(button)
    button.ensurePolished()
    hint = button.sizeHint()
    assert hint.width() == hint.height()


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_a_primary_menu_button_s_arrow_is_white_as_its_label(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    """Draft follow-up, the Waiting page's primary action, opens a menu: its arrow on the
    blue fill is the label's white, never the secondary grey of a field's chevron, which
    barely shows on blue in light mode."""
    apply_theme(qapp, mode)
    button = QPushButton("Draft follow-up")
    qtbot.addWidget(button)
    button.setMenu(QMenu(button))
    button.setProperty("variant", "primary")
    button.ensurePolished()
    button.resize(button.sizeHint())
    button.show()
    image = grab(button)
    ratio = image.devicePixelRatio()
    # The arrow's band: the right end, inside the pill's rim, around the middle.
    band = image.copy(
        round((button.width() - 26) * ratio),
        round((button.height() / 2 - 4) * ratio),
        round(18 * ratio),
        round(8 * ratio),
    )
    fill = QColor(current_tokens().primary_fill)
    white = QColor(current_tokens().on_primary)
    arrow = [QColor(c) for c in colours(band)]
    assert max(c.lightness() for c in arrow) > (fill.lightness() + white.lightness()) / 2
    assert all(c.lightness() >= fill.lightness() - 12 for c in arrow)


@pytest.mark.parametrize("mode", list(ThemeMode))
def test_every_dialog_has_the_home_screen_s_sheet(
    qtbot: QtBot, qapp: QApplication, themed: None, mode: ThemeMode
) -> None:
    """Glass at the window's edge, the content on a rounded panel-coloured sheet inside
    it, with no square corner, and the dialog's own layout inside the sheet."""
    apply_theme(qapp, mode)
    tokens = current_tokens()
    for dialog in (QDialog(), QMessageBox(QMessageBox.Icon.NoIcon, "Forewit", "Discard?")):
        qtbot.addWidget(dialog)
        dialog.setLayout(dialog.layout() or QVBoxLayout())
        dialog.resize(320, 200)
        dialog.show()
        image = dialog.grab().toImage()

        def at(x: float, y: float, image: QImage = image) -> str:
            ratio = image.devicePixelRatio()
            return image.pixelColor(round(x * ratio), round(y * ratio)).name()

        # A message box sizes itself, so sample by the size it took.
        width, height = dialog.width(), dialog.height()
        assert at(2, height / 2) != tokens.panel  # The glass round the sheet.
        corner = DIALOG_GUTTER + 1  # Inside the sheet's square corner, outside its curve.
        assert at(corner, corner) != tokens.panel
        assert at(width / 2, DIALOG_GUTTER + 2) == tokens.panel  # Its straight top edge.
    plain = QDialog()
    qtbot.addWidget(plain)
    layout = QVBoxLayout(plain)
    assert layout.contentsMargins().left() == DIALOG_MARGIN
