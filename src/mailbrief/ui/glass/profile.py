"""Paint timings for the glass widgets, on request.

With MAILBRIEF_PAINT_PROFILE=1 in the environment, every glass paint is timed, and at quit
desktop.log gets one line per widget kind: how many paints, and the median and 95th
percentile in milliseconds of the last KEPT of them. Nothing else is recorded. Without
it, a paint costs one check.
"""

import functools
import math
import os
import time
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Final, ParamSpec, TypeVar

from mailbrief.ui.diagnostics import logger

ENVIRONMENT: Final = "MAILBRIEF_PAINT_PROFILE"

P = ParamSpec("P")
R = TypeVar("R")

# The most recent paints kept per widget kind, so a long session's memory stays bounded;
# the percentiles describe these, and the count is every paint.
KEPT: Final = 10_000

_enabled = os.environ.get(ENVIRONMENT) == "1"
_samples: dict[str, deque[int]] = {}  # Nanoseconds per paint, by widget kind.
_counts: dict[str, int] = {}


@dataclass(frozen=True, slots=True)
class PaintStats:
    count: int
    p50_ms: float
    p95_ms: float


def set_enabled(enabled: bool) -> None:
    global _enabled
    _enabled = enabled


def enabled() -> bool:
    return _enabled


def clear() -> None:
    _samples.clear()
    _counts.clear()


def profiled(kind: str | None = None) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Time each call of a paint method while profiling is on, under ``kind`` or, without
    one, the class name of the object painting (so a header and a sidebar sharing a paint
    method report apart)."""

    def decorate(paint: Callable[P, R]) -> Callable[P, R]:
        @functools.wraps(paint)
        def timed(*args: P.args, **kwargs: P.kwargs) -> R:
            if not _enabled:
                return paint(*args, **kwargs)
            name = kind if kind is not None else type(args[0]).__name__
            start = time.perf_counter_ns()
            try:
                return paint(*args, **kwargs)
            finally:
                elapsed = time.perf_counter_ns() - start
                _samples.setdefault(name, deque(maxlen=KEPT)).append(elapsed)
                _counts[name] = _counts.get(name, 0) + 1

        return timed

    return decorate


def _percentile(ordered: Sequence[int], fraction: float) -> float:
    """The nearest-rank percentile of sorted nanoseconds, in milliseconds."""
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[rank - 1] / 1_000_000


def summary() -> dict[str, PaintStats]:
    stats = {}
    for kind, samples in sorted(_samples.items()):
        ordered = sorted(samples)
        stats[kind] = PaintStats(
            count=_counts.get(kind, len(ordered)),
            p50_ms=_percentile(ordered, 0.50),
            p95_ms=_percentile(ordered, 0.95),
        )
    return stats


def log_summary() -> None:
    """One desktop.log line per widget kind, if profiling was on."""
    if not _enabled:
        return
    for kind, stats in summary().items():
        logger.info(
            "paint widget=%s count=%d p50_ms=%.2f p95_ms=%.2f",
            kind,
            stats.count,
            stats.p50_ms,
            stats.p95_ms,
        )
