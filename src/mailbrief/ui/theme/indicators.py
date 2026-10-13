"""Check boxes and radio buttons as Apple draws them: an outline while off; the primary fill
with a mark in its text colour while on. The theme style draws Qt's own with these, and
the shortlist draws its rows' boxes with them too, so every check box looks the same."""

from typing import Final

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen

from mailbrief.ui.theme.tokens import Tokens

# No fill and no mark: an unchecked box is only its outline.
NONE: Final = "transparent"
BOX_RADIUS: Final = 4.0
MARK_WIDTH: Final = 1.5
DISABLED_OPACITY: Final = 0.5
# A radio button's dot, as a share of its diameter.
DOT_SHARE: Final = 0.4


def indicator_colors(checked: bool, tokens: Tokens) -> tuple[str, str, str]:
    """The (border, fill, mark) of a check box or radio button: an outline when off, the
    primary fill with a mark in its text colour when on."""
    if checked:
        return tokens.accent_border, tokens.primary_fill, tokens.on_primary
    return tokens.border_strong, NONE, NONE


def _outline(painter: QPainter, rect: QRectF, border: str, fill: str) -> QRectF:
    """Set a one-device-pixel pen and the fill; returns ``rect`` with the outline on pixel
    centres, so it stays crisp."""
    inset = 0.5 / painter.device().devicePixelRatioF()
    pen = QPen(QColor(border), 0)
    pen.setCosmetic(True)
    painter.setPen(pen)
    painter.setBrush(QColor(fill))
    return rect.adjusted(inset, inset, -inset, -inset)


def paint_check_box(
    painter: QPainter,
    rect: QRectF,
    tokens: Tokens,
    *,
    checked: bool,
    partial: bool = False,
    enabled: bool = True,
) -> None:
    """A rounded check box filling ``rect``: a check mark when ``checked``, a dash when
    ``partial``; at half strength when disabled."""
    on = checked or partial
    border, fill, mark = indicator_colors(on, tokens)
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if not enabled:
        painter.setOpacity(DISABLED_OPACITY)
    box = _outline(painter, rect, border, fill)
    painter.drawRoundedRect(box, BOX_RADIUS, BOX_RADIUS)
    if on:
        pen = QPen(QColor(mark), MARK_WIDTH)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        left, top, width, height = box.left(), box.top(), box.width(), box.height()
        if checked:
            painter.drawPolyline(
                [
                    QPointF(left + 0.26 * width, top + 0.52 * height),
                    QPointF(left + 0.43 * width, top + 0.69 * height),
                    QPointF(left + 0.75 * width, top + 0.33 * height),
                ]
            )
        else:
            middle = top + height / 2
            painter.drawLine(
                QPointF(left + 0.28 * width, middle), QPointF(left + 0.72 * width, middle)
            )
    painter.restore()


def paint_radio(
    painter: QPainter, rect: QRectF, tokens: Tokens, *, checked: bool, enabled: bool = True
) -> None:
    """A round radio button filling ``rect``, with a dot when ``checked``."""
    border, fill, mark = indicator_colors(checked, tokens)
    size = min(rect.width(), rect.height())
    circle = QRectF(0, 0, size, size)
    circle.moveCenter(rect.center())
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    if not enabled:
        painter.setOpacity(DISABLED_OPACITY)
    painter.drawEllipse(_outline(painter, circle, border, fill))
    if checked:
        dot = QRectF(0, 0, size * DOT_SHARE, size * DOT_SHARE)
        dot.moveCenter(circle.center())
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(mark))
        painter.drawEllipse(dot)
    painter.restore()
