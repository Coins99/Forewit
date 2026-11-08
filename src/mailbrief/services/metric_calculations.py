"""Metric semantics over materialized snapshots. CPU work runs outside the Qt thread."""

import hashlib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import cast
from zoneinfo import ZoneInfo

from mailbrief.domain.actions import ActionActivityKind
from mailbrief.domain.analysis import DeadlinePrecision, deadline_due_at
from mailbrief.domain.availability import availability, busy_spans, day_span
from mailbrief.domain.intervals import clip, union
from mailbrief.domain.metric_entries import (
    DefinedMetricEntry,
    MeasurementKind,
    MetricEntryInput,
    scaled_decimal,
)
from mailbrief.domain.metric_queries import (
    MetricBucket,
    MetricContribution,
    MetricCoverage,
    MetricQueryResult,
    duration_minutes,
    reduce_contributions,
)
from mailbrief.domain.metrics import (
    MetricDescriptor,
    MetricDimension,
    MetricQuery,
    MetricReducer,
    builtin_metric,
    custom_metric,
    validate_metric_query,
)
from mailbrief.domain.workload import workload
from mailbrief.storage.metric_sources import MetricSources


def descriptor_for(query: MetricQuery, sources: MetricSources) -> MetricDescriptor:
    if sources.definition is None:
        descriptor = builtin_metric(query.metric_key)
    else:
        definition = sources.definition
        suffix = "amount" if definition.kind is MeasurementKind.AMOUNT else "point"
        if query.metric_key != f"custom.{definition.public_id}.{suffix}":
            raise ValueError("The custom metric kind does not match its definition.")
        descriptor = custom_metric(
            query.metric_key, definition.name, definition.unit, definition.kind
        )
    validate_metric_query(query, descriptor)
    return descriptor


def _area(project: str | None, sources: MetricSources) -> str | None:
    return sources.project_areas.get(project or "")


def _matches(row: MetricContribution, query: MetricQuery) -> bool:
    return (query.project_public_id is None or row.project_id == query.project_public_id) and (
        query.area_public_id is None or row.area_id == query.area_public_id
    )


def _tasks(
    query: MetricQuery, sources: MetricSources, now: datetime
) -> tuple[list[MetricContribution], int]:
    first, end = query.dates.instant_bounds()
    zone = ZoneInfo(query.dates.zone)
    rows: list[MetricContribution] = []
    partial = 0
    for task in sources.tasks:
        if query.metric_key == "tasks.current_overdue":
            due = deadline_due_at(
                DeadlinePrecision(task["deadline_precision"]),
                task["deadline_date"],
                task["deadline_at_utc"],
                task["deadline_timezone"],
            )
            if (
                task["deleted_at_utc"] is None
                and task["status"] == "open"
                and due is not None
                and now >= due
            ):
                rows.append(
                    MetricContribution(
                        key=task["public_id"],
                        source_id=task["public_id"],
                        source_kind="task",
                        day=now.astimezone(zone).date(),
                        value=Decimal(1),
                        label=task["title"],
                        project_id=task["project_public_id"],
                        area_id=_area(task["project_public_id"], sources),
                        revision=task["revision"],
                        explanation="Current resolved deadline has passed.",
                    )
                )
            continue
        resolved = sources.task_histories[task["id"]]
        partial += not resolved.complete
        kind = (
            ActionActivityKind.CREATED
            if query.metric_key == "tasks.created"
            else ActionActivityKind.COMPLETED
        )
        for event in resolved.effective:
            if event.kind is kind and first <= event.occurred_at_utc < end:
                rows.append(
                    MetricContribution(
                        key=f"{task['public_id']}:{event.revision}",
                        source_id=task["public_id"],
                        source_kind="task_event",
                        day=event.occurred_at_utc.astimezone(zone).date(),
                        value=Decimal(1),
                        label=task["title"],
                        project_id=event.project_public_id,
                        area_id=event.area_public_id,
                        unknown_group=event.legacy,
                        revision=event.revision,
                        occurred_at_utc=event.occurred_at_utc,
                        explanation="Effective retained transition, grouped at the event time.",
                    )
                )
    return rows, partial


