"""The backdrop behind Liquid Glass: a gradient with diagonal light, painted from the glass
tokens, and the blurred copy the glass shows.

Only the blurred copy is ever seen, so it is painted at a quarter of the window's size and
blurred there; glass scales it up. Painting a QImage is allowed off the Qt thread, and
`BackdropRenderer` does it there.
"""

import asyncio
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from PySide6.QtCore import QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter

from mailbrief.ui.theme import ColorScheme, GlassTokens, ThemeMode, Tint, glass_for

# The backdrop is painted at this fraction of the window's logical size.
SCALE: Final = 0.25
# Each halving doubles the blur; three at a quarter size match five at full size.
BLUR_STEPS: Final = 3
# Streaks are laid out on a 1100 px wide reference window and stretch with the width:
# (left edge, width, strength), where strength scales the streak token's peak alpha.
REFERENCE_WIDTH: Final = 1100
STREAKS: Final = (
    (18, 46, 1.0),
    (78, 30, 0.75),
    (330, 70, 0.85),
    (690, 110, 0.7),
    (980, 60, 0.85),
)
STREAK_ANGLE: Final = -14  # Degrees from vertical, leaning right as they rise.
CACHE_SIZE: Final = 4


@dataclass(frozen=True, slots=True)
class BackdropKey:
    """What a backdrop depends on: the window's logical size and the theme."""

    width: int
    height: int
    scheme: ColorScheme
    mode: ThemeMode


@dataclass(frozen=True, slots=True)
class Backdrop:
    key: BackdropKey
    blurred: QImage  # At SCALE of the window's logical size.

    def source(self, area: QRectF) -> QRectF:
        """The part of ``blurred`` behind ``area``, in the window's logical coordinates."""
        x = self.blurred.width() / max(self.key.width, 1)
        y = self.blurred.height() / max(self.key.height, 1)
        return QRectF(area.x() * x, area.y() * y, area.width() * x, area.height() * y)


def color(tint: Tint, strength: float = 1.0) -> QColor:
    result = QColor(tint.color)
    result.setAlphaF(tint.alpha * strength)
    return result


def paint_backdrop(key: BackdropKey) -> QImage:
    """The sharp backdrop at SCALE: a diagonal gradient between the two backdrop stops,
    under streaks of light no brighter than the streak token's alpha."""
    glass = glass_for(key.mode, key.scheme)
    width = max(1, round(key.width * SCALE))
    height = max(1, round(key.height * SCALE))
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    base = QLinearGradient(0, 0, width * 0.55, height)
    base.setColorAt(0.0, QColor(glass.backdrop_top))
    base.setColorAt(1.0, QColor(glass.backdrop_bottom))
    painter.fillRect(image.rect(), base)
    _paint_streaks(painter, glass, width, height)
    painter.end()
    return image


def _paint_streaks(painter: QPainter, glass: GlassTokens, width: int, height: int) -> None:
    stretch = width / (REFERENCE_WIDTH * SCALE)
    for left, breadth, strength in STREAKS:
        band = breadth * SCALE
        painter.save()
        painter.translate(left * SCALE * stretch, -height * 0.1)
        painter.rotate(STREAK_ANGLE)
        light = QLinearGradient(0, 0, band, 0)
        light.setColorAt(0.0, color(glass.streak, 0.0))
        light.setColorAt(0.5, color(glass.streak, strength))
        light.setColorAt(1.0, color(glass.streak, 0.0))
        painter.fillRect(QRectF(0, 0, band, height * 1.8), light)
        painter.restore()


def blur(image: QImage, steps: int = BLUR_STEPS) -> QImage:
    """A wide, soft blur: halve the image ``steps`` times, then scale it back up, smoothly
    each time. Only averages pixels, so it never leaves the range the image had."""
    sizes: list[QSize] = []
    small = image
    for _ in range(steps):
        sizes.append(small.size())
        small = small.scaled(
            max(1, small.width() // 2),
            max(1, small.height() // 2),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    for size in reversed(sizes):
        small = small.scaled(
            size, Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation
        )
    return small


def render_backdrop(key: BackdropKey) -> Backdrop:
    """Paint and blur; safe off the Qt thread (QImage only, no pixmaps or widgets)."""
    return Backdrop(key=key, blurred=blur(paint_backdrop(key)))


class BackdropRenderer(QObject):
    """Renders backdrops off the Qt thread, one at a time, and keeps the last few.

    ``request`` returns a cached backdrop at once. Otherwise it renders in a worker
    thread and ``ready`` emits the backdrop, unless a newer request came first: requests
    made during a render (a window being resized) become one render of the last of them.
    """

    ready = Signal(object)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        render: Callable[[BackdropKey], Backdrop] = render_backdrop,
    ) -> None:
        super().__init__(parent)
        self._render = render
        self._cache: OrderedDict[BackdropKey, Backdrop] = OrderedDict()
        self._latest: BackdropKey | None = None
        self._task: asyncio.Task[None] | None = None

    def request(self, key: BackdropKey) -> Backdrop | None:
        self._latest = key
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())
        return None

    async def _run(self) -> None:
        while self._latest is not None and self._latest not in self._cache:
            key = self._latest
            backdrop = await asyncio.to_thread(self._render, key)
            self._cache[key] = backdrop
            while len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
            if key == self._latest:
                self.ready.emit(backdrop)

    async def drain(self) -> None:
        """Wait for the render in progress, for shutdown."""
        if self._task is not None:
            await self._task
