"""Map Google's calendar list and events to MailBrief's models, defensively.

Titles, locations and names come from whoever made the event, so they are untrusted: they
lose control and format characters and are cut to fixed lengths. A malformed item is
skipped, never fatal.
"""

from datetime import date, datetime, timedelta

from pydantic import HttpUrl, ValidationError

from mailbrief.domain.google_calendar import (
    CALENDAR_NAME_MAX_CHARS,
    GOOGLE_ID_MAX_CHARS,
    GOOGLE_LOCATION_MAX_CHARS,
    GOOGLE_TITLE_MAX_CHARS,
    UNTITLED,
    GoogleCalendarInfo,
    GoogleEvent,
    is_calendar_link,
)
from mailbrief.text.prepare import clean_generated_text

_READABLE_ROLES = frozenset({"reader", "writer", "owner"})


def _text(value: object, limit: int) -> str:
    return clean_generated_text(value)[:limit].strip() if isinstance(value, str) else ""


def _identifier(value: object) -> str | None:
    if isinstance(value, str) and 0 < len(value) <= GOOGLE_ID_MAX_CHARS and value.isprintable():
        return value
    return None


def map_calendar(item: dict[str, object]) -> GoogleCalendarInfo | None:
    """A calendar MailBrief may read, or None (free/busy-only, deleted or malformed)."""
    calendar_id = _identifier(item.get("id"))
    if calendar_id is None or item.get("deleted") is True:
        return None
    if item.get("accessRole") not in _READABLE_ROLES:
        return None
    name = _text(item.get("summaryOverride"), CALENDAR_NAME_MAX_CHARS) or _text(
        item.get("summary"), CALENDAR_NAME_MAX_CHARS
    )
    primary = item.get("primary") is True
    shown = primary or (item.get("selected") is True and item.get("hidden") is not True)
    try:
        return GoogleCalendarInfo(
            calendar_id=calendar_id,
            name=name or calendar_id[:CALENDAR_NAME_MAX_CHARS],
            primary=primary,
            shown=shown,
        )
    except ValidationError:
        return None


def _moment(value: object) -> datetime | None:
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _day(value: object) -> date | None:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def map_event(item: dict[str, object], calendar: GoogleCalendarInfo) -> GoogleEvent | None:
    """An event to show, or None when it is cancelled, malformed or longer than a year.

    An all-day event's end date is exclusive in Google, so its last day is the day before.
    """
    event_id = _identifier(item.get("id"))
    if event_id is None or item.get("status") == "cancelled":
        return None
    start, end = item.get("start"), item.get("end")
    if not isinstance(start, dict) or not isinstance(end, dict):
        return None
    fields: dict[str, object]
    if "dateTime" in start and "dateTime" in end:
        begins, ends = _moment(start.get("dateTime")), _moment(end.get("dateTime"))
        if begins is None or ends is None:
            return None
        fields = {"all_day": False, "starts_at_utc": begins, "ends_at_utc": ends}
    else:
        first, after = _day(start.get("date")), _day(end.get("date"))
        if first is None or after is None:
            return None
        fields = {
            "all_day": True,
            "start_date": first,
            "last_date": max(first, after - timedelta(days=1)),
        }
    link = item.get("htmlLink")
    location = _text(item.get("location"), GOOGLE_LOCATION_MAX_CHARS)
    try:
        return GoogleEvent(
            calendar_id=calendar.calendar_id,
            calendar_name=calendar.name,
            event_id=event_id,
            title=_text(item.get("summary"), GOOGLE_TITLE_MAX_CHARS) or UNTITLED,
            location=location or None,
            web_link=HttpUrl(link) if isinstance(link, str) and is_calendar_link(link) else None,
            busy=item.get("transparency") != "transparent",
            **fields,
        )
    except (ValidationError, ValueError):
        return None
