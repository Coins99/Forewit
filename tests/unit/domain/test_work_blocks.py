"""Work block times, days, scheduled time and a new block's length (ADR 0028)."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from mailbrief.domain.actions import ActionStatus
from mailbrief.domain.common import known_zone
from mailbrief.domain.events import ScheduledBlock
from mailbrief.domain.work_blocks import (
    BlockTime,
    WorkBlock,
    check_block_time,
    scheduled_time,
    suggested_minutes,
)

TORONTO = ZoneInfo("America/Toronto")
ZONE = "America/Toronto"
NOW = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), TORONTO)


def block(start: datetime, end: datetime, number: int = 1) -> WorkBlock:
    return WorkBlock(
        public_id=f"00000000-0000-4000-8000-{number:012d}",
        action_public_id="00000000-0000-4000-8000-000000000999",
        starts_at_utc=start,
        ends_at_utc=end,
        time_zone=ZONE,
        revision=1,
        created_at_utc=NOW,
        updated_at_utc=NOW,
    )


def test_a_block_lasts_five_minutes_to_twelve_hours() -> None:
    day = date(2026, 11, 2)
    check_block_time(at(day, 9), at(day, 9, 5))
    check_block_time(at(day, 9), at(day, 21))
    with pytest.raises(ValueError, match="at least 5 minutes"):
        check_block_time(at(day, 9), at(day, 9, 4))
    with pytest.raises(ValueError, match="at most 12 hours"):
        check_block_time(at(day, 9), at(day, 21, 1))
    with pytest.raises(ValidationError):
        BlockTime(starts_at_utc=at(day, 9), ends_at_utc=at(day, 9), time_zone=ZONE)


def test_block_times_are_aware_utc_in_a_known_zone() -> None:
    day = date(2026, 11, 2)
    time_ = BlockTime(starts_at_utc=at(day, 9), ends_at_utc=at(day, 10), time_zone=ZONE)
    assert time_.starts_at_utc.tzinfo is UTC
    assert time_.minutes == 60
    with pytest.raises(ValidationError):
        BlockTime(
            starts_at_utc=datetime(2026, 11, 2, 9),
            ends_at_utc=at(day, 10),
            time_zone=ZONE,
        )
    with pytest.raises(ValidationError):
        BlockTime(starts_at_utc=at(day, 9), ends_at_utc=at(day, 10), time_zone="Mars/Base")
    with pytest.raises(ValueError, match="unknown"):
        known_zone("Mars/Base")


def test_days_ending_at_midnight_and_across_a_dst_change() -> None:
    monday = date(2026, 11, 2)
    tuesday = monday + timedelta(days=1)
    assert block(at(monday, 22), at(tuesday, 0)).days(TORONTO) == (monday, monday)
    assert block(at(monday, 23), at(tuesday, 1)).days(TORONTO) == (monday, tuesday)
    # Toronto springs forward at 02:00 on 8 March 2026: 01:30 to 03:30 is one hour.
    spring = date(2026, 3, 8)
    start = datetime(2026, 3, 8, 6, 30, tzinfo=UTC)  # 01:30 EST.
    across = block(start, start + timedelta(hours=1))
    assert across.days(TORONTO) == (spring, spring)
    assert across.minutes == 60
    assert across.ends_at_utc.astimezone(TORONTO).hour == 3


def test_a_stored_block_must_end_after_it_starts() -> None:
    day = date(2026, 11, 2)
    with pytest.raises(ValidationError):
        block(at(day, 10), at(day, 10))


def test_scheduled_time_counts_overlaps_once_in_the_unique_total() -> None:
    day = date(2026, 11, 2)
    found = [block(at(day, 9), at(day, 11), 1), block(at(day, 10), at(day, 12), 2)]
    whole = scheduled_time(found)
    assert (whole.blocks, whole.total_minutes, whole.unique_minutes) == (2, 240, 180)
    clipped = scheduled_time(found, at(day, 11), at(day, 13))
    assert (clipped.blocks, clipped.total_minutes, clipped.unique_minutes) == (1, 60, 60)
    assert scheduled_time([]).blocks == 0


@pytest.mark.parametrize(
    ("estimate", "scheduled", "expected"),
    [
        (None, 0, 60),
        (120, 0, 120),
        (300, 0, 120),
        (100, 0, 105),
        (90, 80, 15),
        (90, 90, 60),
        (90, 200, 60),
    ],
)
def test_a_new_block_starts_with_what_is_left_of_the_estimate(
    estimate: int | None, scheduled: int, expected: int
) -> None:
    assert suggested_minutes(estimate, scheduled) == expected


def test_a_scheduled_block_shows_its_task() -> None:
    day = date(2026, 11, 2)
    found = block(at(day, 9), at(day, 10))
    shown = ScheduledBlock(block=found, action_title="Draft", action_status=ActionStatus.SKIPPED)
    assert (shown.public_id, shown.action_public_id, shown.title) == (
        found.public_id,
        found.action_public_id,
        "Draft",
    )
    assert (shown.starts_at_utc, shown.ends_at_utc) == found.interval
    assert not shown.open and shown.days(TORONTO) == (day, day)
    assert shown.sort_key()[0] == found.starts_at_utc
    assert found.time.minutes == found.minutes == 60
    assert found.sort_key() == (found.starts_at_utc, found.ends_at_utc, found.public_id)