def _logs(query: MetricQuery, sources: MetricSources) -> list[MetricContribution]:
    assert sources.definition is not None
    definition = sources.definition
    rows: list[MetricContribution] = []
    for record in sources.entries:
        entry = MetricEntryInput.model_validate(
            {field: record[field] for field in MetricEntryInput.model_fields}
        )
        DefinedMetricEntry(definition=definition, entry=entry)
        rows.append(
            MetricContribution(
                key=record["public_id"],
                source_id=record["public_id"],
                source_kind="entry",
                day=entry.local_date,
                value=scaled_decimal(entry.value_scaled, definition.scale),
                project_id=entry.project_public_id,
                revision=record["revision"],
                occurred_at_utc=record["created_at_utc"],
                explanation="Owner-recorded date and exact value.",
            )
        )
    return rows


def _selections(sources: MetricSources) -> list[MetricContribution]:
    return [
        MetricContribution(
            key=f"{record['local_date']}:{record['public_id']}",
            source_id=record["public_id"],
            source_kind="day_plan",
            day=record["local_date"],
            value=None
            if record["estimate_minutes"] is None
            else Decimal(record["estimate_minutes"]),
            label=record["title"],
            project_id=record["project_public_id"],
            area_id=_area(record["project_public_id"], sources),
            revision=record["revision"],
            explanation=(
                f"Current selection: {record['status']}, {record['ownership']}. "
                "Live estimate, not a historical snapshot."
            ),
        )
        for record in sources.selections
    ]


def _micros(start: datetime, end: datetime) -> int:
    duration = end - start
    return (duration.days * 86400 + duration.seconds) * 1_000_000 + duration.microseconds


def _blocks(query: MetricQuery, sources: MetricSources) -> list[MetricContribution]:
    assert sources.calendar is not None
    rows: list[MetricContribution] = []
    zone = ZoneInfo(query.dates.zone)
    by_task = {task["public_id"]: task for task in sources.tasks}
    for offset in range(query.dates.days):
        day = query.dates.first + timedelta(days=offset)
        first, end = day_span(day, zone)
        blocks = [
            (block, span)
            for block in sources.calendar.blocks
            for span in clip((block.block.interval,), first, end)
        ]
        if query.metric_key == "blocks.scheduled_unique":
            for position, span in enumerate(union(span for _, span in blocks)):
                duration = _micros(*span)
                rows.append(
                    MetricContribution(
                        key=f"{day}:{position}",
                        source_id=f"{day}:{position}",
                        source_kind="interval",
                        day=day,
                        value=duration_minutes(duration),
                        duration_us=duration,
                        intervals=(span,),
                        related_ids=tuple(
                            sorted(
                                block.public_id
                                for block, part in blocks
                                if part[0] < span[1] and span[0] < part[1]
                            )
                        ),
                        explanation="Union segment; overlapping block time counted once.",
                    )
                )
        else:
            for block, span in blocks:
                project = by_task[block.action_public_id]["project_public_id"]
                duration = _micros(*span)
                rows.append(
                    MetricContribution(
                        key=f"{block.public_id}:{day}",
                        source_id=block.public_id,
                        source_kind="block",
                        day=day,
                        value=duration_minutes(duration),
                        duration_us=duration,
                        intervals=(span,),
                        label=block.title,
                        project_id=project,
                        area_id=_area(project, sources),
                        revision=block.block.revision,
                        related_ids=(block.action_public_id,),
                        explanation="Retained schedule; completed tasks included. Not actual work.",
                    )
                )
    return rows


