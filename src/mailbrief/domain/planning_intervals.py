"""Exact half-open UTC interval algebra shared by calendar read models (ADR 0028)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Self

from pydantic import ConfigDict, field_validator, model_validator

from mailbrief.domain.common import DomainModel, normalize_utc


class UtcInterval(DomainModel):
    """A positive elapsed interval; adjacent endpoints never overlap."""

    model_config = ConfigDict(hide_input_in_errors=True)

    start: datetime
    end: datetime

    @field_validator("start", "end")
    @classmethod
    def _utc(cls, value: datetime) -> datetime:
        return normalize_utc(value)

    @model_validator(mode="after")
    def _positive(self) -> Self:
        if self.end <= self.start:
            raise ValueError("an interval must end after it starts")
        return self

    @property
    def duration(self) -> timedelta:
        return self.end - self.start

    def overlaps(self, other: UtcInterval) -> bool:
        return self.start < other.end and other.start < self.end

    def intersection(self, other: UtcInterval) -> UtcInterval | None:
        start, end = max(self.start, other.start), min(self.end, other.end)
        return UtcInterval(start=start, end=end) if start < end else None


def union_intervals(intervals: Iterable[UtcInterval]) -> tuple[UtcInterval, ...]:
    """Return sorted disjoint spans, merging duplicates, overlaps and adjacency."""
    result: list[UtcInterval] = []
    for interval in sorted(intervals, key=lambda value: (value.start, value.end)):
        if result and interval.start <= result[-1].end:
            previous = result[-1]
            result[-1] = UtcInterval(start=previous.start, end=max(previous.end, interval.end))
        else:
            result.append(interval)
    return tuple(result)


def clip_intervals(
    intervals: Iterable[UtcInterval], bounds: UtcInterval
) -> tuple[UtcInterval, ...]:
    """Clip before totaling; intervals touching only a boundary contribute nothing."""
    return union_intervals(
        clipped for interval in intervals if (clipped := interval.intersection(bounds)) is not None
    )


def subtract_intervals(
    windows: Iterable[UtcInterval], busy: Iterable[UtcInterval]
) -> tuple[UtcInterval, ...]:
    """Subtract unioned occupancy once, retaining exact endpoint precision."""
    obstacles = union_intervals(busy)
    result: list[UtcInterval] = []
    obstacle_index = 0
    for window in union_intervals(windows):
        cursor = window.start
        while obstacle_index < len(obstacles) and obstacles[obstacle_index].end <= cursor:
            obstacle_index += 1
        index = obstacle_index
        while index < len(obstacles) and obstacles[index].start < window.end:
            obstacle = obstacles[index]
            if obstacle.start > cursor:
                result.append(UtcInterval(start=cursor, end=obstacle.start))
            cursor = max(cursor, obstacle.end)
            if cursor >= window.end:
                break
            index += 1
        if cursor < window.end:
            result.append(UtcInterval(start=cursor, end=window.end))
    return tuple(result)


def occupied_duration(intervals: Iterable[UtcInterval]) -> timedelta:
    """Unique occupied time, not summed bookings or measured actual work."""
    return sum((interval.duration for interval in union_intervals(intervals)), timedelta())
