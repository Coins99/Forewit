"""Known local/cache commitments over 7 or 30 local dates. No provider calls."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import ActionFilter
from mailbrief.domain.events import CalendarEvent, CalendarRange
from mailbrief.domain.google_calendar import GoogleEvent
from mailbrief.domain.intervals import Interval, minutes
from mailbrief.domain.routes import CalendarRoute, TaskRoute
from mailbrief.domain.upcoming import Upcoming, UpcomingItem
from mailbrief.services.events import EventService


def overview(calendar: CalendarRange, zone: ZoneInfo) -> Upcoming:
    """Deduplicate canonical identities and union clipped timed intervals in UTC."""
    first, last = calendar.first_day, calendar.last_day
    start = datetime.combine(first, time.min, zone).astimezone(UTC)
    end = datetime.combine(last + timedelta(days=1), time.min, zone).astimezone(UTC)
    items: dict[tuple[str, ...], UpcomingItem] = {}
    intervals: list[Interval] = []
    all_day = 0
    entry: tuple[Literal["event"], str] | tuple[Literal["google"], str, str]
    events: tuple[CalendarEvent | GoogleEvent, ...] = (*calendar.events, *calendar.google_events)
    identity: tuple[str, ...]
    for event in events:
        if isinstance(event, CalendarEvent):
            identity = ("event", event.public_id)
            entry = ("event", event.public_id)
        else:
            identity = ("google", event.calendar_id, event.event_id)
            entry = ("google", event.calendar_id, event.event_id)
        if identity in items:
            continue
        if event.all_day:
            assert event.start_date is not None and event.last_date is not None
            if event.start_date > last or event.last_date < first:
                continue
            day = max(first, event.start_date)
            all_day += 1
            reason = "All-day event"
        else:
            assert event.starts_at_utc is not None and event.ends_at_utc is not None
            left, right = max(start, event.starts_at_utc), min(end, event.ends_at_utc)
            if left >= right:
                continue
            intervals.append((left, right))
            day = left.astimezone(zone).date()
            reason = (
                f"{left.astimezone(zone).isoformat(timespec='minutes')} – "
                f"{right.astimezone(zone).isoformat(timespec='minutes')}"
            )
        items[identity] = UpcomingItem(
            identity=identity,
            title=event.title,
            day=day,
            reasons=(reason,),
            route=CalendarRoute(day=day, entry=entry),
            sort_at_utc=None if event.all_day else event.starts_at_utc,
        )
    for marker in sorted(calendar.markers, key=lambda value: value.sort_key()):
        if not first <= marker.day <= last:
            continue
        identity = ("task", marker.action_public_id)
        label = (
            "Waiting follow-up"
            if marker.waiting and marker.kind.value == "target"
            else marker.kind.value.title()
        )
        reason = f"{label} · {marker.day.isoformat()}"
        previous = items.get(identity)
        reasons = tuple(dict.fromkeys((*previous.reasons, reason))) if previous else (reason,)
        items[identity] = UpcomingItem(
            identity=identity,
            title=marker.action_title,
            day=min(marker.day, previous.day) if previous else marker.day,
            reasons=reasons,
            route=TaskRoute(
                public_id=marker.action_public_id,
                view=ActionFilter.WAITING if marker.waiting else ActionFilter.OPEN,
            ),
            sort_at_utc=marker.due_at_utc,
        )
    coverage = calendar.google_coverage
    return Upcoming(
        calendar=calendar,
        items=tuple(
            sorted(
                items.values(),
                key=lambda item: (
                    item.day,
                    item.sort_at_utc or datetime.min.replace(tzinfo=UTC),
                    item.identity,
                ),
            )
        ),
        timed_minutes=minutes(intervals),
        all_day_events=all_day,
        google_complete=coverage is not None and coverage.covers(first, last),
        google_synced_at_utc=None if coverage is None else coverage.synced_at_utc,
    )


class UpcomingService:
    def __init__(self, session: AsyncSession) -> None:
        self.events = EventService(session)

    async def load(self, first: date, zone: ZoneInfo, *, days: int = 7) -> Upcoming:
        if days not in (7, 30):
            raise ValueError("Choose seven or thirty local dates.")
        return overview(
            await self.events.list_range(first, first + timedelta(days=days - 1), zone), zone
        )
