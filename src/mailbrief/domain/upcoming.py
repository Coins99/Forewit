"""A deterministic view of known commitments; never an availability calculation. Free
time and workload are the Calendar's (``domain.availability``, ``domain.workload``)."""

from datetime import date, datetime

from mailbrief.domain.common import DomainModel
from mailbrief.domain.events import CalendarRange
from mailbrief.domain.routes import Route


class UpcomingItem(DomainModel):
    identity: tuple[str, ...]
    title: str
    day: date
    reasons: tuple[str, ...]
    route: Route
    sort_at_utc: datetime | None = None


class Upcoming(DomainModel):
    calendar: CalendarRange
    items: tuple[UpcomingItem, ...]
    timed_minutes: float
    all_day_events: int
    google_complete: bool
    google_synced_at_utc: datetime | None = None
