"""The backdrop behind Liquid Glass: a gradient with diagonal light, painted from the glass
tokens, and the blurred copy the glass shows.

Only the blurred copy is ever seen, so it is painted at a quarter of the window's size and
blurred there; glass scales it up. Painting a QImage is allowed off the Qt thread, and
`BackdropRenderer` does it there.
"""

import asyncio
import contextlib
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Final

from PySide6.QtCore import QObject, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter

from mailbrief.ui.diagnostics import log_failure
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


# One worker for every renderer: renders are short and only the latest matters.
_WORKER: Final = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mailbrief-backdrop")


class _Relay(QObject):
    """Carries a finished render from the worker thread to the Qt thread (queued)."""

    finished = Signal(object, object)  # BackdropKey, Future[Backdrop]


class BackdropRenderer(QObject):
    """Renders backdrops off the Qt thread, one at a time, and keeps the last few.

    ``request`` returns a cached backdrop at once, and with no asyncio loop running it
    renders one at once. Otherwise it renders in a worker thread and ``ready`` emits it,
    unless a newer request came first: requests made during a render (a window being
    resized) become one render of the last of them.

    Results come back through a queued Qt signal, not an asyncio task, so they arrive under
    any event loop. ``drain`` finishes the render in progress at shutdown.
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
        self._job: tuple[BackdropKey, Future[Backdrop]] | None = None
        self._relay = _Relay(self)
        self._relay.finished.connect(self._finished)

    def request(self, key: BackdropKey) -> Backdrop | None:
        self._latest = key
        cached = self._cache.get(key)
        if cached is not None:
            self._cache.move_to_end(key)
            return cached
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            # No loop yet (before the app's loop starts, or in a plain script): a
            # quarter-size render is quick enough to do now.
            backdrop = self._render(key)
            self._keep(backdrop)
            return backdrop
        if self._job is None:
            self._start(key)
        return None

    def _start(self, key: BackdropKey) -> None:
        future = _WORKER.submit(self._render, key)
        self._job = (key, future)
        relay = self._relay

        def deliver(done: Future[Backdrop]) -> None:
            # On the worker thread; the renderer may have been deleted with its window.
            with contextlib.suppress(RuntimeError):
                relay.finished.emit(key, done)

        future.add_done_callback(deliver)

    def _keep(self, backdrop: Backdrop) -> None:
        self._cache[backdrop.key] = backdrop
        while len(self._cache) > CACHE_SIZE:
            self._cache.popitem(last=False)

    def _finished(self, key: BackdropKey, future: Future[Backdrop]) -> None:
        if self._job is None or self._job[1] is not future:
            return  # Already handled by drain.
        self._job = None
        try:
            backdrop = future.result()
        except Exception as exc:
            log_failure(exc)  # The glass keeps its opaque underlay; a later request retries.
        else:
            self._keep(backdrop)
            if key == self._latest:
                self.ready.emit(backdrop)
        latest = self._latest
        if latest is not None and latest not in self._cache and latest != key:
            self._start(latest)

    async def drain(self) -> None:
        """Finish the renders in progress, for shutdown; each takes milliseconds."""
        while self._job is not None:
            key, future = self._job
            wait([future])
            self._finished(key, future)
