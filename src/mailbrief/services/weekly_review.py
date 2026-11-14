"""The weekly review (A07, P5-1): saved records only, so it needs no Gmail, AI or network.

The done count is computed by the same metric code as Insights' "Distinct tasks completed",
from the same read snapshot as the rest of the review, so the two never disagree. The only
write is the owner's review record, which needs the revision the caller saw.
"""

import asyncio
import logging
from collections.abc import Callable, Iterable
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.actions import Action, ActionActivityKind, ActionFilter, ActionStatus
from mailbrief.domain.analysis import ActionOwnership
from mailbrief.domain.common import normalize_utc, utc_now
from mailbrief.domain.metrics import MetricDateRange, MetricQuery, MetricReducer
from mailbrief.domain.routes import TaskRoute
from mailbrief.domain.weekly_review import (
    WEEK_DAYS,
    ReviewTask,
    WeeklyReview,
    WeeklyReviewRecord,
    week_dates,
)
from mailbrief.domain.workload import workload
from mailbrief.services.actions import action_order
from mailbrief.services.day_plans import DayPlanService
from mailbrief.services.events import EventService
from mailbrief.services.metric_calculations import calculate_metric
from mailbrief.services.today import due_day
from mailbrief.services.upcoming import overview
from mailbrief.services.writes import commit_once
from mailbrief.storage.actions import ActionRepository
from mailbrief.storage.database import Database
from mailbrief.storage.metric_sources import MetricSources, read_metric_sources
from mailbrief.storage.tables import WeeklyReviewTable

logger = logging.getLogger(__name__)

DONE_METRIC = "tasks.distinct_completed"


class WeeklyReviewConflictError(ValueError):
    """The review record changed since the caller read it."""

    def __init__(self) -> None:
        super().__init__("This week's review changed. Reopen it and try again.")


def done_query(start: date, zone: ZoneInfo) -> MetricQuery:
    """Insights' distinct completed tasks for the week's seven local dates."""
    return MetricQuery(
        metric_key=DONE_METRIC,
        reducer=MetricReducer.DISTINCT,
        dates=MetricDateRange(
            first=start, last=start + timedelta(days=WEEK_DAYS - 1), zone=zone.key
        ),
    )


def _record(row: WeeklyReviewTable | None) -> WeeklyReviewRecord | None:
    if row is None:
        return None
    return WeeklyReviewRecord(
        week_start=row.week_start,
        revision=row.revision,
        reviewed_at_utc=row.reviewed_at_utc,
        reflection_note_public_id=row.reflection_note_public_id,
    )


def _route(action: Action) -> TaskRoute:
    waiting = action.ownership is ActionOwnership.WAITING_FOR
    return TaskRoute(
        public_id=action.public_id,
        view=ActionFilter.WAITING if waiting else ActionFilter.OPEN,
    )


def _row(action: Action, detail: str) -> ReviewTask:
    return ReviewTask(
        public_id=action.public_id,
        title=action.title,
        detail=detail,
        route=_route(action),
        revision=action.revision,
    )


def _project_names(sources: MetricSources) -> dict[str, str]:
    return {str(row["public_id"]): str(row["name"]) for row in sources.projects}


def _done(
    sources: MetricSources, start: date, zone: ZoneInfo, now: datetime
) -> tuple[tuple[ReviewTask, ...], int, int, tuple[ReviewTask, ...]]:
    result = calculate_metric(done_query(start, zone), sources, now, google_on=False)
    names = _project_names(sources)
    done: dict[str, ReviewTask] = {}
    for row in sorted(result.contributions, key=lambda found: (found.day, found.key)):
        if row.source_id in done:
            continue
        project = names.get(row.project_id or "", "")
        detail = " · ".join(part for part in (f"Done {row.day.isoformat()}", project) if part)
        done[row.source_id] = ReviewTask(
            public_id=row.source_id,
            title=row.label,
            detail=detail,
            route=TaskRoute(public_id=row.source_id, view=ActionFilter.COMPLETED),
        )
    first, end = result.query.dates.instant_bounds()
    skipped: list[ReviewTask] = []
    for task in sources.tasks:
        history = sources.task_histories.get(task["id"])
        if history is None or task["deleted_at_utc"] is not None:
            continue
        when = [
            event.occurred_at_utc
            for event in history.effective
            if event.kind is ActionActivityKind.SKIPPED and first <= event.occurred_at_utc < end
        ]
        if when:
            day = min(when).astimezone(zone).date()
            skipped.append(
                ReviewTask(
                    public_id=task["public_id"],
                    title=task["title"],
                    detail=f"Skipped {day.isoformat()}",
                    route=TaskRoute(public_id=task["public_id"], view=ActionFilter.COMPLETED),
                )
            )
    total = int(result.total or 0)
    return tuple(done.values()), total, result.coverage.partial_tasks, tuple(skipped)


def _open_reason(action: Action, last: date, zone: ZoneInfo, now: datetime) -> str | None:
    """Why an open task belongs under Still open, or None. Overdue is as of now."""
    if action.is_overdue(now):
        return "Overdue"
    due = due_day(action, zone)
    if due is not None and due <= last:
        return f"Due {due.isoformat()}"
    if action.target_date is not None and action.target_date <= last:
        return f"Planned {action.target_date.isoformat()}"
    return None


