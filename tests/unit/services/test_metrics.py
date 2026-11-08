"""P4 aggregate semantics over real local services, with no accounts or providers."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, select

from mailbrief.domain.dashboards import TargetScope, WidgetTarget
from mailbrief.domain.events import EventEdit
from mailbrief.domain.metric_entries import MeasurementKind, MetricDefinitionSpec, MetricEntryInput
from mailbrief.domain.metric_queries import reduce_contributions
from mailbrief.domain.metrics import (
    MetricDateRange,
    MetricDimension,
    MetricQuery,
    MetricReducer,
    builtin_metric,
)
from mailbrief.domain.projects import AreaEdit, ProjectEdit
from mailbrief.domain.work_blocks import BlockTime
from mailbrief.domain.working_hours import HoursEdit, WorkingWindow
from mailbrief.services.actions import ActionService
from mailbrief.services.availability import AvailabilityService
from mailbrief.services.day_plans import DayPlanService
from mailbrief.services.events import EventService
from mailbrief.services.metric_entries import MetricEntryService
from mailbrief.services.metrics import (
    MetricDataError,
    MetricResultChangedError,
    MetricService,
    assess_target,
)
from mailbrief.services.projects import ProjectService
from mailbrief.services.work_blocks import WorkBlockService
from mailbrief.storage.database import Database
from mailbrief.storage.tables import ActionActivityTable, ActionTable
from tests.factories import Clock
from tests.unit.services.test_manual_tasks import EDIT

DAY = date(2026, 11, 8)
NOW = datetime(2026, 11, 8, 15, tzinfo=UTC)
ZONE = ZoneInfo("America/Toronto")


def query(key: str, first: date = DAY, last: date = DAY, **changes: object) -> MetricQuery:
    reducer = (
        builtin_metric(key).reducer
        if not key.startswith("custom.")
        else (MetricReducer.LATEST if key.endswith(".point") else MetricReducer.SUM)
    )
    return MetricQuery.model_validate(
        {
            "metric_key": key,
            "reducer": reducer,
            "dates": MetricDateRange(first=first, last=last, zone=ZONE.key),
            **changes,
        }
    )


def command() -> str:
    return str(uuid4())


async def test_completion_episodes_event_grouping_and_out_of_range_correction(
    database: Database,
) -> None:
    clock = Clock(NOW)
    async with database.session() as session:
        projects = ProjectService(session, clock=clock)
        area = await projects.create_area(AreaEdit(name="Area"))
        first = await projects.create_project(
            ProjectEdit(name="First", area_public_id=area.public_id)
        )
        second = await projects.create_project(ProjectEdit(name="Second"))
        actions = ActionService(session, clock=clock)
        spec = EDIT.model_copy(update={"project_public_id": first.public_id})
        task = await actions.create(spec)
        task = await actions.complete(task.public_id, task.revision)
        task = await actions.reopen(task.public_id, task.revision)
        task = await actions.save(
            task.public_id,
            task.revision,
            spec.model_copy(update={"project_public_id": second.public_id}),
        )
        clock.advance(days=1)
        task = await actions.complete(task.public_id, task.revision)
    service = MetricService(database, google_on=False, clock=clock)
    request = query("tasks.completions", DAY, DAY + timedelta(days=1))
    result = await service.query(request)
    assert result.total == 2
    assert [bucket.value for bucket in result.buckets] == [1, 1]
    grouped = await service.query(request.model_copy(update={"group_by": MetricDimension.PROJECT}))
    assert {bucket.key: bucket.value for bucket in grouped.buckets} == {
        first.public_id: 1,
        second.public_id: 1,
    }
    filtered = await service.query(request.model_copy(update={"area_public_id": area.public_id}))
    assert filtered.total == 1
    distinct = await service.query(query("tasks.distinct_completed", DAY, DAY + timedelta(days=1)))
    assert distinct.total == 1
    assert sum(bucket.value or 0 for bucket in distinct.buckets) == 2
    clock.advance(days=1)
    async with database.session() as session:
        task = await ActionService(session, clock=clock).undo_status(task.public_id, task.revision)
    corrected = await service.query(request)
    assert corrected.total == 1
    assert corrected.contributions[0].project_id == first.public_id
    assert corrected.coverage.retained_records
    assert (await service.query(query("tasks.created"))).total == 1
    rows = await service.contributors(corrected, bucket=DAY.isoformat())
    assert reduce_contributions(rows, corrected.descriptor.reducer) == corrected.total


async def test_partial_malformed_soft_deleted_and_purged_history(database: Database) -> None:
    async with database.session() as session:
        task = await ActionService(session, clock=Clock(NOW)).create(EDIT)
        row = await session.scalar(
            select(ActionTable).where(ActionTable.public_id == task.public_id)
        )
        assert row is not None
        row.deleted_at_utc = NOW
        await session.commit()
    service = MetricService(database, google_on=False, clock=Clock(NOW))
    assert (await service.query(query("tasks.created"))).total == 1
    async with database.session() as session:
        event = await session.scalar(select(ActionActivityTable))
        assert event is not None
        event.legacy = True
        await session.commit()
    partial = await service.query(query("tasks.created", group_by=MetricDimension.PROJECT))
    assert partial.coverage.partial_tasks == 1
    assert partial.buckets[0].label == "Unknown"
    async with database.session() as session:
        event = await session.scalar(select(ActionActivityTable))
        assert event is not None
        event.kind = "undo"
        event.undo_of_revision = 1
        await session.commit()
    with pytest.raises(MetricDataError):
        await service.query(query("tasks.completions"))
    async with database.session() as session:
        await session.execute(delete(ActionTable))
        await session.commit()
    assert (await service.query(query("tasks.created"))).total == 0


async def test_more_than_200_events_and_stale_detail_generation(database: Database) -> None:
    async with database.session() as session:
        actions = ActionService(session, clock=Clock(NOW))
        task = await actions.create(EDIT)
        for _ in range(101):
            task = await actions.complete(task.public_id, task.revision)
            task = await actions.reopen(task.public_id, task.revision)
    service = MetricService(database, google_on=False, clock=Clock(NOW))
    result = await service.query(query("tasks.completions"))
    assert result.total == 101
    page = await service.contributors(result, limit=100)
    tail = await service.contributors(result, after=page[-1].key, limit=100)
    assert len(page) == 100 and len(tail) == 1
    async with database.session() as session:
        await ActionService(session, clock=Clock(NOW)).complete(task.public_id, task.revision)
    with pytest.raises(MetricResultChangedError):
        await service.contributors(result)


@pytest.mark.parametrize("kind", [MeasurementKind.AMOUNT, MeasurementKind.POINT_VALUE])
async def test_custom_values_corrections_gaps_and_pre_range_observation(
    database: Database, kind: MeasurementKind
) -> None:
    clock = Clock(NOW)
    suffix = "amount" if kind is MeasurementKind.AMOUNT else "point"
    async with database.session() as session:
        logs = MetricEntryService(session, clock=clock)
        definition = await logs.define(
            MetricDefinitionSpec(name="Metric", unit="units", kind=kind, scale=2),
            command_id=command(),
        )
        prior = await logs.create(
            definition.public_id,
            MetricEntryInput(value_scaled=100, local_date=DAY - timedelta(days=1), zone=ZONE.key),
            command_id=command(),
        )
        value = await logs.create(
            definition.public_id,
            MetricEntryInput(value_scaled=250, local_date=DAY, zone=ZONE.key),
            command_id=command(),
        )
        # Later insertion on the same local date wins for point values.
        clock.advance(seconds=1)
        zero = await logs.create(
            definition.public_id,
            MetricEntryInput(value_scaled=0, local_date=DAY, zone=ZONE.key),
            command_id=command(),
        )
    service = MetricService(database, google_on=False, clock=clock)
    request = query(f"custom.{definition.public_id}.{suffix}", DAY, DAY + timedelta(days=1))
    result = await service.query(request)
    assert result.total == (Decimal("2.50") if kind is MeasurementKind.AMOUNT else 0)
    assert result.buckets[1].value is None
    assert result.coverage.missing_observation_dates == 1
    async with database.session() as session:
        logs = MetricEntryService(session, clock=clock)
        await logs.mutate(
            value.public_id,
            1,
            "edit",
            entry=MetricEntryInput(value_scaled=350, local_date=DAY, zone=ZONE.key),
            command_id=command(),
        )
        await logs.mutate(zero.public_id, 1, "delete", command_id=command())
    revised = await service.query(request)
    assert revised.total == Decimal("3.50")
    assert revised.fingerprint != result.fingerprint
    if kind is MeasurementKind.POINT_VALUE:
        before = await service.query(
            query(request.metric_key, DAY - timedelta(days=1), DAY - timedelta(days=1))
        )
        assert before.total == 1 and before.observation_date == prior.local_date
        carried = await service.query(
            query(request.metric_key, DAY + timedelta(days=2), DAY + timedelta(days=2))
        )
        assert carried.total == Decimal("3.50")
        assert carried.observation_date == DAY and carried.buckets[0].value is None


async def test_selected_and_scheduled_workload_do_not_double_count(database: Database) -> None:
    clock = Clock(NOW)
    async with database.session() as session:
        actions = ActionService(session, clock=clock)
        first = await actions.create(EDIT)
        second = await actions.create(EDIT.model_copy(update={"estimate_minutes": None}))
        plans = DayPlanService(session, clock=clock)
        plan = await plans.select(DAY, ZONE, first.public_id, 0)
        await plans.select(DAY, ZONE, second.public_id, plan.revision)
        await AvailabilityService(session).save_hours(
            HoursEdit(days=((WorkingWindow(start_minute=540, end_minute=1020),),) * 7), 0
        )
        blocks = WorkBlockService(session, clock=clock)
        await blocks.schedule(
            first.public_id,
            BlockTime(starts_at_utc=NOW, ends_at_utc=NOW + timedelta(hours=1), time_zone=ZONE.key),
        )
        await blocks.schedule(
            first.public_id,
            BlockTime(
                starts_at_utc=NOW + timedelta(minutes=30),
                ends_at_utc=NOW + timedelta(minutes=90),
                time_zone=ZONE.key,
            ),
        )
    service = MetricService(database, google_on=False, clock=clock)
    selected = await service.query(query("plan.selected_estimates"))
    assert selected.total == 90 and selected.coverage.missing_estimates == 1
    scheduled = await service.query(query("blocks.scheduled_total"))
    unique = await service.query(query("blocks.scheduled_unique"))
    assert scheduled.total == 120 and unique.total == 90
    assert len(unique.contributions[0].related_ids) == 2
    planned = await service.query(query("workload.planned"))
    assert planned.total == 90 and planned.coverage.missing_estimates == 1
    assert planned.coverage.provisional
    async with database.session() as session:
        await ActionService(session, clock=clock).complete(first.public_id, first.revision)
    # Historical retained schedules include completed tasks; forward workload does not.
    assert (await service.query(query("blocks.scheduled_total"))).total == 120
    assert (await service.query(query("workload.planned"))).total == 0
    assert (await service.query(query("plan.selected_estimates"))).total == 90


async def test_available_time_buffers_local_scope_and_unknown_external_knowledge(
    database: Database,
) -> None:
    clock = Clock(NOW)
    async with database.session() as session:
        hours = HoursEdit(
            days=((WorkingWindow(start_minute=540, end_minute=1020),),) * 7,
            buffer_before_minutes=15,
            buffer_after_minutes=30,
        )
        await AvailabilityService(session).save_hours(hours, 0)
        await EventService(session, clock=clock).create(
            EventEdit(
                title="Busy",
                all_day=False,
                starts_at_utc=NOW,
                ends_at_utc=NOW + timedelta(hours=1),
                time_zone=ZONE.key,
            )
        )
    service = MetricService(database, google_on=False, clock=clock)
    available = await service.query(query("capacity.available"))
    assert available.total == 375
    assert not available.coverage.provisional
    uncertain = await MetricService(database, google_on=True, clock=clock).query(
        query("capacity.available")
    )
    assert uncertain.total == available.total and uncertain.coverage.provisional
    historical = await MetricService(database, google_on=True, clock=clock).query(
        query("capacity.available", DAY - timedelta(days=1), DAY - timedelta(days=1))
    )
    assert historical.coverage.provisional  # Old-day P3 flags are not proof of coverage.


async def test_unset_hours_current_overdue_and_query_validation(database: Database) -> None:
    async with database.session() as session:
        task = await ActionService(session, clock=Clock(NOW)).create(EDIT)
        row = await session.scalar(
            select(ActionTable).where(ActionTable.public_id == task.public_id)
        )
        assert row is not None
        row.deadline_origin = "owner"
        row.deadline_precision, row.deadline_date, row.deadline_timezone = (
            "date",
            DAY - timedelta(days=1),
            ZONE.key,
        )
        await session.commit()
    service = MetricService(database, google_on=False, clock=Clock(NOW))
    overdue = await service.query(
        query("tasks.current_overdue", DAY - timedelta(days=10), DAY - timedelta(days=10))
    )
    assert overdue.total == 1 and overdue.contributions[0].day == DAY
    assert (await service.query(query("capacity.available"))).total is None
    assert (await service.compare(query("tasks.current_overdue"))).previous is None
    for request in (
        query("capacity.available", group_by="project"),
        query("tasks.created", reducer="sum"),
    ):
        with pytest.raises(ValueError):
            await service.query(request)
    with pytest.raises(ValueError, match="today and future"):
        await service.query(query("workload.planned", DAY - timedelta(days=1), DAY))


async def test_targets_comparison_and_detail_validation(database: Database) -> None:
    async with database.session() as session:
        await ActionService(session, clock=Clock(NOW)).create(EDIT)
    service = MetricService(database, google_on=False, clock=Clock(NOW))
    request = query("tasks.created")
    comparison = await service.compare(request)
    assert comparison.difference == 1 and comparison.percent_change is None
    assert "in progress" in comparison.explanation
    result = comparison.current
    assert assess_target(result, WidgetTarget(value=Decimal(3))).difference == -2
    assert assess_target(
        result, WidgetTarget(value=Decimal(2), scope=TargetScope.DAILY)
    ).bucket_differences == ((DAY.isoformat(), Decimal(-1)),)
    with pytest.raises(ValueError):
        assess_target(
            await service.query(query("tasks.current_overdue")),
            WidgetTarget(value=Decimal(2), scope=TargetScope.DAILY),
        )
    for kwargs in ({"limit": 0}, {"after": "missing"}, {"bucket": "missing"}):
        with pytest.raises(ValueError):
            await service.contributors(result, **kwargs)


async def test_custom_bad_keys_missing_definitions_and_empty_logs(database: Database) -> None:
    service = MetricService(database, google_on=False, clock=Clock(NOW))
    for key in ("custom.bad", f"custom.{command()}.amount"):
        with pytest.raises(ValueError):
            await service.query(query(key))
    async with database.session() as session:
        definition = await MetricEntryService(session).define(
            MetricDefinitionSpec(name="Balance", unit="CAD", kind=MeasurementKind.POINT_VALUE),
            command_id=command(),
        )
    assert (await service.query(query(f"custom.{definition.public_id}.point"))).total is None
    with pytest.raises(MetricDataError):
        await service.query(query(f"custom.{definition.public_id}.amount"))
