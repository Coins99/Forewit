"""Dates, deadlines and lengths of time in a few words, in one style for the whole window:
the brief, its suggestion and proposal lines, the Actions page and the editors' lengths. The
action editor's date fields stay ISO, since dates are typed there."""

import re
from datetime import date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from mailbrief.domain.actions import Action, ActionProposal, StatedDeadline
from mailbrief.domain.analysis import ActionSuggestion, DeadlinePrecision
from mailbrief.domain.digests import DigestItem

# English, whatever the system language: Qt applies it to C date formatting, so strftime's
# weekday and month directives would follow it. Every weekday and month name in the window
# comes from these tables.
WEEKDAYS: Final = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS: Final = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)
FULL_WEEKDAYS: Final = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
MONTH_NAMES: Final = (
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
)

# A length typed as h:mm ("1:30"), as a fraction of hours ("1.5"), or as hours, minutes or
# both, each with or without its unit ("1.5h", "2 hrs", "90", "1h 30", "1 hr 30 mins").
_CLOCK_LENGTH: Final = re.compile(r"(\d{1,3}):([0-5]\d)")
_DECIMAL_HOURS: Final = re.compile(r"\d{1,3}[.,]\d+")
_UNIT_LENGTH: Final = re.compile(
    r"(?:(\d{1,3}(?:[.,]\d+)?)\s*h(?:ours?|rs?)?)?\s*(?:(\d{1,5})\s*(?:m(?:in(?:ute)?s?)?)?)?",
    re.IGNORECASE,
)


def length_text(length: timedelta) -> str:
    """A length of time: "1 min", "45 mins", "1 hr", "2 hrs 15 mins"."""
    hours, minutes = divmod(int(length.total_seconds()) // 60, 60)
    parts = [f"{hours} hr" if hours == 1 else f"{hours} hrs"] if hours else []
    if minutes or not hours:
        parts.append("1 min" if minutes == 1 else f"{minutes} mins")
    return " ".join(parts)


def parse_length(text: str) -> int | None:
    """A typed length in whole minutes, as length_text writes it or more loosely: a whole
    number is minutes ("90") and a fraction is hours ("1.5"), as are "1.5h", "1h 30" and
    "1:30". None for anything else, an empty text included."""
    text = text.strip()
    if clock := _CLOCK_LENGTH.fullmatch(text):
        return int(clock[1]) * 60 + int(clock[2])
    if _DECIMAL_HOURS.fullmatch(text):
        return round(float(text.replace(",", ".")) * 60)
    unit = _UNIT_LENGTH.fullmatch(text)
    if unit is None or not (unit[1] or unit[2]):
        return None
    hours = float(unit[1].replace(",", ".")) if unit[1] else 0.0
    return round(hours * 60) + int(unit[2] or 0)


def full_day_text(day: date, *, today: date) -> str:
    """The design's full English date, independent of the operating system locale."""
    text = f"{FULL_WEEKDAYS[day.weekday()]}, {MONTH_NAMES[day.month - 1]} {day.day}"
    return text if day.year == today.year else f"{text}, {day.year}"


def weekday(day: date) -> str:
    """A day's weekday as "Mon"."""
    return WEEKDAYS[day.weekday()]


def month_day(day: date, *, today: date) -> str:
    """A day as "Oct 5"; with the year too when it isn't ``today``'s: "Jan 4, 2027"."""
    text = f"{MONTHS[day.month - 1]} {day.day}"
    return text if day.year == today.year else f"{text}, {day.year}"


def day_text(day: date, *, today: date) -> str:
    """A day as "Mon Oct 5"; in a year other than ``today``'s (the owner's day), with the
    year too: "Mon Jan 4, 2027"."""
    return f"{weekday(day)} {month_day(day, today=today)}"


def moment_text(moment: datetime, zone: ZoneInfo, *, today: date) -> str:
    """A moment as "Thu Oct 8, 17:00" in ``zone``, with the year as ``day_text`` adds it."""
    local = moment.astimezone(zone)
    return f"{day_text(local.date(), today=today)}, {local:%H:%M}"


def deadline_text(
    item: Action | ActionSuggestion | ActionProposal | StatedDeadline | DigestItem,
    zone: ZoneInfo,
    *,
    today: date,
) -> str | None:
    """How a deadline reads, or None when there is none.

    - exact: "Thu Oct 8, 17:00" in ``zone``, the owner's;
    - date only: "Mon Oct 5", the day the email names, whatever the owner's zone;
    - unresolved: the email's phrase in quotes.

    A deadline in a year other than ``today``'s (the owner's day) names it: "Mon Jan 4, 2027".
    """
    if item.deadline_precision is DeadlinePrecision.DATETIME and item.deadline_at_utc:
        return moment_text(item.deadline_at_utc, zone, today=today)
    if item.deadline_precision is DeadlinePrecision.DATE and item.deadline_date:
        return day_text(item.deadline_date, today=today)
    if item.deadline_precision is DeadlinePrecision.UNRESOLVED and item.deadline_text:
        return f"“{item.deadline_text}”"
    return None
