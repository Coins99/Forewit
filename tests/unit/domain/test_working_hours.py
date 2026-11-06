"""Working hours (ADR 0029): up to three quarter-hour windows a weekday, and a buffer."""

from typing import Any

import pytest
from pydantic import ValidationError

from mailbrief.domain.working_hours import (
    SUGGESTED_HOURS,
    WEEKDAYS,
    HoursEdit,
    WorkingHours,
    WorkingWindow,
)


def window(start: int, end: int) -> WorkingWindow:
    return WorkingWindow(start_minute=start, end_minute=end)


def week(**days: Any) -> tuple[tuple[WorkingWindow, ...], ...]:
    found: list[tuple[WorkingWindow, ...]] = [()] * WEEKDAYS
    for name, windows in days.items():
        found[("mon", "tue", "wed", "thu", "fri", "sat", "sun").index(name)] = tuple(windows)
    return tuple(found)


def test_windows_are_quarter_hours_of_one_day() -> None:
    assert window(9 * 60, 17 * 60).minutes == 480
    assert window(0, 1440).minutes == 1440  # Midnight to midnight.
    for start, end in ((540, 540), (600, 540), (545, 600), (540, 610), (-15, 60), (0, 1455)):
        with pytest.raises(ValidationError):
            window(start, end)


def test_a_day_keeps_its_windows_in_order_and_refuses_overlaps() -> None:
    hours = HoursEdit(days=week(mon=[window(780, 1020), window(540, 720)]))
    assert hours.windows(0) == (window(540, 720), window(780, 1020))
    touching = HoursEdit(days=week(tue=[window(540, 720), window(720, 900)]))
    assert len(touching.windows(1)) == 2
    with pytest.raises(ValidationError, match="overlap"):
        HoursEdit(days=week(mon=[window(540, 720), window(690, 900)]))
    with pytest.raises(ValidationError, match="at most 3"):
        HoursEdit(
            days=week(mon=[window(0, 60), window(120, 180), window(240, 300), window(360, 420)])
        )


def test_the_week_has_seven_days_and_the_buffer_is_a_choice() -> None:
    with pytest.raises(ValidationError, match="each day"):
        HoursEdit(days=((),) * 6)
    for buffer in (0, 5, 10, 15, 30):
        assert HoursEdit(buffer_minutes=buffer).buffer_minutes == buffer
    for buffer in (-5, 3, 20, 45):
        with pytest.raises(ValidationError, match="buffer"):
            HoursEdit(buffer_minutes=buffer)


def test_unset_hours_and_the_suggestion() -> None:
    unset = WorkingHours.unset()
    assert unset.revision == 0 and not unset.is_set and unset.weekly_minutes == 0
    assert SUGGESTED_HOURS.is_set and SUGGESTED_HOURS.weekly_minutes == 5 * 480
    assert SUGGESTED_HOURS.windows(5) == () and SUGGESTED_HOURS.windows(6) == ()
    saved = WorkingHours(days=SUGGESTED_HOURS.days, buffer_minutes=10, revision=3)
    assert saved.edit() == HoursEdit(days=SUGGESTED_HOURS.days, buffer_minutes=10)
    # Saved hours with every day empty are hours that aren't set.
    assert not WorkingHours(revision=2).is_set
