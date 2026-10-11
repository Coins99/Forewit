"""Calendar events: the two shapes, their bounds and the days they touch."""

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from mailbrief.domain.events import (
    ActionMarker,
    CalendarEvent,
    EventEdit,
    MarkerKind,
    check_timing,
    known_zone,
)

TORONTO = ZoneInfo("America/Toronto")
START = datetime(2026, 10, 12, 13, 0, tzinfo=UTC)
PUBLIC_ID = "5b1d7c2e-3f4a-4b6c-8d9e-0a1b2c3d4e5f"


def timed(**overrides: Any) -> EventEdit:
    fields: dict[str, Any] = {
        "title": "Dentist",
        "all_day": False,
        "starts_at_utc": START,
        "ends_at_utc": START + timedelta(hours=1),
        "time_zone": "America/Toronto",
    }
    return EventEdit(**(fields | overrides))


def all_day(**overrides: Any) -> EventEdit:
    fields: dict[str, Any] = {
        "title": "Conference",
        "all_day": True,
        "start_date": date(2026, 10, 14),
        "last_date": date(2026, 10, 16),
        "time_zone": "America/Toronto",
    }
    return EventEdit(**(fields | overrides))


def stored(edit: EventEdit) -> CalendarEvent:
    return CalendarEvent(
        public_id=PUBLIC_ID,
        revision=1,
        created_at_utc=START,
        updated_at_utc=START,
        **edit.model_dump(),
    )


def test_timed_edit_keeps_utc_and_strips_the_title() -> None:
    edit = timed(
        title="  Dentist  ",
        starts_at_utc=datetime(2026, 10, 12, 9, 0, tzinfo=TORONTO),
        ends_at_utc=datetime(2026, 10, 12, 10, 0, tzinfo=timezone(timedelta(hours=-4))),
    )
    assert edit.title == "Dentist"
    assert edit.starts_at_utc == START and edit.starts_at_utc.tzinfo is UTC
    assert edit.ends_at_utc == START + timedelta(hours=1)
    assert edit.notes == ""


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": ""},
        {"title": "x" * 201},
        {"title": "Two\nlines"},
        {"title": "Tab\there"},
        {"notes": "x" * 10_001},
        {"time_zone": "Mars/Olympus"},
        {"time_zone": "../etc/passwd"},
        {"starts_at_utc": datetime(2026, 10, 12, 13, 0)},  # No zone.
        {"ends_at_utc": START},  # Ends as it starts.
        {"ends_at_utc": START - timedelta(minutes=1)},
        {"ends_at_utc": START + timedelta(days=31, minutes=1)},
        {"ends_at_utc": None},
        {"start_date": date(2026, 10, 12)},  # A timed event has no days.
    ],
)
def test_timed_edit_rejects(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        timed(**overrides)


def test_timed_edit_may_last_exactly_the_longest() -> None:
    assert timed(ends_at_utc=START + timedelta(days=31)).ends_at_utc == START + timedelta(days=31)


@pytest.mark.parametrize(
    "overrides",
    [
        {"last_date": date(2026, 10, 13)},  # Before the first day.
        {"last_date": date(2026, 11, 14)},  # 32 days.
        {"last_date": None},
        {"starts_at_utc": START},  # An all-day event has no times.
        {"ends_at_utc": START},
    ],
)
def test_all_day_edit_rejects(overrides: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        all_day(**overrides)


def test_all_day_edit_may_be_one_day_or_the_longest() -> None:
    assert all_day(last_date=date(2026, 10, 14)).last_date == date(2026, 10, 14)
    assert all_day(last_date=date(2026, 11, 13)).last_date == date(2026, 11, 13)


def test_errors_never_echo_the_owner_text() -> None:
    with pytest.raises(ValidationError) as caught:
        timed(title="secret\nplan")
    assert "secret" not in str(caught.value)


def test_check_timing_messages_are_static() -> None:
    with pytest.raises(ValueError, match="no times"):
        check_timing(True, START, None, date(2026, 10, 1), date(2026, 10, 1))
    with pytest.raises(ValueError, match="no days"):
        check_timing(False, START, START + timedelta(hours=1), date(2026, 10, 1), None)


def test_known_zone() -> None:
    assert known_zone("Asia/Tokyo") == "Asia/Tokyo"
    with pytest.raises(ValueError, match="unknown"):
        known_zone("Nowhere/Land")


def test_timed_event_days_follow_the_zone() -> None:
    # 22:00-01:00 in Toronto touches two local days; in Tokyo it is one morning.
    event = stored(
        timed(
            starts_at_utc=datetime(2026, 10, 13, 2, 0, tzinfo=UTC),
            ends_at_utc=datetime(2026, 10, 13, 5, 0, tzinfo=UTC),
        )
    )
    assert event.days(TORONTO) == (date(2026, 10, 12), date(2026, 10, 13))
    assert event.days(ZoneInfo("Asia/Tokyo")) == (date(2026, 10, 13), date(2026, 10, 13))


def test_event_ending_at_midnight_does_not_touch_the_next_day() -> None:
    midnight = datetime(2026, 10, 13, 0, 0, tzinfo=TORONTO)
    event = stored(timed(starts_at_utc=midnight - timedelta(hours=2), ends_at_utc=midnight))
    assert event.days(TORONTO) == (date(2026, 10, 12), date(2026, 10, 12))


def test_all_day_event_keeps_its_days_in_every_zone() -> None:
    event = stored(all_day())
    for zone in (TORONTO, ZoneInfo("Pacific/Kiritimati"), ZoneInfo("Pacific/Pago_Pago")):
        assert event.days(zone) == (date(2026, 10, 14), date(2026, 10, 16))


def test_stored_event_round_trips_its_edit_and_validates() -> None:
    edit = timed(notes="Bring the form")
    assert stored(edit).edit() == edit
    with pytest.raises(ValidationError):
        CalendarEvent.model_validate(stored(edit).model_dump() | {"revision": 0})
    with pytest.raises(ValidationError):
        CalendarEvent.model_validate(stored(edit).model_dump() | {"start_date": date.today()})


def test_sort_keys_put_all_day_first_then_start() -> None:
    later = stored(
        timed(starts_at_utc=START + timedelta(hours=2), ends_at_utc=START + timedelta(hours=3))
    )
    earlier = stored(timed(title="Standup"))
    whole_day = stored(all_day())
    ordered = sorted([later, earlier, whole_day], key=CalendarEvent.sort_key)
    assert [event.title for event in ordered] == ["Conference", "Standup", "Dentist"]


def test_marker_sort_key_puts_deadlines_first() -> None:
    day = date(2026, 10, 14)

    def marker(kind: MarkerKind, title: str, due: datetime | None = None) -> ActionMarker:
        return ActionMarker(
            kind=kind, day=day, due_at_utc=due, action_public_id=PUBLIC_ID, action_title=title
        )

    ordered = sorted(
        [
            marker(MarkerKind.TARGET, "A target"),
            marker(MarkerKind.DEADLINE, "Timed", START),
            marker(MarkerKind.DEADLINE, "Untimed"),
        ],
        key=ActionMarker.sort_key,
    )
    assert [item.action_title for item in ordered] == ["Untimed", "Timed", "A target"]
    with pytest.raises(ValidationError):
        marker(MarkerKind.DEADLINE, "Naive", datetime(2026, 10, 14, 9, 0))
