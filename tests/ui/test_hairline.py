"""Cosmetic hairlines: cards, dividers and the splitter handle paint with token colours."""

import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QRegion
from PySide6.QtWidgets import QApplication, QLabel, QStyle, QWidget
from pytestqt.qtbot import QtBot

from mailbrief.ui.hairline import (
    SELECTION_INSET_X,
    SELECTION_INSET_Y,
    HairlineDivider,
    HairlineFrame,
    HairlineSplitter,
    hairline_pen,
    keyboard_focus,
    paint_focus_ring,
)
from mailbrief.ui.theme import DARK, apply_theme


def test_hairline_pen_is_cosmetic() -> None:
    pen = hairline_pen(DARK.hairline)
    assert pen.isCosmetic() and pen.width() == 0
    assert pen.color() == QColor(DARK.hairline)


def test_card_divider_and_splitter_paint_hairlines(
    qtbot: QtBot, qapp: QApplication, themed: None
) -> None:
    apply_theme(qapp)
    card = HairlineFrame(strong=True)
    qtbot.addWidget(card)
    card.resize(120, 60)
    assert card.contentsMargins().left() == 16 and card.contentsMargins().top() == 14
    image = card.grab().toImage()
    middle = image.height() // 2
    assert image.pixelColor(0, middle) == QColor(DARK.border_strong)
    assert image.pixelColor(image.width() // 2, middle) == QColor(DARK.card)
    # A plain card is its fill alone, with no border.
    plain = HairlineFrame()
    qtbot.addWidget(plain)
    plain.resize(120, 60)
    filled = plain.grab().toImage()
    assert filled.pixelColor(0, middle) == QColor(DARK.card)
    assert filled.pixelColor(filled.width() // 2, middle) == QColor(DARK.card)

    divider = HairlineDivider(Qt.Orientation.Vertical)
    qtbot.addWidget(divider)
    divider.resize(1, 40)
    assert divider.width() == 1
    assert divider.grab().toImage().pixelColor(0, 20) == QColor(DARK.hairline)
    horizontal = HairlineDivider()
    qtbot.addWidget(horizontal)
    assert horizontal.maximumHeight() == 1
    assert not horizontal.grab().isNull()

    splitter = HairlineSplitter()
    qtbot.addWidget(splitter)
    splitter.addWidget(QLabel("left"))
    splitter.addWidget(QLabel("right"))
    splitter.resize(200, 50)
    splitter.show()
    assert splitter.handleWidth() == 5  # Wide enough to grab.
    handle = splitter.handle(1).grab().toImage()
    # Grabs are in device pixels: 5 at 1x, 10 on a 2x screen.
    pixels = round(5 * handle.devicePixelRatio())
    assert handle.width() == pixels
    middle = handle.height() // 2
    row = [handle.pixelColor(x, middle) for x in range(pixels)]
    # One device-pixel line at the centre, panel colour either side.
    assert row.count(QColor(DARK.hairline)) == 1
    assert row[(pixels - 1) // 2] == QColor(DARK.hairline)
    assert row[0] == row[-1] == QColor(DARK.panel)
    # The line stops short of both ends, so it meets no other hairline.
    centre = (pixels - 1) // 2
    assert handle.pixelColor(centre, 0) == QColor(DARK.panel)
    assert handle.pixelColor(centre, handle.height() - 1) == QColor(DARK.panel)


FOCUS = QStyle.StateFlag.State_HasFocus
KEYBOARD = QStyle.StateFlag.State_KeyboardFocusChange


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (QStyle.StateFlag.State_None, False),
        (FOCUS, False),
        (KEYBOARD, False),
        (FOCUS | KEYBOARD, True),
    ],
)
def test_the_focus_ring_needs_keyboard_focus(state: QStyle.StateFlag, expected: bool) -> None:
    assert keyboard_focus(state) is expected


def ring_image(state: QStyle.StateFlag) -> QImage:
    image = QImage(20, 20, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    paint_focus_ring(painter, QRect(0, 0, 20, 20), state, DARK.accent_fg)
    painter.end()
    return image


def transparent(image: QImage) -> bool:
    return all(
        image.pixelColor(x, y).alpha() == 0
        for x in range(image.width())
        for y in range(image.height())
    )


def test_the_focus_ring_paints_only_after_keyboard_movement(qapp: QApplication) -> None:
    assert transparent(ring_image(FOCUS))
    assert not transparent(ring_image(FOCUS | KEYBOARD))


@pytest.mark.parametrize("dpr", [1.0, 1.25, 1.5, 2.0])
def test_a_card_border_is_one_crisp_device_pixel_at_any_scale(
    qtbot: QtBot, qapp: QApplication, themed: None, dpr: float
) -> None:
    """At 125% and 150% a card at an odd position has edges between device pixels; its
    border is snapped onto the grid, so it is still one full-strength pixel, and its fill
    starts on a whole pixel too."""
    apply_theme(qapp)

    def top_rows(strong: bool) -> list[str]:
        parent = QWidget()
        qtbot.addWidget(parent)
        parent.resize(200, 100)
        card = HairlineFrame(parent, strong=strong)
        card.setGeometry(3, 3, 120, 60)
        size = (round(200 * dpr), round(100 * dpr))
        picture = QImage(*size, QImage.Format.Format_ARGB32_Premultiplied)
        picture.setDevicePixelRatio(dpr)
        picture.fill(QColor(DARK.panel))
        parent.render(picture, QPoint(), QRegion(), QWidget.RenderFlag.DrawChildren)
        column = round(60 * dpr)  # Where the card's top edge is straight.
        return [picture.pixelColor(column, y).name() for y in range(round(12 * dpr))]

    rows = top_rows(strong=True)
    border = [colour for colour in rows if colour not in (DARK.panel, DARK.card)]
    assert border == [DARK.border_strong], (dpr, rows)
    after = rows[rows.index(DARK.border_strong) + 1 :]
    assert set(after) == {DARK.card}, (dpr, rows)
    rows = top_rows(strong=False)
    assert set(rows) == {DARK.panel, DARK.card}, (dpr, rows)  # No blended edge.


def test_the_focus_ring_is_rounded_round_the_selection(qapp: QApplication) -> None:
    """The ring follows a row's rounded selection: inset like it, with round corners."""
    image = QImage(80, 40, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    paint_focus_ring(painter, QRect(0, 0, 80, 40), FOCUS | KEYBOARD, DARK.accent_fg)
    painter.end()
    left, top = SELECTION_INSET_X, SELECTION_INSET_Y
    assert image.pixelColor(40, top).alpha() > 0  # The straight top edge.
    assert image.pixelColor(left, 20).alpha() > 0  # The straight left edge.
    assert image.pixelColor(left, top).alpha() == 0  # The corner is cut round.
    assert image.pixelColor(1, 1).alpha() == 0  # Nothing outside the selection.


def test_a_pill_focus_ring_rounds_its_ends(qapp: QApplication) -> None:
    image = QImage(80, 32, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    paint_focus_ring(
        painter, QRect(0, 0, 80, 32), FOCUS | KEYBOARD, DARK.accent_fg, inset=(1, 2), radius=None
    )
    painter.end()
    assert image.pixelColor(40, 2).alpha() > 0
    assert image.pixelColor(3, 4).alpha() == 0  # Well inside the pill's round end.
