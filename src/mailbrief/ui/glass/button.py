"""The Liquid Glass pill button. Made only by `outline_button(..., glass=True)`, which
handles its text, name and icon like every other outline button."""

import math
from collections.abc import Callable
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
from PySide6.QtWidgets import QPushButton, QStyleOptionButton

from mailbrief.ui.glass.painter import (
    GlassMode,
    paint_glow,
    paint_liquid_pill,
    paint_pill,
    paint_specular,
)
from mailbrief.ui.glass.preferences import glass_preferences
from mailbrief.ui.glass.profile import profiled
from mailbrief.ui.hairline import keyboard_focus
from mailbrief.ui.theme import TEXT_PX, current_glass, current_tokens, ui_font

# The pill's box: its label plus 10 px each side and 6 px above and below.
PAD_X: Final = 10
PAD_Y: Final = 6
ICON_GAP: Final = 6
# Room the button keeps round its pill, each side, for the pill to grow into on hover and
# for its shadow.
ROOM_X: Final = 3
ROOM_Y: Final = 3
# How far a hovered pill grows, each side; the hover spring overshoots by about a tenth,
# still inside the room.
GROW_X: Final = 2.4
GROW_Y: Final = 0.8
FOCUS_WIDTH: Final = 2.0  # The focus ring, inside the pill's edge.
PRESSED_SCALE: Final = 0.97
DISABLED_OPACITY: Final = 0.5
PRESS_MS: Final = 90
RELEASE_MS: Final = 280
HOVER_IN_MS: Final = 260
HOVER_OUT_MS: Final = 200