def _capacity(
    query: MetricQuery,
    sources: MetricSources,
    now: datetime,
    google_on: bool,
) -> tuple[list[MetricContribution], tuple[tuple[date, str], ...], int]:
    shown = sources.calendar
    assert shown is not None
    zone = ZoneInfo(query.dates.zone)
    if query.metric_key == "workload.planned" and query.dates.first < now.astimezone(zone).date():
        raise ValueError("Planned workload is available for today and future dates only.")
    loads = {
        item.day: item for item in workload(shown, zone, now, google_on=google_on, conflicts={})
    }
    rows: list[MetricContribution] = []
    statuses: list[tuple[date, str]] = []
    missing = 0
    busy = busy_spans(
        (*shown.events, *shown.google_events),
        zone,
        shown.hours.before_minutes,
        after_minutes=shown.hours.after_minutes,
    )
    for day in availability(shown, shown.hours, zone):
        status = (
            shown.google_knowledge.status(day.day, day.day, zone, now)
            if google_on
            else "local calendar only"
        )
        if not day.hours_set:
            status = "working hours not set"
        statuses.append((day.day, status))
        if query.metric_key == "workload.planned":
            load = loads[day.day]
            missing += load.focus_unestimated
            value: Decimal | None = Decimal(load.planned_minutes)
            explanation = (
                f"{load.scheduled_working_minutes} unique scheduled working minutes + "
                f"{load.focus_minutes} known unscheduled focus minutes; "
                f"{load.focus_unestimated} missing estimates."
            )
        else:
            value = Decimal(day.working_minutes - day.busy_minutes) if day.hours_set else None
            explanation = (
                f"{day.working_minutes} working minutes - "
                f"{day.busy_minutes} unioned busy/buffer minutes."
            )
        rows.append(
            MetricContribution(
                key=day.day.isoformat(),
                source_id=day.day.isoformat(),
                source_kind="day",
                day=day.day,
                value=value,
                working_intervals=day.working,
                busy_intervals=clip(busy, *day_span(day.day, zone)),
                related_ids=tuple(
                    block.public_id
                    for block in shown.blocks
                    if query.metric_key == "workload.planned"
                    and block.open
                    and clip((block.block.interval,), *day_span(day.day, zone))
                ),
                explanation=explanation
                + " "
                + status
                + ". Recomputed from current hours and retained events.",
            )
        )
    return rows, tuple(statuses), missing


def _group_key(row: MetricContribution, dimension: MetricDimension) -> str:
    identity = row.project_id if dimension is MetricDimension.PROJECT else row.area_id
    return identity or ("unknown" if row.unknown_group else "unassigned")


def _buckets(
    query: MetricQuery,
    descriptor: MetricDescriptor,
    sources: MetricSources,
    rows: tuple[MetricContribution, ...],
) -> tuple[MetricBucket, ...]:
    empty_unknown = query.metric_key.startswith("custom.")
    if query.group_by is None:
        by_day: dict[date, list[MetricContribution]] = defaultdict(list)
        for row in rows:
            by_day[row.day].append(row)
        days = (
            sorted({row.day for row in rows})
            if not descriptor.daily_series
            else [query.dates.first + timedelta(days=offset) for offset in range(query.dates.days)]
        )
        return tuple(
            MetricBucket(
                key=day.isoformat(),
                label=day.isoformat(),
                value=reduce_contributions(
                    tuple(by_day[day]),
                    descriptor.reducer,
                    empty_unknown=empty_unknown,
                ),
                contribution_keys=tuple(row.key for row in by_day[day]),
            )
            for day in days
        )
    grouped: dict[str, list[MetricContribution]] = defaultdict(list)
    for row in rows:
        grouped[_group_key(row, query.group_by)].append(row)
    names = {
        row["public_id"]: row["name"]
        for row in (
            sources.projects if query.group_by is MetricDimension.PROJECT else sources.areas
        )
    }
    names.update({"unassigned": "Unassigned", "unknown": "Unknown"})
    ordered = sorted(
        grouped,
        key=lambda key: (
            -(reduce_contributions(tuple(grouped[key]), descriptor.reducer) or Decimal(0)),
            key,
        ),
    )
    buckets = [
        MetricBucket(
            key=key,
            label=names.get(key, "Unavailable group"),
            value=reduce_contributions(tuple(grouped[key]), descriptor.reducer),
            contribution_keys=tuple(row.key for row in grouped[key]),
            group_members=(key,),
        )
        for key in ordered[:20]
    ]
    if len(ordered) > 20:
        other = tuple(row for key in ordered[20:] for row in grouped[key])
        buckets.append(
            MetricBucket(
                key="other",
                label="Other",
                value=reduce_contributions(other, descriptor.reducer),
                contribution_keys=tuple(row.key for row in other),
                group_members=tuple(ordered[20:]),
            )
        )
    return tuple(buckets)


