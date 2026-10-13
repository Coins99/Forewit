"""Paint profiling: off unless asked for, timings by widget kind, counts and percentiles
only in desktop.log, and the real glass widgets painting well inside a frame."""

import logging
from collections import deque
from collections.abc import Iterator

import pytest
from PySide6.QtCore import QPointF
from PySide6.QtGui import QEnterEvent
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from mailbrief.ui.diagnostics import logger
from mailbrief.ui.glass import profile
from mailbrief.ui.glass.button import GlassButton
from mailbrief.ui.labels import outline_button
from mailbrief.ui.theme import apply_theme
from tests.ui.test_workspace import build
from tests.ui.window_wait import show

# Far above the 4 ms these paints take on a laptop, so a slow CI machine passes, yet far
# below what a regression such as blurring on every paint would cost.
BUDGET_MS = 50.0


@pytest.fixture(autouse=True)
def profiling_off() -> Iterator[None]:
    profile.clear()
    yield
    profile.set_enabled(False)
    profile.clear()


class Painter:
    @profile.profiled("Named")
    def paint_named(self, value: int) -> int:
        return value + 1

    @profile.profiled()
    def paint_by_class(self) -> None:
        pass


class Header(Painter):
    pass


class Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def test_paints_are_timed_only_while_profiling_is_on() -> None:
    painter = Painter()
    assert painter.paint_named(1) == 2
    assert profile.summary() == {}
    profile.set_enabled(True)
    assert profile.enabled()
    assert painter.paint_named(1) == 2
    Header().paint_by_class()
    Painter().paint_by_class()
    stats = profile.summary()
    assert set(stats) == {"Named", "Header", "Painter"}
    assert stats["Named"].count == 1


def test_percentiles_are_nearest_rank(monkeypatch: pytest.MonkeyPatch) -> None:
    samples = {"Kind": deque(ms * 1_000_000 for ms in range(1, 101))}  # 1 to 100 ms.
    monkeypatch.setattr(profile, "_samples", samples)
    stats = profile.summary()["Kind"]
    assert (stats.count, stats.p50_ms, stats.p95_ms) == (100, 50.0, 95.0)
    samples["One"] = deque([2_500_000])
    assert profile.summary()["One"] == profile.PaintStats(1, 2.5, 2.5)


def test_the_log_gets_counts_and_times_only() -> None:
    records = Records()
    logger.addHandler(records)
    level = logger.level
    logger.setLevel(logging.INFO)
    try:
        Painter().paint_named(1)
        profile.log_summary()  # Off: nothing.
        assert records.lines == []
        profile.set_enabled(True)
        Painter().paint_named(1)
        Header().paint_by_class()
        profile.log_summary()
    finally:
        logger.removeHandler(records)
        logger.setLevel(level)
    assert [line.split(" count=")[0] for line in records.lines] == [
        "paint widget=Header",
        "paint widget=Named",
    ]
    for line in records.lines:
        words = line.split()
        assert [word.split("=")[0] for word in words[1:]] == ["widget", "count", "p50_ms", "p95_ms"]


def test_the_glass_chrome_and_pills_paint_well_inside_a_frame(
    qtbot: QtBot, qapp: QApplication, themed: None
) -> None:
    apply_theme(qapp)
    workspace = build(qtbot)
    pill = outline_button("&Sync and review", "generateButton", mnemonic=True, glass=True)
    assert isinstance(pill, GlassButton)
    workspace.header.add_widget(pill)
    workspace.resize(1100, 720)
    show(qtbot, workspace)
    profile.set_enabled(True)
    for _ in range(20):
        workspace.repaint()
        point = QPointF(10, pill.height() / 2)
        pill.enterEvent(QEnterEvent(point, point, point))  # Hovered: lifted, rim lit.
        pill.repaint()
    stats = profile.summary()
    for kind in ("HeaderBar", "SidebarNav", "GlassButton", "SidebarPage", "SidebarButton"):
        assert stats[kind].count >= 20, kind
        assert stats[kind].p95_ms < BUDGET_MS, (kind, stats[kind])


def test_a_long_session_keeps_a_bounded_sample(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the last KEPT timings per kind are kept, so memory stays flat; the count is
    still every paint."""
    monkeypatch.setattr(profile, "KEPT", 5)
    profile.set_enabled(True)
    painter = Painter()
    for value in range(12):
        painter.paint_named(value)
    stats = profile.summary()["Named"]
    assert stats.count == 12
    assert len(profile._samples["Named"]) == 5