class GlassButton(QPushButton):
    """A pill of glass with its text and icon centred.

    Hovering animates in, as Apple's glass does: the pill grows a little on a spring, a
    glow starts inside its edge under the pointer and spreads round it, and its rim lights
    near the pointer, which the light follows. Leaving eases it all back. Pressing, by
    mouse or keyboard, shrinks the pill slightly and releasing springs it back. With
    motion reduced, the light comes at once and nothing moves; reduced transparency and
    increased contrast paint the pill opaque, with no light. Keyboard focus draws a ring
    in the accent colour (a click doesn't), and ``primary`` tints the one primary action.
    """

    def __init__(self, text: str, *, primary: bool = False) -> None:
        super().__init__(text)
        self.primary = primary
        self.setFont(ui_font(TEXT_PX, medium=True))
        self.setProperty("variant", "glass")
        self.setMouseTracking(True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self._light_at: QPointF | None = None  # The pointer, kept while the light fades out.
        self._hover = 0.0  # 0 at rest, 1 hovered; the spring passes 1 on the way in.
        self._scale = 1.0
        self.press_animation = QVariantAnimation(self)
        self.press_animation.valueChanged.connect(self._set_scale)
        self.hover_animation = QVariantAnimation(self)
        self.hover_animation.valueChanged.connect(self._set_hover)
        # Qt emits these for mouse and keyboard presses alike, and emits released when a
        # held button is disabled, so the pill never stays shrunk.
        self.pressed.connect(self._pressed)
        self.released.connect(self._released)

    def _set_scale(self, value: object) -> None:
        if isinstance(value, float):
            self._scale = value
            self.update()

    def _set_hover(self, value: object) -> None:
        if isinstance(value, float):
            self._hover = value
            self.update()

    def scale(self) -> float:
        return self._scale

    def hover(self) -> float:
        return self._hover

    def _shown_text(self) -> str:
        """The text as painted: mnemonic markers dropped and ``&&`` shown as ``&``."""
        return self.text().replace("&&", "\0").replace("&", "").replace("\0", "&")

    def _content_width(self) -> float:
        """The pill's width for its content: the padding, the icon and the label."""
        text = QFontMetricsF(self.font()).horizontalAdvance(self._shown_text())
        width = text + 2 * PAD_X
        if not self.icon().isNull():
            width += self.iconSize().width() + ICON_GAP
        return width

    def sizeHint(self) -> QSize:
        metrics = QFontMetricsF(self.font())
        height = metrics.height() + 2 * PAD_Y
        width = self._content_width()
        return QSize(math.ceil(width + 2 * ROOM_X), math.ceil(height + 2 * ROOM_Y))

    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _animate(
        self,
        animation: QVariantAnimation,
        set_value: Callable[[object], None],
        start: float,
        target: float,
        duration: int,
        curve: QEasingCurve.Type,
    ) -> None:
        """Run ``animation`` from ``start`` to ``target``; when motion is reduced, set
        ``target`` at once instead."""
        animation.stop()
        if glass_preferences().reduce_motion:
            animation.setDuration(0)
            set_value(float(target))
            return
        animation.setDuration(duration)
        animation.setEasingCurve(curve)
        animation.setStartValue(start)
        animation.setEndValue(target)
        animation.start()

    def _animate_scale(self, target: float, duration: int, curve: QEasingCurve.Type) -> None:
        animation = self.press_animation
        self._animate(animation, self._set_scale, self._scale, target, duration, curve)

    def enterEvent(self, event: QEnterEvent) -> None:
        self._light_at = event.position()
        self._animate(
            self.hover_animation,
            self._set_hover,
            self._hover,
            1.0,
            HOVER_IN_MS,
            QEasingCurve.Type.OutBack,
        )
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._animate(
            self.hover_animation,
            self._set_hover,
            self._hover,
            0.0,
            HOVER_OUT_MS,
            QEasingCurve.Type.OutCubic,
        )
        super().leaveEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._light_at = event.position()
        self.update()
        super().mouseMoveEvent(event)

    def _pressed(self) -> None:
        self._animate_scale(PRESSED_SCALE, PRESS_MS, QEasingCurve.Type.OutQuad)

    def _released(self) -> None:
        self._animate_scale(1.0, RELEASE_MS, QEasingCurve.Type.OutBack)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() != QEvent.Type.EnabledChange:
            return
        if not self.isEnabled():
            # A disabled button gets no Leave or release: drop the hover and press now.
            self.hover_animation.stop()
            self.press_animation.stop()
            self._set_hover(0.0)
            self._set_scale(1.0)
        elif self.underMouse():
            self._animate(
                self.hover_animation,
                self._set_hover,
                self._hover,
                1.0,
                HOVER_IN_MS,
                QEasingCurve.Type.OutBack,
            )

    def _pill(self) -> QRectF:
        """The pill's rectangle: grown into its room as hovered, scaled about its centre
        while pressed."""
        rect = QRectF(self.rect()).adjusted(ROOM_X, ROOM_Y, -ROOM_X, -ROOM_Y)
        if self.isEnabled() and not glass_preferences().reduce_motion and self._hover:
            grow_x = min(GROW_X * self._hover, ROOM_X)
            grow_y = min(GROW_Y * self._hover, ROOM_Y)
            rect.adjust(-grow_x, -grow_y, grow_x, grow_y)
        if self._scale != 1.0:
            centre = rect.center()
            rect.setSize(rect.size() * self._scale)
            rect.moveCenter(centre)
        return rect

    @profiled("GlassButton")
    def paintEvent(self, event: QPaintEvent) -> None:
        glass, tokens, preferences = current_glass(), current_tokens(), glass_preferences()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self._pill()
        if not self.isEnabled():
            painter.setOpacity(DISABLED_OPACITY)
        hover = self._hover if self.isEnabled() else 0.0
        if glass.highlight.alpha > 0:
            paint_liquid_pill(
                painter,
                rect,
                glass,
                primary=self.primary,
                mode=preferences.mode,
                hover=hover,
            )
        else:
            paint_pill(
                painter, rect, glass, primary=self.primary, mode=preferences.mode, hover=hover
            )
        normal = preferences.mode is GlassMode.NORMAL
        light_at = self._light_at
        if normal and self.isEnabled() and self._hover > 0 and light_at is not None:
            paint_glow(painter, rect, glass, light_at, self._hover)
            paint_specular(painter, rect, glass, light_at, self._hover)
        painter.setOpacity(1.0)
        option = QStyleOptionButton()
        self.initStyleOption(option)
        if keyboard_focus(option.state):  # As elsewhere: not after a click.
            ring = QPainterPath()
            inset = FOCUS_WIDTH / 2
            inner = rect.adjusted(inset, inset, -inset, -inset)
            ring.addRoundedRect(inner, inner.height() / 2, inner.height() / 2)
            # The accent would vanish on a solid primary fill, so its ring takes the label's
            # colour, which is proven against the fill.
            ring_color = glass.primary_text if self.primary else tokens.accent_fg
            painter.setPen(QPen(QColor(ring_color), FOCUS_WIDTH))
            painter.drawPath(ring)
        if not self.isEnabled():
            text_color = tokens.text_muted
        else:
            text_color = glass.primary_text if self.primary else tokens.text
        self._paint_label(painter, rect, text_color)
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
