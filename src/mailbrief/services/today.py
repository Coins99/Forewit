"""The owner's day at a glance (the Today page's "Your day"): local data only.

Nothing here contacts Gmail, Google Calendar or an AI provider, and nothing is written. It
works with no account connected.
"""

from collections.abc import Iterable
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import Action, ActionFilter, ActionStatus
from mailbrief.domain.analysis import DeadlinePrecision
from mailbrief.domain.common import normalize_utc
from mailbrief.domain.today import AttentionItem, AttentionReason, DaySummary
from mailbrief.services.actions import ActionService, action_order
from mailbrief.services.events import EventService

_ORDER = {reason: number for number, reason in enumerate(AttentionReason)}
_EARLIEST = datetime.min.replace(tzinfo=UTC)


def due_day(action: Action, zone: ZoneInfo) -> date | None:
    """The day a dated deadline falls on: a date deadline's own date, or a timed one's day
    in ``zone``; None without a dated deadline."""
    if action.deadline_precision is DeadlinePrecision.DATE:
        return action.deadline_date
    if action.deadline_precision is DeadlinePrecision.DATETIME and action.deadline_at_utc:
        return action.deadline_at_utc.astimezone(zone).date()
    return None


def attention_reason(
    action: Action, *, today: date, zone: ZoneInfo, now: datetime
) -> AttentionReason | None:
    """Why an open action needs attention today: its deadline passed, falls today, or its
    target date is today or has passed. None for a completed action, or one with nothing due
    or planned by today."""
    if action.status is not ActionStatus.OPEN:
        return None
    if action.is_overdue(normalize_utc(now)):
        return AttentionReason.OVERDUE
    if due_day(action, zone) == today:
        return AttentionReason.DUE_TODAY
    if action.target_date == today:
        return AttentionReason.PLANNED_TODAY
    if action.target_date is not None and action.target_date < today:
        return AttentionReason.PLANNED_EARLIER
    return None


def needs_attention(
    actions: Iterable[Action], *, today: date, zone: ZoneInfo, now: datetime
) -> tuple[AttentionItem, ...]:
    """The actions that need attention, most urgent reason first, then as the Actions page
    orders them (target date, due time, priority, creation). Each action appears once."""
    items = [
        AttentionItem(action=action, reason=reason)
        for action in {action.public_id: action for action in actions}.values()
        if (reason := attention_reason(action, today=today, zone=zone, now=now)) is not None
    ]
    return tuple(
        sorted(
            items,
            key=lambda item: (_ORDER[item.reason], action_order(item.action)),
        )
    )


def new_replies(actions: Iterable[Action]) -> tuple[Action, ...]:
    """The open actions with messages from others the owner hasn't marked seen, latest
    reply first."""
    found: dict[str, tuple[datetime, Action]] = {}
    for action in actions:
        thread = action.thread
        if action.status is ActionStatus.OPEN and thread is not None and thread.new_messages:
            # A thread with new messages always has the latest one's time.
            found[action.public_id] = (thread.latest_at_utc or _EARLIEST, action)
    return tuple(
        action for _, action in sorted(found.values(), key=lambda pair: pair[0], reverse=True)
    )


class TodayService:
    """Reads the owner's day; never writes."""

    def __init__(self, session: AsyncSession) -> None:
        self._actions = ActionService(session)
        self._events = EventService(session)

    async def summary(self, today: date, zone: ZoneInfo, now: datetime) -> DaySummary:
        """``today`` in ``zone``, as of ``now``: see DaySummary."""
        actions = [
            *await self._actions.list_actions(ActionFilter.OPEN),
            *await self._actions.list_actions(ActionFilter.WAITING),
        ]
        return DaySummary(
            day=today,
            attention=needs_attention(actions, today=today, zone=zone, now=now),
            replies=new_replies(actions),
            calendar=await self._events.list_range(today, today, zone),
        )
