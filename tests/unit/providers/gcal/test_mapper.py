"""Mapping Google's calendar list and events: shapes, bounds and untrusted text."""

from datetime import UTC, date, datetime
from typing import Any

import pytest

from mailbrief.domain.google_calendar import UNTITLED, GoogleCalendarInfo, is_calendar_link
from mailbrief.providers.gcal.mapper import map_calendar, map_event

CALENDAR = GoogleCalendarInfo(
    calendar_id="owner@example.com", name="Owner", primary=True, shown=True
)


def event(**fields: Any) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "abc123",
        "status": "confirmed",
        "summary": "Standup",
        "start": {"dateTime": "2026-10-12T09:00:00-04:00", "timeZone": "America/Toronto"},
        "end": {"dateTime": "2026-10-12T09:30:00-04:00"},
    }
    return base | fields


def test_calendars_keep_only_readable_ones_and_follow_googles_list() -> None:
    primary = map_calendar(
        {"id": "owner@example.com", "summary": "Owner", "accessRole": "owner", "primary": True}
    )
    assert primary == CALENDAR
    shared = map_calendar(
        {
            "id": "team@group.calendar.google.com",
            "summary": "Team",
            "summaryOverride": " Work\n",
            "accessRole": "reader",
            "selected": True,
        }
    )
    assert shared is not None and shared.name == "Work" and shared.shown and not shared.primary
    hidden = map_calendar(
        {"id": "x", "summary": "Hidden", "accessRole": "writer", "selected": True, "hidden": True}
    )
    assert hidden is not None and not hidden.shown
    unselected = map_calendar({"id": "y", "accessRole": "writer"})
    assert unselected is not None and not unselected.shown and unselected.name == "y"
    refused: list[dict[str, object]] = [
        {"id": "z", "accessRole": "freeBusyReader", "selected": True},
        {"id": "z", "accessRole": "owner", "deleted": True},
        {"id": "", "accessRole": "owner"},
        {"id": "bad\nid", "accessRole": "owner"},
        {"accessRole": "owner"},
    ]
    for item in refused:
        assert map_calendar(item) is None


def test_timed_event_is_kept_in_utc_without_what_the_page_never_shows() -> None:
    mapped = map_event(event(location="Room 4", transparency="transparent"), CALENDAR)
    assert mapped is not None and not mapped.all_day
    assert mapped.starts_at_utc == datetime(2026, 10, 12, 13, 0, tzinfo=UTC)
    assert mapped.ends_at_utc == datetime(2026, 10, 12, 13, 30, tzinfo=UTC)
    assert mapped.calendar_name == "Owner"
    assert "Room 4" not in mapped.model_dump_json()
    assert {"location", "busy"}.isdisjoint(type(mapped).model_fields)


def test_all_day_end_is_exclusive_in_google() -> None:
    mapped = map_event(event(start={"date": "2026-10-14"}, end={"date": "2026-10-17"}), CALENDAR)
    assert mapped is not None and mapped.all_day
    assert (mapped.start_date, mapped.last_date) == (date(2026, 10, 14), date(2026, 10, 16))
    one_day = map_event(event(start={"date": "2026-10-14"}, end={"date": "2026-10-15"}), CALENDAR)
    assert one_day is not None and one_day.last_date == date(2026, 10, 14)


def test_untrusted_text_is_cleaned_and_bounded() -> None:
    mapped = map_event(event(summary="\x1b[31mPay\u202e now\n" + "x" * 300), CALENDAR)
    assert mapped is not None
    assert mapped.title.startswith("[31mPay now x") and len(mapped.title) == 200
    untitled = map_event(event(summary="  "), CALENDAR)
    assert untitled is not None and untitled.title == UNTITLED


def test_only_google_calendar_links_are_kept() -> None:
    good = map_event(event(htmlLink="https://www.google.com/calendar/event?eid=abc"), CALENDAR)
    assert good is not None and str(good.web_link).startswith("https://www.google.com/calendar/")
    for link in (
        "https://www.google.com/calendar",
        "https://calendar.google.com/calendar/event?eid=abc",
    ):
        assert is_calendar_link(link)
    for link in (
        "http://www.google.com/calendar/event?eid=abc",
        "https://evil.example/calendar",
        "https://user@calendar.google.com/x",
        "https://:secret@calendar.google.com/x",
        "https://www.google.com/url?q=https://evil.example/",
        "https://www.google.com/calendarx",
        "https://www.google.com:8443/calendar/event",
        "https://calendar.google.com:bad/x",
        "javascript:alert(1)",
    ):
        assert not is_calendar_link(link)
        mapped = map_event(event(htmlLink=link), CALENDAR)
        assert mapped is not None and mapped.web_link is None


def test_an_unusable_link_drops_only_the_link() -> None:
    long = "https://www.google.com/calendar/event?eid=" + "a" * 2_100  # Over HttpUrl's limit.
    mapped = map_event(event(htmlLink=long), CALENDAR)
    assert mapped is not None and mapped.title == "Standup" and mapped.web_link is None


@pytest.mark.parametrize(
    "fields",
    [
        {"status": "cancelled"},
        {"id": None},
        {"start": "2026-10-12"},
        {"start": {"dateTime": "not a time"}},
        {"start": {"dateTime": "2026-10-12T09:00:00"}},  # No offset.
        {"end": {"dateTime": "2026-10-12T08:00:00-04:00"}},  # Ends before it starts.
        {"start": {"date": "2026-10-14"}, "end": {"date": "nope"}},
        {"start": {"date": "2026-01-01"}, "end": {"date": "2027-06-01"}},  # Over a year.
    ],
)
def test_unusable_events_are_skipped(fields: dict[str, object]) -> None:
    assert map_event(event(**fields), CALENDAR) is None


def test_zero_length_events_are_kept() -> None:
    mapped = map_event(event(end={"dateTime": "2026-10-12T09:00:00-04:00"}), CALENDAR)
    assert mapped is not None and mapped.starts_at_utc == mapped.ends_at_utc
