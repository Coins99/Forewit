"""AI calendar proposals from the owner's instruction (ADR 0018, phase 3).

Nothing is sent before the owner approves a preview of exactly what leaves the device,
and the first use also records consent. The model only proposes: every change is checked
here (it names one of the owner's events that was sent, its times are real and inside the
planning window, and actions and Google events are never touched), and nothing changes
until the owner applies the changes they keep. Logs carry counts and codes only.
"""

import asyncio
import json
import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Final, Protocol
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.calendar_planning import (
    MAX_OVERLAPS,
    MAX_PROPOSED_CHANGES,
    MAX_SENT_ACTIONS,
    MAX_SENT_BUSY,
    MAX_SENT_EVENTS,
    MESSAGE_MAX_CHARS,
    PLANNING_DAYS,
    REASON_MAX_CHARS,
    BusyTime,
    CalendarPlan,
    CalendarPlanningRequest,
    ChangeCandidate,
    PlannedAction,
    PlannedEvent,
    PlanningCandidate,
    PlanningOptions,
    PlanningOutcome,
    PlanningPart,
    PlanningPreview,
    PlanningStatus,
    ProposedChange,
)
from mailbrief.domain.common import normalize_utc
from mailbrief.domain.drafting import DraftingProblem, PreviewLine
from mailbrief.domain.events import (
    EVENT_TITLE_MAX_CHARS,
    ActionMarker,
    CalendarEvent,
    ChangeKind,
    EventChange,
    EventEdit,
    MarkerKind,
    wall_clock,
)
from mailbrief.domain.google_calendar import GoogleEvent
from mailbrief.ports.calendar_planning import CalendarPlanningProvider
from mailbrief.ports.errors import ProviderError
from mailbrief.services.analysis import KEY_MISSING, provider_detail, provider_error_code
from mailbrief.services.events import EventService
from mailbrief.storage.repositories import OwnerConsentRepository
from mailbrief.text.prepare import clean_generated_text

logger = logging.getLogger(__name__)

CALENDAR_SCOPE: Final = "calendar"
CALENDAR_DISCLOSURE_VERSION: Final = "1"
_ATTEMPTS: Final = 2
_PROBLEM_CODES: Final = {
    DraftingProblem.INCOMPLETE: "AI_OUTPUT_INCOMPLETE",
    DraftingProblem.INVALID_OUTPUT: "AI_INVALID_OUTPUT",
    DraftingProblem.REFUSED: "AI_REFUSED",
}
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_CLOCK = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")
_WEEKDAYS: Final = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
_DEFAULT_LENGTH: Final = timedelta(hours=1)


class PlanningGate(Protocol):
    """Asks the owner to approve one request; the preview says whether it is first use."""

    async def request_planning_consent(self, preview: PlanningPreview) -> bool: ...


@dataclass(frozen=True, slots=True)
class PreparedPlan:
    """One prepared request: exactly what would be sent, and what checking needs."""

    request: CalendarPlanningRequest = field(repr=False)
    preview: PlanningPreview
    zone: ZoneInfo
    keys: dict[str, CalendarEvent] = field(repr=False)
    events: tuple[CalendarEvent, ...] = field(repr=False)
    google: tuple[GoogleEvent, ...] = field(repr=False)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _local(moment: datetime, zone: ZoneInfo) -> str:
    return f"{moment.astimezone(zone):%Y-%m-%d %H:%M}"


def _when(
    all_day: bool,
    starts: datetime | None,
    ends: datetime | None,
    first: date | None,
    last: date | None,
    zone: ZoneInfo,
) -> tuple[str, str]:
    if all_day:
        assert first is not None and last is not None
        return first.isoformat(), last.isoformat()
    assert starts is not None and ends is not None
    return _local(starts, zone), _local(ends, zone)


def _actions(markers: Sequence[ActionMarker], zone: ZoneInfo) -> tuple[PlannedAction, ...]:
    """Each open action once, with its deadline and target as local text."""
    found: dict[str, dict[str, str | None]] = {}
    titles: dict[str, str] = {}
    for marker in markers:
        entry = found.setdefault(marker.action_public_id, {"deadline": None, "target": None})
        titles[marker.action_public_id] = marker.action_title
        if marker.kind is MarkerKind.TARGET:
            entry["target"] = marker.day.isoformat()
        else:
            entry["deadline"] = (
                marker.day.isoformat()
                if marker.due_at_utc is None
                else _local(marker.due_at_utc, zone)
            )
    return tuple(
        PlannedAction(title=titles[key], deadline=value["deadline"], target=value["target"])
        for key, value in list(found.items())[:MAX_SENT_ACTIONS]
    )


