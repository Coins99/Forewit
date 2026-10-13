"""Controls the theme style draws itself: rounded fields with chevrons, and Apple-style
check boxes and radio buttons."""

import pytest
from PySide6.QtCore import QDate, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QLineEdit,
    QMenu,
    QRadioButton,
    QSpinBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionSpinBox,
    QWidget,
)
from pytestqt.qtbot import QtBot

from mailbrief.ui.theme import ThemeMode, apply_theme, current_tokens, tokens_for
from mailbrief.ui.theme.indicators import indicator_colors, paint_check_box, paint_radio
from mailbrief.ui.theme.style import CHEVRON_PX, paint_chevron


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
