"""Half-open spans of time, ``[start, end)`` in aware UTC: unions, clipping and lengths.

Calendar arithmetic goes through these, so that overlapping entries never count twice and
an entry ending at midnight never touches the next day (roadmap §5.2–5.3).
"""

from collections.abc import Iterable
from datetime import datetime

type Interval = tuple[datetime, datetime]


def union(intervals: Iterable[Interval]) -> tuple[Interval, ...]:
    """The intervals merged where they overlap or touch, in order; empty ones dropped."""
    merged: list[Interval] = []
    for start, end in sorted(span for span in intervals if span[0] < span[1]):
        if merged and start <= merged[-1][1]:
            last_start, last_end = merged[-1]
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))
    return tuple(merged)


def clip(intervals: Iterable[Interval], start: datetime, end: datetime) -> tuple[Interval, ...]:
    """Each interval's part inside ``[start, end)``, in the given order; empty parts dropped."""
    found: list[Interval] = []
    for left, right in intervals:
        left, right = max(left, start), min(right, end)
        if left < right:
            found.append((left, right))
    return tuple(found)


def subtract(intervals: Iterable[Interval], cuts: Iterable[Interval]) -> tuple[Interval, ...]:
    """The time the intervals cover and the cuts don't, merged and in order: overlapping
    cuts take their time once."""
    left: list[Interval] = []
    removed = union(cuts)
    for start, end in union(intervals):
        for cut_start, cut_end in removed:
            if cut_end <= start or cut_start >= end:
                continue
            if cut_start > start:
                left.append((start, cut_start))
            start = max(start, cut_end)
            if start >= end:
                break
        if start < end:
            left.append((start, end))
    return tuple(left)


def minutes(intervals: Iterable[Interval]) -> float:
    """The minutes the intervals cover together, each instant counted once."""
    return sum((end - start).total_seconds() for start, end in union(intervals)) / 60


def total_minutes(intervals: Iterable[Interval]) -> float:
    """The intervals' lengths added up, so that overlaps count once for each interval."""
    return sum(max(0.0, (end - start).total_seconds()) for start, end in intervals) / 60


def overlaps(first: Interval, second: Interval) -> bool:
    """Whether the two share any instant; touching ends don't."""
    return first[0] < second[1] and second[0] < first[1]