def _size(value: object) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str))


def preview_lines(request: CalendarPlanningRequest) -> tuple[PreviewLine, ...]:
    """What each part sends, in characters, computed from the request itself."""
    # ISO dates: month names would follow the system language.
    window = f"{request.window_first.isoformat()} to {request.window_last.isoformat()}"
    lines = [
        PreviewLine(
            label="Your instruction, today's date and your time zone",
            characters=len(request.instruction) + len(request.now_local) + len(request.time_zone),
        ),
        PreviewLine(
            label=f"Your {len(request.events)} events from {window}: titles and times",
            characters=_size([e.model_dump() for e in request.events]),
        ),
    ]
    if request.busy is not None:
        titled = any(b.title is not None for b in request.busy)
        lines.append(
            PreviewLine(
                label=f"{len(request.busy)} Google Calendar times"
                + (" with their titles" if titled else ", without titles"),
                characters=_size([b.model_dump() for b in request.busy]),
            )
        )
    if request.actions is not None:
        lines.append(
            PreviewLine(
                label=f"{len(request.actions)} open actions: titles, deadlines and target dates",
                characters=_size([a.model_dump() for a in request.actions]),
            )
        )
    return tuple(lines)


def _day(value: str | None) -> date | None:
    if value is None or not _DAY.fullmatch(value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _clock(value: str | None) -> time | None:
    match = None if value is None else _CLOCK.fullmatch(value.strip())
    return None if match is None else time(int(match[1]), int(match[2]))


def _text(value: str | None, limit: int) -> str | None:
    cleaned = "" if value is None else clean_generated_text(value)[:limit].strip()
    return cleaned or None


def _edit(change: ChangeCandidate, current: CalendarEvent | None, plan: PreparedPlan) -> EventEdit:
    """The new fields for a create or an update; raises ValueError when they don't work.

    On update a missing field keeps the current value, and moving only the start keeps
    the event's length.
    """
    zone = plan.zone
    title = _text(change.title, EVENT_TITLE_MAX_CHARS) or (
        None if current is None else current.title
    )
    all_day = (
        change.all_day
        if change.all_day is not None
        else (None if current is None else current.all_day)
    )
    if title is None or all_day is None:
        raise ValueError("a title and a shape are needed")
    if current is not None:
        first_now, last_now = current.days(zone)
    start_day, end_day = _day(change.start_date), _day(change.end_date)
    if all_day:
        first = start_day or (None if current is None else first_now)
        if first is None:
            raise ValueError("an all-day event needs its first day")
        last = end_day or (first if current is None or start_day is not None else last_now)
        edit = EventEdit(
            title=title,
            notes="" if current is None else current.notes,
            all_day=True,
            start_date=first,
            last_date=last,
            time_zone=zone.key,
        )
        _inside(first, last, plan)
        return edit
    start_clock, end_clock = _clock(change.start_time), _clock(change.end_time)
    timed_now = current is not None and not current.all_day
    if start_day is None and start_clock is None and timed_now:
        assert current is not None and current.starts_at_utc is not None
        starts = current.starts_at_utc
    else:
        local_start = (
            None
            if not timed_now or current is None or current.starts_at_utc is None
            else current.starts_at_utc.astimezone(zone)
        )
        day = (
            start_day
            or (None if local_start is None else local_start.date())
            or (first_now if current is not None else None)
        )
        clock = start_clock or (
            None if local_start is None else local_start.time().replace(second=0, microsecond=0)
        )
        if day is None or clock is None:
            raise ValueError("a timed event needs its start")
        starts = wall_clock(day, clock, zone)
    if end_day is None and end_clock is None:
        if timed_now and current is not None and current.starts_at_utc and current.ends_at_utc:
            ends = starts + (current.ends_at_utc - current.starts_at_utc)
        else:
            ends = starts + _DEFAULT_LENGTH
    else:
        local_end = (
            None
            if not timed_now or current is None or current.ends_at_utc is None
            else current.ends_at_utc.astimezone(zone)
        )
        day = end_day or starts.astimezone(zone).date()
        clock = end_clock or (
            None if local_end is None else local_end.time().replace(second=0, microsecond=0)
        )
        if clock is None:
            raise ValueError("a timed event needs its end")
        ends = wall_clock(day, clock, zone)
    edit = EventEdit(
        title=title,
        notes="" if current is None else current.notes,
        all_day=False,
        starts_at_utc=starts,
        ends_at_utc=ends,
        time_zone=zone.key,
    )
    first, last = (
        starts.astimezone(zone).date(),
        (ends - timedelta(microseconds=1)).astimezone(zone).date(),
    )
    _inside(first, last, plan)
    return edit


def _inside(first: date, last: date, plan: PreparedPlan) -> None:
    if first < plan.request.window_first or last > plan.request.window_last:
        raise ValueError("the change falls outside the planning window")


def _overlaps(
    proposal: EventChange,
    plan: PreparedPlan,
    deleted: set[str],
    moved: dict[str, EventEdit],
) -> tuple[str, ...]:
    """Titles of the timed events this change would overlap, the owner's then Google's.

    Every timed Google event counts, since its free/busy flag isn't kept.
    """
    edit = proposal.edit
    if edit is None or edit.all_day or edit.starts_at_utc is None or edit.ends_at_utc is None:
        return ()
    start, end = edit.starts_at_utc, edit.ends_at_utc
    own = None if proposal.event is None else proposal.event.public_id
    found: list[str] = []
    for event in plan.events:
        if event.public_id in deleted or event.public_id == own:
            continue
        other = moved.get(event.public_id) or event.edit()
        if other.all_day or other.starts_at_utc is None or other.ends_at_utc is None:
            continue
        if other.starts_at_utc < end and other.ends_at_utc > start:
            found.append(other.title)
    for google in plan.google:
        if google.all_day or google.starts_at_utc is None:
            continue
        assert google.ends_at_utc is not None
        if google.starts_at_utc < end and google.ends_at_utc > start:
            found.append(f"{google.title} ({google.calendar_name})")
    return tuple(found[:MAX_OVERLAPS])


def validate_plan(candidate: PlanningCandidate, plan: PreparedPlan) -> CalendarPlan:
    """Check an untrusted answer change by change; a bad change is dropped, never fatal.

    A change is dropped when its op is unknown, it names an event that wasn't sent or was
    already changed, its times don't work or leave the window, or it changes nothing. At
    most MAX_PROPOSED_CHANGES are kept.
    """
    kept: list[tuple[EventChange, str | None]] = []
    seen: set[str] = set()
    ignored = 0
    for change in candidate.changes:
        if len(kept) >= MAX_PROPOSED_CHANGES:
            ignored += 1
            continue
        try:
            checked = _change(change, plan, seen)
        except ValueError:
            checked = None
        if checked is None:
            ignored += 1
            continue
        kept.append((checked, _text(change.reason, REASON_MAX_CHARS)))
    deleted = {c.event.public_id for c, _ in kept if c.kind is ChangeKind.DELETE and c.event}
    moved = {
        c.event.public_id: c.edit
        for c, _ in kept
        if c.kind is ChangeKind.UPDATE and c.event and c.edit
    }
    proposals = tuple(
        ProposedChange(change=c, reason=reason, overlaps=_overlaps(c, plan, deleted, moved))
        for c, reason in kept
    )
    return CalendarPlan(
        proposals=proposals, message=_text(candidate.message, MESSAGE_MAX_CHARS), ignored=ignored
    )


def _change(change: ChangeCandidate, plan: PreparedPlan, seen: set[str]) -> EventChange | None:
    op = change.op.strip().casefold()
    if op == ChangeKind.CREATE.value:
        return EventChange(kind=ChangeKind.CREATE, edit=_edit(change, None, plan))
    if op not in (ChangeKind.UPDATE.value, ChangeKind.DELETE.value):
        return None
    key = (change.event_key or "").strip()
    event = plan.keys.get(key)
    if event is None or key in seen:
        return None
    seen.add(key)
    if op == ChangeKind.DELETE.value:
        return EventChange(kind=ChangeKind.DELETE, event=event)
    edit = _edit(change, event, plan)
    if edit == event.edit():
        return None  # It changes nothing.
    return EventChange(kind=ChangeKind.UPDATE, event=event, edit=edit)


class CalendarPlanningService:
    """Prepare and run one AI request for proposed calendar changes; never applies them."""

    def __init__(
        self,
        session: AsyncSession,
        provider: CalendarPlanningProvider,
        *,
        clock: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._session = session
        self._provider = provider
        self._clock = clock
        self._consents = OwnerConsentRepository(session)

    async def consent_active(self) -> bool:
        consent = await self._consents.get_active(
            self._provider.provider_name, CALENDAR_SCOPE, CALENDAR_DISCLOSURE_VERSION
        )
        return consent is not None

    async def prepare(
        self, options: PlanningOptions, first_day: date, zone: ZoneInfo
    ) -> PreparedPlan:
        """Build exactly what would be sent for the four weeks from ``first_day``, and its
        preview; nothing is sent or stored."""
        last_day = first_day + timedelta(days=PLANNING_DAYS - 1)
        shown = await EventService(self._session).list_range(first_day, last_day, zone)
        events = shown.events[:MAX_SENT_EVENTS]
        keys = {f"e{number}": event for number, event in enumerate(events, start=1)}
        planned = tuple(
            PlannedEvent(
                key=key,
                title=event.title,
                all_day=event.all_day,
                start=_when(
                    event.all_day,
                    event.starts_at_utc,
                    event.ends_at_utc,
                    event.start_date,
                    event.last_date,
                    zone,
                )[0],
                end=_when(
                    event.all_day,
                    event.starts_at_utc,
                    event.ends_at_utc,
                    event.start_date,
                    event.last_date,
                    zone,
                )[1],
            )
            for key, event in keys.items()
        )
        busy = None
        if PlanningPart.GOOGLE_BUSY in options.parts:
            titled = PlanningPart.GOOGLE_TITLES in options.parts
            busy = tuple(
                BusyTime(
                    all_day=g.all_day,
                    start=_when(
                        g.all_day, g.starts_at_utc, g.ends_at_utc, g.start_date, g.last_date, zone
                    )[0],
                    end=_when(
                        g.all_day, g.starts_at_utc, g.ends_at_utc, g.start_date, g.last_date, zone
                    )[1],
                    title=g.title if titled else None,
                )
                # Every cached Google event counts as busy: its free/busy flag isn't kept.
                for g in shown.google_events
            )[:MAX_SENT_BUSY]
        actions = _actions(shown.markers, zone) if PlanningPart.ACTIONS in options.parts else None
        local_now = normalize_utc(self._clock()).astimezone(zone)
        request = CalendarPlanningRequest(
            instruction=options.instruction,
            now_local=f"{local_now:%Y-%m-%d %H:%M} ({_WEEKDAYS[local_now.weekday()]})",
            time_zone=zone.key,
            window_first=first_day,
            window_last=last_day,
            events=planned,
            busy=busy,
            actions=actions,
        )
        preview = PlanningPreview(
            provider=self._provider.provider_name,
            model=self._provider.model_name,
            privacy_notice=self._provider.privacy_notice,
            first_use=not await self.consent_active(),
            lines=preview_lines(request),
        )
        return PreparedPlan(request, preview, zone, keys, shown.events, shown.google_events)

    async def generate(
        self, plan: PreparedPlan, gate: PlanningGate, cancel: asyncio.Event | None = None
    ) -> PlanningOutcome:
        """Ask approval, record first-use consent, call the provider and check the answer.

        Nothing is sent before approval. An unusable answer is tried once more. Nothing in
        the calendar changes here, whatever happens.
        """

        def cancelled() -> bool:
            return cancel is not None and cancel.is_set()

        if cancelled():
            return PlanningOutcome(status=PlanningStatus.CANCELLED)
        if not await self._provider.credentials_available():
            return PlanningOutcome(status=PlanningStatus.FAILED, error_code=KEY_MISSING)
        first_use = not await self.consent_active()
        preview = plan.preview.model_copy(update={"first_use": first_use})
        if not await gate.request_planning_consent(preview):
            logger.info("AI calendar planning declined")
            return PlanningOutcome(status=PlanningStatus.DECLINED)
        if cancelled():
            return PlanningOutcome(status=PlanningStatus.CANCELLED)
        if first_use:
            await self._consents.grant(
                self._provider.provider_name,
                CALENDAR_SCOPE,
                CALENDAR_DISCLOSURE_VERSION,
                normalize_utc(self._clock()),
            )
            await self._session.commit()
        checked: CalendarPlan | None = None
        problem: DraftingProblem | None = None
        for _ in range(_ATTEMPTS):
            if cancelled():
                return PlanningOutcome(status=PlanningStatus.CANCELLED)
            try:
                response = await self._provider.plan_calendar(plan.request)
            except ProviderError as exc:
                code = provider_error_code(exc)
                logger.info("AI calendar planning call failed: %s", code)
                return PlanningOutcome(
                    status=PlanningStatus.FAILED,
                    error_code=code,
                    provider_detail=provider_detail(exc),
                )
            if response.candidate is None:
                problem = response.problem
                continue
            checked = validate_plan(response.candidate, plan)
            break
        if cancelled():
            return PlanningOutcome(status=PlanningStatus.CANCELLED)
        if checked is None:
            code = _PROBLEM_CODES[problem or DraftingProblem.INVALID_OUTPUT]
            logger.info("AI calendar planning answer unusable: %s", code)
            return PlanningOutcome(status=PlanningStatus.FAILED, error_code=code)
        logger.info(
            "AI calendar planning: %d changes kept, %d dropped",
            len(checked.proposals),
            checked.ignored,
        )
        return PlanningOutcome(status=PlanningStatus.PROPOSED, plan=checked)