def still_open(
    actions: Iterable[Action],
    focus: dict[str, date],
    last: date,
    zone: ZoneInfo,
    now: datetime,
) -> tuple[tuple[ReviewTask, ...], tuple[ReviewTask, ...]]:
    """The open tasks the week left open, then the tasks waiting on someone. Each task is
    listed once; ``focus`` maps a task to the last day of the week it was in focus."""
    unique = {action.public_id: action for action in actions}
    listed: list[ReviewTask] = []
    waiting: list[ReviewTask] = []
    for action in sorted(unique.values(), key=action_order):
        if action.status is not ActionStatus.OPEN:
            continue
        if action.ownership is ActionOwnership.WAITING_FOR:
            since = action.waiting_since_utc
            days = 0 if since is None else max(0, (now - since).days)
            waiting.append(_row(action, f"Waiting {days} days" if days != 1 else "Waiting 1 day"))
            continue
        reason = _open_reason(action, last, zone, now)
        if reason is None and action.public_id in focus:
            reason = f"In focus {focus[action.public_id].isoformat()}"
        if reason is not None:
            listed.append(_row(action, reason))
    return tuple(listed), tuple(waiting)


class WeeklyReviewService:
    """Reads one week; writes only the owner's review record."""

    def __init__(
        self,
        database: Database,
        *,
        google_on: bool,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.database = database
        self.google_on = google_on
        self.clock = clock

    async def load(self, start: date, zone: ZoneInfo) -> WeeklyReview:
        """The week starting on Monday ``start`` in ``zone``, as of now."""
        if start.weekday() != 0:
            raise ValueError("A review week starts on a Monday.")
        now = normalize_utc(self.clock())
        query = done_query(start, zone)
        async with self.database.session() as session:
            await session.execute(text("BEGIN"))  # One read snapshot for every section.
            sources = await read_metric_sources(session, query, now, google_on=False)
            repository = ActionRepository(session)
            actions = await repository.load(
                [
                    *await repository.list_rows(ActionFilter.OPEN, limit=None),
                    *await repository.list_rows(ActionFilter.WAITING, limit=None),
                ],
                include_activity=False,
                include_blocks=False,
            )
            plans = DayPlanService(session)
            focus: dict[str, date] = {}
            for day in week_dates(start):
                for item in (await plans.load(day)).items:
                    focus[item.task_public_id] = day
            next_start = start + timedelta(days=WEEK_DAYS)
            shown = await EventService(session).list_range(
                next_start, next_start + timedelta(days=WEEK_DAYS - 1), zone, with_workload=True
            )
            record = await self._row(session, start)
        done, total, partial, skipped = await asyncio.to_thread(_done, sources, start, zone, now)
        listed, waiting = still_open(actions, focus, start + timedelta(days=6), zone, now)
        logger.info(
            "Weekly review read: %d done, %d open, %d waiting", total, len(listed), len(waiting)
        )
        return WeeklyReview(
            week_start=start,
            zone=zone.key,
            captured_at_utc=now,
            done=done,
            done_total=total,
            partial_tasks=partial,
            skipped=skipped,
            still_open=listed,
            waiting=waiting,
            next_week=overview(shown, zone),
            next_days=workload(shown, zone, now, google_on=self.google_on),
            record=_record(record),
        )

    async def record(self, start: date) -> WeeklyReviewRecord | None:
        async with self.database.session() as session:
            return _record(await self._row(session, start))

    async def mark_reviewed(
        self, start: date, expected_revision: int | None, *, reviewed: bool = True
    ) -> WeeklyReviewRecord:
        """Mark the week reviewed now, or clear the mark. ``expected_revision`` is the
        record's revision as read, or None when there was none."""
        return await self._change(
            start,
            expected_revision,
            lambda row, now: setattr(row, "reviewed_at_utc", now if reviewed else None),
        )

    async def attach_reflection(
        self, start: date, expected_revision: int | None, note_public_id: str | None
    ) -> WeeklyReviewRecord:
        """Link the note holding the owner's reflection on the week, or unlink it."""
        if note_public_id is not None and len(note_public_id) != 36:
            raise ValueError("Choose a saved note.")
        return await self._change(
            start,
            expected_revision,
            lambda row, _now: setattr(row, "reflection_note_public_id", note_public_id),
        )

    @staticmethod
    async def _row(session: AsyncSession, start: date) -> WeeklyReviewTable | None:
        found: WeeklyReviewTable | None = await session.scalar(
            select(WeeklyReviewTable).where(WeeklyReviewTable.week_start == start)
        )
        return found

    async def _change(
        self,
        start: date,
        expected_revision: int | None,
        apply: Callable[[WeeklyReviewTable, datetime], None],
    ) -> WeeklyReviewRecord:
        if start.weekday() != 0:
            raise ValueError("A review week starts on a Monday.")
        async with self.database.session() as session:

            async def change(now: datetime) -> WeeklyReviewRecord:
                row = await self._row(session, start)
                if row is None:
                    if expected_revision is not None:
                        raise WeeklyReviewConflictError()
                    row = WeeklyReviewTable(
                        week_start=start, revision=1, created_at_utc=now, updated_at_utc=now
                    )
                    apply(row, now)
                    session.add(row)
                else:
                    if expected_revision != row.revision:
                        raise WeeklyReviewConflictError()
                    before = (row.reviewed_at_utc, row.reflection_note_public_id)
                    apply(row, now)
                    if (row.reviewed_at_utc, row.reflection_note_public_id) != before:
                        row.revision += 1
                        row.updated_at_utc = now
                await session.flush()
                result = _record(row)
                assert result is not None
                return result

            return await commit_once(session, self.clock, change)
