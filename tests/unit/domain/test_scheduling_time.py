"""P3 time contracts use real instants, never guessed folds or 24-hour local days."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from mailbrief.domain.planning_intervals import UtcInterval, occupied_duration
from mailbrief.domain.scheduling_time import (
    AmbiguousWallTimeError,
    NonexistentWallTimeError,
    WallTimeChoice,
    all_day_intervals,
    planning_dates,
    resolve_wall_time,
    wall_time_options,
    working_window_intervals,
)

TORONTO = ZoneInfo("America/Toronto")


def test_ordinary_wall_time_has_one_utc_choice_without_changing_existing_event_rules() -> None:
    assert wall_time_options(date(2026, 11, 5), time(9), TORONTO) == (
        datetime(2026, 11, 5, 14, tzinfo=UTC),
    )
    assert resolve_wall_time(date(2026, 11, 5), time(9), TORONTO) == datetime(
        2026, 11, 5, 14, tzinfo=UTC
    )
    with pytest.raises(ValueError, match="without a time zone"):
        wall_time_options(date(2026, 11, 5), time(9, tzinfo=UTC), TORONTO)


def test_skipped_input_requires_correction_instead_of_silent_normalization() -> None:
    assert wall_time_options(date(2026, 3, 8), time(2, 30), TORONTO) == ()
    with pytest.raises(NonexistentWallTimeError, match="does not occur"):
        resolve_wall_time(date(2026, 3, 8), time(2, 30), TORONTO)


def test_repeated_input_requires_an_explicit_earlier_or_later_choice() -> None:
    day, clock = date(2026, 11, 1), time(1, 30)
    earlier, later = wall_time_options(day, clock, TORONTO)
    assert earlier == datetime(2026, 11, 1, 5, 30, tzinfo=UTC)
    assert later == earlier + timedelta(hours=1)
    with pytest.raises(AmbiguousWallTimeError, match="choose earlier or later"):
        resolve_wall_time(day, clock, TORONTO)
    assert resolve_wall_time(day, clock, TORONTO, choice=WallTimeChoice.EARLIER) == earlier
    assert resolve_wall_time(day, clock, TORONTO, choice=WallTimeChoice.LATER) == later
    # A time.fold default, or a pasted fold=1, is not explicit input choice.
    with pytest.raises(AmbiguousWallTimeError):
        resolve_wall_time(day, clock.replace(fold=1), TORONTO)


def test_repeated_partial_window_does_not_bridge_unconfigured_wall_minutes() -> None:
    intervals = working_window_intervals(date(2026, 11, 1), 75, 105, TORONTO)
    assert intervals == (
        UtcInterval(
            start=datetime(2026, 11, 1, 5, 15, tzinfo=UTC),
            end=datetime(2026, 11, 1, 5, 45, tzinfo=UTC),
        ),
        UtcInterval(
            start=datetime(2026, 11, 1, 6, 15, tzinfo=UTC),
            end=datetime(2026, 11, 1, 6, 45, tzinfo=UTC),
        ),
    )
    assert occupied_duration(intervals) == timedelta(hours=1)


@pytest.mark.parametrize(
    ("day", "start", "end", "minutes"),
    [
        (date(2026, 3, 8), 120, 180, 0),
        (date(2026, 3, 8), 90, 210, 60),
        (date(2026, 3, 8), 0, 1440, 23 * 60),
        (date(2026, 11, 1), 0, 1440, 25 * 60),
        (date(2026, 11, 5), 540, 1020, 8 * 60),
    ],
)
def test_working_capacity_counts_only_real_elapsed_time(
    day: date, start: int, end: int, minutes: int
) -> None:
    assert occupied_duration(working_window_intervals(day, start, end, TORONTO)) == timedelta(
        minutes=minutes
    )


def test_half_hour_transition_and_historical_second_precision() -> None:
    # Lord Howe repeats only half an hour, not Toronto's one hour.
    intervals = working_window_intervals(
        date(2026, 4, 5), 100, 110, ZoneInfo("Australia/Lord_Howe")
    )
    assert len(intervals) == 2
    assert occupied_duration(intervals) == timedelta(minutes=20)
    # Liberia's 1972 jump occurs at 00:44:30, rather than a minute boundary.
    intervals = working_window_intervals(date(1972, 1, 7), 0, 60, ZoneInfo("Africa/Monrovia"))
    assert occupied_duration(intervals) == timedelta(minutes=15, seconds=30)


def test_midnight_transition_and_an_entire_skipped_day() -> None:
    intervals = all_day_intervals(
        date(2018, 11, 4), date(2018, 11, 4), ZoneInfo("America/Sao_Paulo")
    )
    assert occupied_duration(intervals) == timedelta(hours=23)
    assert all_day_intervals(date(2011, 12, 30), date(2011, 12, 30), ZoneInfo("Pacific/Apia")) == ()
    assert wall_time_options(date(2011, 12, 30), time(12), ZoneInfo("Pacific/Apia")) == ()


def test_all_day_dates_remain_inclusive_with_exclusive_instant_end() -> None:
    assert all_day_intervals(date(2026, 11, 5), date(2026, 11, 6), TORONTO) == (
        UtcInterval(
            start=datetime(2026, 11, 5, 5, tzinfo=UTC),
            end=datetime(2026, 11, 7, 5, tzinfo=UTC),
        ),
    )


@pytest.mark.parametrize(("start", "end"), [(-1, 60), (60, 60), (60, 0), (0, 1441)])
def test_invalid_window_minutes_are_rejected(start: int, end: int) -> None:
    with pytest.raises(ValueError, match="increasing minutes"):
        working_window_intervals(date(2026, 11, 5), start, end, TORONTO)


def test_unsupported_date_edge_has_a_static_error() -> None:
    for day in (date.min, date.max):
        with pytest.raises(ValueError, match="supported time range"):
            working_window_intervals(day, 0, 1440, TORONTO)


def test_range_limit_is_per_command_and_checks_reversed_ranges() -> None:
    first = date(2026, 11, 5)
    assert len(planning_dates(first, first + timedelta(days=41))) == 42
    for last in (first - timedelta(days=1), first + timedelta(days=42)):
        with pytest.raises(ValueError, match="one and 42"):
            planning_dates(first, last)