def calculate_metric(
    query: MetricQuery, sources: MetricSources, now: datetime, *, google_on: bool
) -> MetricQueryResult:
    descriptor = descriptor_for(query, sources)
    partial = missing = 0
    calendar_status: tuple[tuple[date, str], ...] = ()
    if query.metric_key.startswith("tasks."):
        base_key = (
            (
                "tasks.completions"
                if query.metric_key == "tasks.distinct_completed"
                else query.metric_key
            )
            + query.dates.model_dump_json()
            + now.isoformat()
        )
        if base_key not in sources.prepared:
            sources.prepared[base_key] = _tasks(query, sources, now)
        contributions, partial = cast(
            tuple[list[MetricContribution], int], sources.prepared[base_key]
        )
    elif sources.definition is not None:
        if "logs" not in sources.prepared:
            sources.prepared["logs"] = _logs(query, sources)
        contributions = cast(list[MetricContribution], sources.prepared["logs"])
    elif query.metric_key == "plan.selected_estimates":
        contributions = _selections(sources)
    elif query.metric_key.startswith("blocks."):
        contributions = _blocks(query, sources)
    else:
        contributions, calendar_status, missing = _capacity(query, sources, now, google_on)
    rows = tuple(
        sorted(
            (row for row in contributions if _matches(row, query)),
            key=lambda row: (row.day, row.key),
        )
    )
    if query.metric_key == "plan.selected_estimates":
        missing = sum(row.value is None for row in rows)
    total = reduce_contributions(
        rows, descriptor.reducer, empty_unknown=descriptor.reducer is MetricReducer.LATEST
    )
    if query.metric_key == "capacity.available" and any(row.value is None for row in rows):
        total = None
    latest = max((row.day for row in rows if row.value is not None), default=None)
    observed = {row.day for row in rows if query.dates.first <= row.day <= query.dates.last}
    notes = [descriptor.explanation]
    retained = query.metric_key.startswith("tasks.") and query.metric_key != "tasks.current_overdue"
    if retained:
        notes += [
            "Based on retained records; purged tasks are not included.",
            "Historical group IDs are event snapshots; group names are current labels.",
        ]
    if query.metric_key == "plan.selected_estimates":
        notes.append(
            "Current estimates for retained selected task-days, including completed selections."
        )
    if sources.definition is not None:
        notes.append("Missing observation dates are unknown; explicit zero is retained.")
    coverage = MetricCoverage(
        retained_records=retained,
        partial_tasks=partial,
        missing_estimates=missing,
        missing_observation_dates=query.dates.days - len(observed)
        if sources.definition is not None
        else 0,
        calendar_status=calendar_status,
        notes=tuple(notes),
    )
    buckets = _buckets(query, descriptor, sources, rows)
    fingerprint = hashlib.sha256(
        json.dumps(
            (
                query.model_dump(mode="json"),
                now.isoformat(),
                [row.model_dump(mode="json") for row in rows],
                coverage.model_dump(mode="json"),
                sources.signature,
            ),
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return MetricQueryResult(
        query=query,
        descriptor=descriptor,
        captured_at_utc=now,
        total=total,
        buckets=buckets,
        contributions=rows,
        coverage=coverage,
        fingerprint=fingerprint,
        observation_date=latest if descriptor.reducer is MetricReducer.LATEST else None,
    )
