"""The Liquid Glass pill button. Made only by `outline_button(..., glass=True)`, which
handles its text, name and icon like every other outline button."""

import math
from typing import Final

from PySide6.QtCore import QEasingCurve, QEvent, QPointF, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import (
    QColor,
    QEnterEvent,
    QFontMetricsF,
    QIcon,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPaintEvent,
    QPen,
)
from PySide6.QtWidgets import QPushButton

from mailbrief.ui.glass.painter import GlassMode, paint_pill, paint_specular
from mailbrief.ui.glass.preferences import glass_preferences
from mailbrief.ui.theme import current_glass, current_tokens

# The outline button's box: text plus 14 px each side and 6 px above and below.
PAD_X: Final = 14
PAD_Y: Final = 6
ICON_GAP: Final = 6
LIFT: Final = 1  # How far a hovered pill rises; the button keeps room for it.
FOCUS_WIDTH: Final = 2.0  # The focus ring, inside the pill's edge.
PRESSED_SCALE: Final = 0.97
DISABLED_OPACITY: Final = 0.5
PRESS_MS: Final = 90
RELEASE_MS: Final = 280


class GlassButton(QPushButton):
    """A pill of glass with its text and icon centred.

    Hovering lifts it a pixel and lights its rim near the pointer; pressing shrinks it
    slightly and releasing springs it back, both at once when motion is reduced. Focus
    draws a ring in the accent colour, and ``primary`` tints the one primary action.
    Reduced transparency and increased contrast paint it opaque.
    """

    def __init__(self, text: str, *, primary: bool = False) -> None:
        super().__init__(text)
        self.primary = primary
        self.setProperty("variant", "glass")
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self._pointer: QPointF | None = None
        self._scale = 1.0
        self.press_animation = QVariantAnimation(self)
        self.press_animation.valueChanged.connect(self._set_scale)

    def _set_scale(self, value: object) -> None:
        if isinstance(value, float):
            self._scale = value
            self.update()

    def scale(self) -> float:
        return self._scale

    def _shown_text(self) -> str:
        """The text as painted: mnemonic markers dropped and ``&&`` shown as ``&``."""
        return self.text().replace("&&", "\0").replace("&", "").replace("\0", "&")

    def sizeHint(self) -> QSize:
        metrics = QFontMetricsF(self.font())
        width = metrics.horizontalAdvance(self._shown_text()) + 2 * PAD_X
        if not self.icon().isNull():
            width += self.iconSize().width() + ICON_GAP
        height = metrics.height() + 2 * PAD_Y + 2 * LIFT
        return QSize(math.ceil(width), math.ceil(height))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _animate_scale(self, target: float, duration: int, curve: QEasingCurve.Type) -> None:
        animation = self.press_animation
        animation.stop()
        if glass_preferences().reduce_motion:
            animation.setDuration(0)
            self._set_scale(target)
            return
        animation.setDuration(duration)
        animation.setEasingCurve(curve)
        animation.setStartValue(self._scale)
        animation.setEndValue(target)
        animation.start()

    def enterEvent(self, event: QEnterEvent) -> None:
        self._pointer = event.position()
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._pointer = None
        self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._pointer = event.position()
        self.update()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        super().mousePressEvent(event)
        if self.isDown():
            self._animate_scale(PRESSED_SCALE, PRESS_MS, QEasingCurve.Type.OutQuad)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        super().mouseReleaseEvent(event)
        self._animate_scale(1.0, RELEASE_MS, QEasingCurve.Type.OutBack)

    def _pill(self) -> QRectF:
        """The pill's rectangle: lifted when hovered, scaled about its centre."""
        rect = QRectF(self.rect()).adjusted(0, LIFT, 0, -LIFT)
        motion = not glass_preferences().reduce_motion
        if motion and self.isEnabled() and self._pointer is not None and not self.isDown():
            rect.translate(0, -LIFT)
        if self._scale != 1.0:
            centre = rect.center()
            rect.setSize(rect.size() * self._scale)
            rect.moveCenter(centre)
        return rect

    def paintEvent(self, event: QPaintEvent) -> None:
        glass, tokens, preferences = current_glass(), current_tokens(), glass_preferences()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._pill()
        if not self.isEnabled():
            painter.setOpacity(DISABLED_OPACITY)
        paint_pill(painter, rect, glass, primary=self.primary, mode=preferences.mode)
        normal = preferences.mode is GlassMode.NORMAL
        if normal and self.isEnabled() and self._pointer is not None:
            paint_specular(painter, rect, glass, self._pointer)
        painter.setOpacity(1.0)
        if self.hasFocus():
            ring = QPainterPath()
            inset = FOCUS_WIDTH / 2
            inner = rect.adjusted(inset, inset, -inset, -inset)
            ring.addRoundedRect(inner, inner.height() / 2, inner.height() / 2)
            painter.setPen(QPen(QColor(tokens.accent_fg), FOCUS_WIDTH))
            painter.drawPath(ring)
        self._paint_label(painter, rect, tokens.text if self.isEnabled() else tokens.text_muted)
        painter.end()

    def _paint_label(self, painter: QPainter, rect: QRectF, text_color: str) -> None:
        metrics = QFontMetricsF(self.font())
        text_width = metrics.horizontalAdvance(self._shown_text())
        icon_width = 0 if self.icon().isNull() else self.iconSize().width()
        gap = ICON_GAP if icon_width else 0
        left = rect.center().x() - (icon_width + gap + text_width) / 2
        if icon_width:
            size = self.iconSize()
            mode = QIcon.Mode.Normal if self.isEnabled() else QIcon.Mode.Disabled
            pixmap = self.icon().pixmap(size, self.devicePixelRatioF(), mode)
            top = rect.center().y() - size.height() / 2
            painter.drawPixmap(QPointF(left, top), pixmap)
        painter.setFont(self.font())
        painter.setPen(QColor(text_color))
        flags = Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        area = QRectF(left + icon_width + gap, rect.top(), text_width + 1, rect.height())
        painter.drawText(area, int(flags) | int(Qt.TextFlag.TextHideMnemonic), self.text())
