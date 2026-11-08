"""Consistent local metric inputs; no provider calls and no capped UI list readers."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import cached_property
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.availability import day_span
from mailbrief.domain.events import CalendarRange
from mailbrief.domain.metric_entries import MetricDefinition
from mailbrief.domain.metrics import MetricQuery
from mailbrief.storage.day_plans import focus_days
from mailbrief.storage.events import EventRepository, event_from_row
from mailbrief.storage.google_calendar import GoogleCalendarRepository
from mailbrief.storage.metrics import MetricRepository, definition_from_row
from mailbrief.storage.tables import (
    ActionActivityTable,
    ActionTable,
    DayPlanItemTable,
    DayPlanTable,
    LifeAreaTable,
    MetricEntryTable,
    ProjectTable,
)
from mailbrief.storage.work_blocks import WorkBlockRepository, scheduled_from_rows
from mailbrief.storage.working_hours import WorkingHoursRepository

MAX_METRIC_SOURCE_ROWS: Final = 1_000_000


class MetricQueryTooLargeError(ValueError):
    def __init__(self) -> None:
        super().__init__("The metric query is too large. Choose a smaller range or profile.")


@dataclass(frozen=True)
class MetricSources:
    tasks: tuple[dict[str, Any], ...] = ()
    activities: tuple[dict[str, Any], ...] = ()
    entries: tuple[dict[str, Any], ...] = ()
    selections: tuple[dict[str, Any], ...] = ()
    projects: tuple[dict[str, Any], ...] = ()
    areas: tuple[dict[str, Any], ...] = ()
    definition: MetricDefinition | None = None
    calendar: CalendarRange | None = None

    @cached_property
    def project_areas(self) -> dict[str, str | None]:
        return {row["public_id"]: row["area_public_id"] for row in self.projects}


async def _rows(
    session: AsyncSession, table: Any, names: tuple[str, ...]
) -> tuple[dict[str, Any], ...]:
    result = await session.execute(
        select(*(table.c[name] for name in names)).limit(MAX_METRIC_SOURCE_ROWS + 1)
    )
    rows = tuple(dict(row) for row in result.mappings())
    if len(rows) > MAX_METRIC_SOURCE_ROWS:
        raise MetricQueryTooLargeError()
    return rows


async def read_metric_sources(
    session: AsyncSession,
    query: MetricQuery,
    now: datetime,
    *,
    google_on: bool,
) -> MetricSources:
    projects = await _rows(
        session, ProjectTable.__table__, ("public_id", "name", "area_public_id", "archived_at_utc")
    )
    areas = await _rows(session, LifeAreaTable.__table__, ("public_id", "name", "deleted_at_utc"))
    key = query.metric_key
    if key.startswith("custom."):
        parts = key.split(".")
        if len(parts) != 3:
            raise ValueError("The custom metric key is invalid.")
        row = await MetricRepository(session).definition(parts[1])
        if row is None:
            raise ValueError("The custom metric is unavailable.")
        statement = select(MetricEntryTable.__table__).where(
            MetricEntryTable.definition_id == row.public_id,
            MetricEntryTable.local_date <= query.dates.last,
            MetricEntryTable.deleted_at_utc.is_(None),
        )
        if row.kind == "amount":
            statement = statement.where(MetricEntryTable.local_date >= query.dates.first)
        result = await session.execute(statement.limit(MAX_METRIC_SOURCE_ROWS + 1))
        entries = tuple(dict(item) for item in result.mappings())
        if len(entries) > MAX_METRIC_SOURCE_ROWS:
            raise MetricQueryTooLargeError()
        return MetricSources(
            entries=entries, definition=definition_from_row(row), projects=projects, areas=areas
        )
    if key.startswith("tasks."):
        tasks = await _rows(
            session,
            ActionTable.__table__,
            (
                "id",
                "public_id",
                "title",
                "status",
                "ownership",
                "estimate_minutes",
                "project_public_id",
                "revision",
                "deleted_at_utc",
                "deadline_precision",
                "deadline_date",
                "deadline_at_utc",
                "deadline_timezone",
            ),
        )
        activities = (
            ()
            if key == "tasks.current_overdue"
            else await _rows(
                session,
                ActionActivityTable.__table__,
                (
                    "action_id",
                    "revision",
                    "kind",
                    "occurred_at_utc",
                    "undo_of_revision",
                    "project_public_id",
                    "area_public_id",
                    "legacy",
                ),
            )
        )
        return MetricSources(tasks=tasks, activities=activities, projects=projects, areas=areas)
    if key == "plan.selected_estimates":
        result = await session.execute(
            select(
                DayPlanTable.local_date,
                DayPlanTable.revision.label("plan_revision"),
                ActionTable.public_id,
                ActionTable.title,
                ActionTable.revision,
                ActionTable.estimate_minutes,
                ActionTable.status,
                ActionTable.ownership,
                ActionTable.project_public_id,
            )
            .join(DayPlanItemTable, DayPlanItemTable.plan_id == DayPlanTable.id)
            .join(ActionTable, ActionTable.public_id == DayPlanItemTable.task_public_id)
            .where(
                DayPlanTable.local_date.between(query.dates.first, query.dates.last),
                ActionTable.deleted_at_utc.is_(None),
            )
            .limit(MAX_METRIC_SOURCE_ROWS + 1)
        )
        selections = tuple(dict(row) for row in result.mappings())
        if len(selections) > MAX_METRIC_SOURCE_ROWS:
            raise MetricQueryTooLargeError()
        return MetricSources(selections=selections, projects=projects, areas=areas)
    # Reuse P3's repositories directly: EventService's 42-date UI cap is inapplicable
    # to bounded 366-date aggregates. Read a day of padding for boundary event buffers.
    zone = ZoneInfo(query.dates.zone)
    first, last = query.dates.first, query.dates.last
    start, end = query.dates.instant_bounds()
    repository = WorkBlockRepository(session)
    blocks = await repository.touching(start, end)
    if key.startswith("blocks."):
        shown = CalendarRange(
            first_day=first,
            last_day=last,
            events=(),
            markers=(),
            blocks=tuple(scheduled_from_rows(block, task) for block, task in blocks),
        )
        # Keep current project membership separately from the neutral calendar model.
        tasks = tuple(
            {
                "public_id": task.public_id,
                "project_public_id": task.project_public_id,
                "revision": task.revision,
            }
            for _, task in blocks
        )
        return MetricSources(calendar=shown, tasks=tasks, projects=projects, areas=areas)
    padded_first, padded_last = first - timedelta(days=1), last + timedelta(days=1)
    events = await EventRepository(session).touching(
        day_span(padded_first, zone)[0], day_span(padded_last, zone)[1], padded_first, padded_last
    )
    google = GoogleCalendarRepository(session)
    cached = (
        await google.touching(
            day_span(padded_first, zone)[0],
            day_span(padded_last, zone)[1],
            padded_first,
            padded_last,
        )
        if google_on
        else []
    )
    if len(events) + len(cached) + len(blocks) > MAX_METRIC_SOURCE_ROWS:
        raise MetricQueryTooLargeError()
    shown = CalendarRange(
        first_day=first,
        last_day=last,
        events=tuple(event_from_row(row) for row in events),
        markers=(),
        google_events=tuple(cached),
        google_knowledge=await google.knowledge(),
        google_coverage=await google.coverage(),
        google_checked_coverage=await google.checked_coverage(),
        blocks=tuple(scheduled_from_rows(block, task) for block, task in blocks),
        hours=await WorkingHoursRepository(session).read(),
        focus=await focus_days(session, first, last),
    )
    return MetricSources(calendar=shown, projects=projects, areas=areas)
