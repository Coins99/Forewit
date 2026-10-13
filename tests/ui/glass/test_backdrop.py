"""The glass backdrop: painted within the bounds the contrast proof assumes, blurred, and
rendered off the Qt thread by a renderer that caches and drops stale results."""

import threading

import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot
from shiboken6 import Shiboken

from mailbrief.ui.glass.backdrop import (
    CACHE_SIZE,
    SCALE,
    Backdrop,
    BackdropKey,
    BackdropRenderer,
    blur,
    paint_backdrop,
    render_backdrop,
)
from mailbrief.ui.theme import ColorScheme, ThemeMode, glass_for
from tests.ui.theme.test_glass import SETS, backdrop_extremes, luminance, rgb

# A smaller window than the gallery's keeps the pixel scans quick.
WIDTH, HEIGHT = 440, 288


def key(scheme: ColorScheme = ColorScheme.SLATE, mode: ThemeMode = ThemeMode.DARK) -> BackdropKey:
    return BackdropKey(WIDTH, HEIGHT, scheme, mode)


def pixels(image: QImage) -> list[tuple[float, float, float]]:
    found = []
    for y in range(image.height()):
        for x in range(image.width()):
            c = image.pixelColor(x, y)
            found.append((float(c.red()), float(c.green()), float(c.blue())))
    return found


def test_the_backdrop_is_painted_at_a_quarter_size(qapp: QApplication) -> None:
    image = paint_backdrop(key())
    assert (image.width(), image.height()) == (round(WIDTH * SCALE), round(HEIGHT * SCALE))
    assert render_backdrop(key()).blurred.size() == image.size()


@pytest.mark.parametrize(("scheme", "mode"), SETS)
def test_no_backdrop_pixel_leaves_the_proven_range(
    qapp: QApplication, scheme: ColorScheme, mode: ThemeMode
) -> None:
    """test_glass.py proves contrast over the darker stop and the lighter stop under the
    brightest streak; the painted and blurred backdrops stay between them."""
    darkest, brightest = (luminance(c) for c in backdrop_extremes(glass_for(mode, scheme)))
    tolerance = 0.003  # 8-bit rounding.
    sharp = paint_backdrop(key(scheme, mode))
    for image in (sharp, blur(sharp)):
        values = [luminance(c) for c in pixels(image)]
        assert min(values) >= darkest - tolerance, (scheme, mode)
        assert max(values) <= brightest + tolerance, (scheme, mode)
    glass = glass_for(mode, scheme)
    lighter = max(luminance(rgb(glass.backdrop_top)), luminance(rgb(glass.backdrop_bottom)))
    assert max(luminance(c) for c in pixels(sharp)) > lighter  # The streaks show.


def test_blurring_smooths_the_streaks(qapp: QApplication) -> None:
    def roughness(image: QImage) -> int:
        y = image.height() // 2
        values = [image.pixelColor(x, y).lightness() for x in range(image.width())]
        return max(abs(a - b) for a, b in zip(values, values[1:], strict=False))

    sharp = paint_backdrop(key())
    assert roughness(blur(sharp)) < roughness(sharp)


def test_a_backdrop_maps_window_areas_to_its_image(qapp: QApplication) -> None:
    backdrop = render_backdrop(key())
    source = backdrop.source(QRectF(40, 20, 100, 44))
    assert source == QRectF(40 * SCALE, 20 * SCALE, 100 * SCALE, 44 * SCALE)


class Recorder:
    """A render that notes its keys and whether it ran off the main thread; with a gate,
    each render waits for it."""

    def __init__(self) -> None:
        self.keys: list[BackdropKey] = []
        self.off_main_thread: list[bool] = []
        self.gate: threading.Event | None = None

    def __call__(self, wanted: BackdropKey) -> Backdrop:
        self.keys.append(wanted)
        self.off_main_thread.append(threading.current_thread() is not threading.main_thread())
        if self.gate is not None:
            assert self.gate.wait(timeout=5)
        return render_backdrop(wanted)


