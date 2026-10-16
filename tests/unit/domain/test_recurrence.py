"""The dates a repeating task's rule gives, at month ends, leap years and its end date."""

from datetime import date

import pytest
from pydantic import ValidationError

from mailbrief.domain.recurrence import (
    ALL_WEEKDAYS,
    WORKING_WEEKDAYS,
    RepeatEdit,
    RepeatFrequency,
    RepeatRule,
    mask_weekdays,
    next_occurrence,
    weekday_mask,
)

DAILY = RepeatRule(frequency=RepeatFrequency.DAILY)
MONDAY_WEDNESDAY = RepeatRule(frequency=RepeatFrequency.WEEKLY, weekdays=(2, 0))
DAY_31 = RepeatRule(frequency=RepeatFrequency.MONTHLY_DAY, month_day=31)
LAST_DAY = RepeatRule(frequency=RepeatFrequency.MONTHLY_LAST)


def dates(rule: RepeatRule, start: date, count: int) -> list[date]:
    found: list[date] = []
    day: date | None = rule.first_on_or_after(start)
    while day is not None and len(found) < count:
        found.append(day)
        day = rule.next_after(day)
    return found


def test_stored_values_are_stable() -> None:
    assert [item.value for item in RepeatFrequency] == [
        "daily",
        "weekly",
        "monthly_day",
        "monthly_last",
    ]
    assert weekday_mask(WORKING_WEEKDAYS) == 0b0011111
    assert weekday_mask(ALL_WEEKDAYS) == 127
    assert mask_weekdays(0b1000001) == (0, 6)


def test_daily_and_weekly_dates() -> None:
    assert dates(DAILY, date(2026, 12, 30), 3) == [
        date(2026, 12, 30),
        date(2026, 12, 31),
        date(2027, 1, 1),
    ]
    assert MONDAY_WEDNESDAY.weekdays == (0, 2)  # Sorted, Monday first.
    # Thursday 15 October 2026: the next Monday, Wednesday, then Monday again.
    assert dates(MONDAY_WEDNESDAY, date(2026, 10, 15), 3) == [
        date(2026, 10, 19),
        date(2026, 10, 21),
        date(2026, 10, 26),
    ]
    sunday = RepeatRule(frequency=RepeatFrequency.WEEKLY, weekdays=(6,))
    assert sunday.next_after(date(2026, 10, 18)) == date(2026, 10, 25)  # A week on.


def test_a_month_day_skips_the_months_without_it() -> None:
    """January 31: February, April, June, September and November have no 31st."""
    assert dates(DAY_31, date(2026, 1, 31), 7) == [
        date(2026, 1, 31),
        date(2026, 3, 31),
        date(2026, 5, 31),
        date(2026, 7, 31),
        date(2026, 8, 31),
        date(2026, 10, 31),
        date(2026, 12, 31),
    ]
    day_29 = RepeatRule(frequency=RepeatFrequency.MONTHLY_DAY, month_day=29)
    assert day_29.next_after(date(2027, 1, 29)) == date(2027, 3, 29)  # Not a leap year.
    assert day_29.next_after(date(2028, 1, 29)) == date(2028, 2, 29)
    day_15 = RepeatRule(frequency=RepeatFrequency.MONTHLY_DAY, month_day=15)
    assert day_15.first_on_or_after(date(2026, 10, 15)) == date(2026, 10, 15)
    assert day_15.next_after(date(2026, 10, 14)) == date(2026, 10, 15)
    assert day_15.next_after(date(2026, 12, 15)) == date(2027, 1, 15)


def test_the_last_day_of_each_month_is_its_own_rule() -> None:
    assert dates(LAST_DAY, date(2028, 1, 15), 4) == [
        date(2028, 1, 31),
        date(2028, 2, 29),
        date(2028, 3, 31),
        date(2028, 4, 30),
    ]
    assert LAST_DAY.next_after(date(2026, 12, 31)) == date(2027, 1, 31)
    assert LAST_DAY.matches(date(2026, 2, 28))
    assert not DAY_31.matches(date(2026, 2, 28))


def test_the_end_date_is_the_last_date_it_repeats_on() -> None:
    ending = DAILY.model_copy(update={"end_date": date(2026, 10, 16)})
    assert dates(ending, date(2026, 10, 15), 5) == [date(2026, 10, 15), date(2026, 10, 16)]
    assert ending.first_on_or_after(date(2026, 10, 17)) is None
    assert ending.next_after(date(2026, 10, 16)) is None


def test_no_date_past_the_calendar() -> None:
    assert DAILY.next_after(date.max) is None
    assert MONDAY_WEDNESDAY.next_after(date(9999, 12, 30)) is None
    assert DAY_31.next_after(date(9999, 12, 31)) is None
    assert LAST_DAY.next_after(date(9999, 12, 31)) is None


def test_the_next_occurrence_skips_missed_dates() -> None:
    """Completed a week late, a daily task comes back tomorrow, not seven times; completed
    early, it comes back on the date after its own."""
    assert next_occurrence(DAILY, date(2026, 10, 8), date(2026, 10, 15)) == date(2026, 10, 16)
    assert next_occurrence(DAILY, date(2026, 10, 20), date(2026, 10, 15)) == date(2026, 10, 21)
    assert next_occurrence(MONDAY_WEDNESDAY, date(2026, 10, 12), date(2026, 10, 19)) == date(
        2026, 10, 21
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"frequency": "weekly"},
        {"frequency": "daily", "weekdays": (1,)},
        {"frequency": "weekly", "weekdays": (7,)},
        {"frequency": "monthly_day"},
        {"frequency": "monthly_day", "month_day": 32},
        {"frequency": "monthly_last", "month_day": 3},
        {"frequency": "yearly"},
    ],
)
def test_a_rule_names_exactly_what_its_frequency_needs(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RepeatRule.model_validate(fields)


def test_a_repeat_edit_changes_later_repeats_by_default() -> None:
    assert RepeatEdit(rule=DAILY).later_too is True
    assert RepeatEdit(rule=None).series_revision is None
