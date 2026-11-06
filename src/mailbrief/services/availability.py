"""Working hours, free time and the tasks that still need time (C04, ADR 0029).

Everything here is local: nothing reads or sends email, calls a provider or changes a task.
Saving working hours reads the clock once, commits once and rolls back on failure. Errors
carry static messages, and nothing logs a task's title.
"""

import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import ActionPriority, ActionStatus
from mailbrief.domain.analysis import ActionOwnership, DeadlinePrecision, deadline_due_at
from mailbrief.domain.availability import (
    FREE_SEARCH_DAYS,
    UNSCHEDULED_LIMIT,
    UnscheduledTask,
    UnscheduledTasks,
    availability,
    next_free,
)
from mailbrief.domain.common import normalize_utc, utc_now
from mailbrief.domain.working_hours import HoursEdit, WorkingHours
from mailbrief.services.actions import urgency
from mailbrief.services.calendar import day_window
from mailbrief.services.events import EventService
from mailbrief.services.writes import commit_once
from mailbrief.storage.tables import ActionTable, WorkBlockTable, WorkingHoursTable
from mailbrief.storage.working_hours import (
    HOURS_ROW_ID,
    WorkingHoursRepository,
    apply_hours,
    hours_from_row,
)

logger = logging.getLogger(__name__)

_CONFLICT: Final = "Working hours changed since they were loaded; open them again."
# Blocks are whole minutes; julianday() arithmetic is accurate to far less than this.
_LEFT_OVER: Final = 0.5


class HoursConflictError(ValueError):
    """The working hours changed since the caller read them."""

    def __init__(self) -> None:
        super().__init__(_CONFLICT)


class AvailabilityService:
    def __init__(self, session: AsyncSession, *, clock: Callable[[], datetime] = utc_now) -> None:
        self._session = session
        self._clock = clock
        self._repository = WorkingHoursRepository(session)

    async def hours(self) -> WorkingHours:
        """The saved working hours, or none set (revision 0 when never saved)."""
        return await self._repository.read()

    async def save_hours(self, edit: HoursEdit, expected_revision: int) -> WorkingHours:
        """Replace the working hours; ``expected_revision`` is the revision the caller saw,
        0 when they were never saved. A different revision raises HoursConflictError."""

        async def operation(now: datetime) -> WorkingHours:
            row = await self._repository.get_row_to_replace()
            if (0 if row is None else row.revision) != expected_revision:
                raise HoursConflictError()
            if row is None:
                row = WorkingHoursTable(id=HOURS_ROW_ID, revision=0)
                self._repository.add(row)
            apply_hours(row, edit)
            row.revision += 1
            row.updated_at_utc = now
            return hours_from_row(row)

        saved = await commit_once(self._session, self._clock, operation)
        logger.info("Working hours saved: %d minutes a week", saved.weekly_minutes)
        return saved

    async def next_free(
        self, after: datetime, length_minutes: int, zone: ZoneInfo, *, first_day: date | None = None
    ) -> datetime | None:
        """The first quarter hour at or after ``after`` that starts ``length_minutes`` of free
        time, looking FREE_SEARCH_DAYS days from ``first_day`` (``after``'s day by default);
        None when there is none, or no working hours are set."""
        start = normalize_utc(after)
        first = first_day or start.astimezone(zone).date()
        last = first + timedelta(days=FREE_SEARCH_DAYS - 1)
        shown = await EventService(self._session).list_range(first, last, zone)
        if not shown.hours.is_set:
            return None
        return next_free(availability(shown, shown.hours, zone), length_minutes, start, zone)

    async def unscheduled(
        self, today: date, zone: ZoneInfo, *, limit: int = UNSCHEDULED_LIMIT
    ) -> UnscheduledTasks:
        """The owner's live, open tasks that still need time, most urgent first, as the
        Actions page orders them; at most ``limit``, with how many there are in all.

        A task the owner does (not one waiting on someone) needs time when its estimate is
        more than its blocks from ``today`` on, or, with no estimate, when it has no block
        from ``today`` on. Earlier blocks don't count: a block is planned time, never work
        done.
        """
        since = day_window(today, zone).start_utc
        ends, starts = WorkBlockTable.ends_at_utc, WorkBlockTable.starts_at_utc
        length = (func.julianday(ends) - func.julianday(starts)) * (24 * 60)
        upcoming = (
            select(
                WorkBlockTable.action_id.label("action_id"),
                func.sum(length).label("minutes"),
            )
            .where(WorkBlockTable.deleted_at_utc.is_(None), WorkBlockTable.ends_at_utc > since)
            .group_by(WorkBlockTable.action_id)
            .subquery()
        )
        scheduled = func.coalesce(upcoming.c.minutes, 0.0)
        rows = (
            await self._session.execute(
                select(
                    ActionTable.id,
                    ActionTable.public_id,
                    ActionTable.title,
                    ActionTable.estimate_minutes,
                    ActionTable.priority,
                    ActionTable.target_date,
                    ActionTable.deadline_precision,
                    ActionTable.deadline_date,
                    ActionTable.deadline_at_utc,
                    ActionTable.deadline_timezone,
                    ActionTable.created_at_utc,
                    scheduled.label("scheduled"),
                )
                .outerjoin(upcoming, upcoming.c.action_id == ActionTable.id)
                .where(
                    ActionTable.deleted_at_utc.is_(None),
                    ActionTable.status == ActionStatus.OPEN.value,
                    ActionTable.ownership != ActionOwnership.WAITING_FOR.value,
                    or_(
                        and_(
                            ActionTable.estimate_minutes.is_(None),
                            upcoming.c.minutes.is_(None),
                        ),
                        ActionTable.estimate_minutes - scheduled > _LEFT_OVER,
                    ),
                )
            )
        ).all()
        dues = {
            row.id: deadline_due_at(
                DeadlinePrecision(row.deadline_precision),
                row.deadline_date,
                row.deadline_at_utc,
                row.deadline_timezone,
            )
            for row in rows
        }
        # As the Tasks page orders them: equal urgency keeps ID order. Only the tasks listed
        # are built.
        ranked = sorted(
            rows,
            key=lambda row: (
                urgency(
                    row.target_date, dues[row.id], row.priority, normalize_utc(row.created_at_utc)
                ),
                row.id,
            ),
        )
        tasks: list[UnscheduledTask] = []
        for row in ranked[:limit]:
            due = dues[row.id]
            if DeadlinePrecision(row.deadline_precision) is DeadlinePrecision.DATE:
                deadline_day = row.deadline_date
            elif due is not None:
                deadline_day = due.astimezone(zone).date()
            else:
                deadline_day = None
            tasks.append(
                UnscheduledTask(
                    action_public_id=row.public_id,
                    title=row.title,
                    estimate_minutes=row.estimate_minutes,
                    scheduled_minutes=round(row.scheduled),
                    priority=None if row.priority is None else ActionPriority(row.priority),
                    target_date=row.target_date,
                    deadline_day=deadline_day,
                )
            )
        return UnscheduledTasks(tasks=tuple(tasks), total=len(rows))