async def test_renders_run_off_the_qt_thread_and_are_cached(qtbot: QtBot) -> None:
    recorder = Recorder()
    renderer = BackdropRenderer(render=recorder)
    emitted: list[Backdrop] = []
    renderer.ready.connect(emitted.append)
    assert renderer.request(key()) is None
    await renderer.drain()
    [backdrop] = emitted
    assert backdrop.key == key()
    assert recorder.off_main_thread == [True]
    assert renderer.request(key()) is backdrop
    await renderer.drain()
    assert emitted == [backdrop]  # A cached backdrop is returned, never emitted again.
    assert recorder.keys == [key()]


async def test_requests_during_a_render_become_one_render_of_the_last(qtbot: QtBot) -> None:
    recorder = Recorder()
    recorder.gate = threading.Event()
    renderer = BackdropRenderer(render=recorder)
    emitted: list[BackdropKey] = []
    renderer.ready.connect(lambda backdrop: emitted.append(backdrop.key))
    first, middle, last = (
        BackdropKey(WIDTH + step, HEIGHT, ColorScheme.SLATE, ThemeMode.DARK) for step in range(3)
    )
    renderer.request(first)  # Starts, and waits at the gate.
    renderer.request(middle)
    renderer.request(last)
    recorder.gate.set()
    await renderer.drain()
    assert recorder.keys == [first, last]
    assert emitted == [last]  # The first finished after a newer request: dropped.
    assert renderer.request(first) is not None  # Still cached for a later request.


async def test_only_the_last_few_backdrops_are_kept(qtbot: QtBot) -> None:
    recorder = Recorder()
    renderer = BackdropRenderer(render=recorder)
    keys = [
        BackdropKey(WIDTH + step, HEIGHT, ColorScheme.SLATE, ThemeMode.DARK)
        for step in range(CACHE_SIZE + 1)
    ]
    for wanted in keys:
        renderer.request(wanted)
        await renderer.drain()
    assert renderer.request(keys[-1]) is not None
    assert renderer.request(keys[0]) is None  # Evicted, so rendered again.
    await renderer.drain()
    assert recorder.keys == [*keys, keys[0]]


async def test_draining_an_idle_renderer_returns_at_once() -> None:
    await BackdropRenderer().drain()


def test_without_a_running_loop_a_request_renders_at_once(qapp: QApplication) -> None:
    renderer = BackdropRenderer()
    backdrop = renderer.request(key())
    assert backdrop is not None and backdrop.key == key()
    assert renderer.request(key()) is backdrop


async def test_results_arrive_through_the_qt_event_loop(qtbot: QtBot) -> None:
    """Without drain: the worker hands the result to the Qt thread by a queued signal,
    which any running event loop delivers."""
    renderer = BackdropRenderer()
    with qtbot.waitSignal(renderer.ready, timeout=5000) as blocker:
        assert renderer.request(key()) is None
    assert blocker.args[0].key == key()
    await renderer.drain()  # Nothing left to finish.


async def test_a_failed_render_is_retried_by_a_later_request(qtbot: QtBot) -> None:
    attempts: list[BackdropKey] = []

    def failing(wanted: BackdropKey) -> Backdrop:
        attempts.append(wanted)
        if len(attempts) == 1:
            raise RuntimeError("render failed")
        return render_backdrop(wanted)

    renderer = BackdropRenderer(render=failing)
    emitted: list[Backdrop] = []
    renderer.ready.connect(emitted.append)
    renderer.request(key())
    await renderer.drain()
    assert emitted == []
    assert renderer.request(key()) is None  # Not cached: rendered again.
    await renderer.drain()
    assert [backdrop.key for backdrop in emitted] == [key()]


async def test_a_renderer_deleted_mid_render_fails_quietly(
    qtbot: QtBot, caplog: pytest.LogCaptureFixture
) -> None:
    """Its window can close while the worker still renders; handing back the result must
    not raise on the worker thread."""
    recorder = Recorder()
    recorder.gate = threading.Event()
    renderer = BackdropRenderer(render=recorder)
    renderer.request(key())
    job = renderer._job
    assert job is not None
    Shiboken.delete(renderer)
    recorder.gate.set()
    job[1].result(timeout=5)
    qtbot.wait(50)  # Let the worker's done callback run.
    assert not [r for r in caplog.records if r.name == "concurrent.futures"]
