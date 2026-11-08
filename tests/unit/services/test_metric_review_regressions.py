"""Review regressions: snapshot isolation, grouping limits, edge dates and concurrent writes."""

import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.events import EventEdit
from mailbrief.domain.metric_queries import duration_minutes, reduce_contributions
from mailbrief.domain.metrics import MetricDimension, MetricQuery
from mailbrief.domain.projects import ProjectEdit
from mailbrief.domain.work_blocks import BlockTime
from mailbrief.domain.working_hours import HoursEdit, WorkingWindow
from mailbrief.services import metrics as metrics_module
from mailbrief.services.actions import ActionService
from mailbrief.services.availability import AvailabilityService
from mailbrief.services.events import EventService
from mailbrief.services.metric_entries import MetricConflictError, MetricEntryService
from mailbrief.services.metrics import MetricService
from mailbrief.services.projects import ProjectService
from mailbrief.services.work_blocks import WorkBlockService
from mailbrief.storage import metric_sources
from mailbrief.storage.database import Database
from mailbrief.storage.metric_sources import MetricQueryTooLargeError, MetricSources
from mailbrief.storage.tables import (
    GoogleCalendarStateTable,
    MetricDefinitionTable,
    MetricEntryChangeTable,
)
from tests.factories import Clock
from tests.unit.services.test_manual_tasks import EDIT
from tests.unit.services.test_metric_entries import INPUT, SPEC, command
from tests.unit.services.test_metrics import DAY, NOW, ZONE, query


async def test_other_category_keeps_exact_membership_and_contributions(database: Database) -> None:
    async with database.session() as session:
        projects, actions = ProjectService(session), ActionService(session, clock=Clock(NOW))
        for index in range(22):
            project = await projects.create_project(ProjectEdit(name=f"Project {index}"))
            await actions.create(EDIT.model_copy(update={"project_public_id": project.public_id}))
    service = MetricService(database, google_on=False, clock=Clock(NOW))
    result = await service.query(query("tasks.created", group_by=MetricDimension.PROJECT))
    assert len(result.buckets) == 21 and result.total == 22
    other = result.buckets[-1]
    assert other.key == "other" and len(other.group_members) == 2
    rows = await service.contributors(result, bucket="other")
    assert {row.project_id for row in rows} == set(other.group_members)
    assert reduce_contributions(rows, result.descriptor.reducer) == other.value == 2


async def test_comparison_holds_one_physical_snapshot_during_a_writer_commit(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with database.session() as session:
        await ActionService(session, clock=Clock(NOW)).create(EDIT)
    original = metric_sources.read_metric_sources
    calls = 0

    async def interleave(
        session: AsyncSession, request: MetricQuery, now: object, *, google_on: bool
    ) -> MetricSources:
        nonlocal calls
        assert isinstance(now, type(NOW))
        sources = await original(session, request, now, google_on=google_on)
        calls += 1
        if calls == 1:
            async with database.session() as writer:
                await ActionService(writer, clock=Clock(NOW - timedelta(days=1))).create(EDIT)
        return sources

    monkeypatch.setattr(metrics_module, "read_metric_sources", interleave)
    service = MetricService(database, google_on=False, clock=Clock(NOW))
    comparison = await service.compare(query("tasks.created"))
    assert comparison.previous is not None and comparison.previous.total == 0
    assert comparison.current.total == 1
    assert (
        await service.query(
            query("tasks.created", DAY - timedelta(days=1), DAY - timedelta(days=1))
        )
    ).total == 1


async def test_resource_limit_refuses_instead_of_returning_a_capped_total(
    database: Database,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with database.session() as session:
        for _ in range(2):
            await ActionService(session, clock=Clock(NOW)).create(EDIT)
    monkeypatch.setattr(metric_sources, "MAX_METRIC_SOURCE_ROWS", 1)
    with pytest.raises(MetricQueryTooLargeError):
        await MetricService(database, google_on=False).query(query("tasks.created"))


async def test_concurrent_edits_and_cancellation_leave_no_duplicate_changes(
    database: Database,
) -> None:
    async with database.session() as session:
        service = MetricEntryService(session)
        definition = await service.define(SPEC, command_id=command())
        entry = await service.create(definition.public_id, INPUT, command_id=command())

    async def edit(value: int) -> object:
        async with database.session() as session:
            return await MetricEntryService(session).mutate(
                entry.public_id,
                1,
                "edit",
                entry=INPUT.model_copy(update={"value_scaled": value}),
                command_id=command(),
            )

    results = await asyncio.gather(edit(10), edit(20), return_exceptions=True)
    assert sum(isinstance(value, MetricConflictError) for value in results) == 1
    async with database.session() as session:
        assert await session.scalar(select(func.count()).select_from(MetricEntryChangeTable)) == 2


async def test_cancelled_commit_rolls_back_record_receipt_and_history(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    with monkeypatch.context() as patch:
        patch.setattr(session, "commit", AsyncMock(side_effect=asyncio.CancelledError))
        with pytest.raises(asyncio.CancelledError):
            await MetricEntryService(session).define(SPEC, command_id=command())
    assert await session.scalar(select(func.count()).select_from(MetricDefinitionTable)) == 0


async def test_successful_empty_calendar_coverage_and_staleness(database: Database) -> None:
    clock = Clock(NOW)
    async with database.session() as session:
        await AvailabilityService(session).save_hours(
            HoursEdit(days=((WorkingWindow(start_minute=540, end_minute=1020),),) * 7), 0
        )
        session.add(
            GoogleCalendarStateTable(
                id=1,
                generation=command(),
                list_succeeded=True,
                listed_at_utc=NOW,
                first_day=DAY,
                last_day=DAY,
                time_zone=ZONE.key,
            )
        )
        await session.commit()
    service = MetricService(database, google_on=True, clock=clock)
    known = await service.query(query("capacity.available"))
    assert known.total == 480 and not known.coverage.provisional
    clock.advance(minutes=16)
    assert (await service.query(query("capacity.available"))).coverage.provisional


async def test_capacity_reads_buffer_crossing_the_range_start(database: Database) -> None:
    midnight = NOW.replace(hour=5, minute=0)  # Toronto midnight.
    async with database.session() as session:
        await AvailabilityService(session).save_hours(
            HoursEdit(
                days=((WorkingWindow(start_minute=0, end_minute=60),),) * 7,
                buffer_before_minutes=0,
                buffer_after_minutes=30,
            ),
            0,
        )
        await EventService(session).create(
            EventEdit(
                title="Prior-day event",
                all_day=False,
                starts_at_utc=midnight - timedelta(hours=1),
                ends_at_utc=midnight,
                time_zone=ZONE.key,
            )
        )
        task = await ActionService(session).create(EDIT)
        await WorkBlockService(session).schedule(
            task.public_id,
            BlockTime(
                starts_at_utc=midnight + timedelta(minutes=30),
                ends_at_utc=midnight + timedelta(minutes=45),
                time_zone=ZONE.key,
            ),
        )
    result = await MetricService(database, google_on=False, clock=Clock(NOW)).query(
        query("capacity.available")
    )
    assert result.total == 30
    assert result.contributions[0].working_intervals
    assert result.contributions[0].busy_intervals
    assert not result.contributions[0].related_ids  # Task blocks are not capacity subtractions.


async def test_inconsistent_change_operation_is_rejected_before_undo(session: AsyncSession) -> None:
    service = MetricEntryService(session)
    definition = await service.define(SPEC, command_id=command())
    entry = await service.create(definition.public_id, INPUT, command_id=command())
    await service.mutate(
        entry.public_id,
        1,
        "edit",
        entry=INPUT.model_copy(update={"value_scaled": 3500}),
        command_id=command(),
    )
    change = await session.scalar(
        select(MetricEntryChangeTable).where(MetricEntryChangeTable.revision == 2)
    )
    assert change is not None
    change.operation = "delete"  # The state still says live; never trust this as an Undo source.
    await session.commit()
    with pytest.raises(ValueError, match="history"):
        await service.mutate(entry.public_id, 2, "undo", command_id=command())


async def test_fractional_scheduled_minutes_reconcile_through_integer_microseconds(
    database: Database,
) -> None:
    async with database.session() as session:
        task = await ActionService(session).create(EDIT)
        await WorkBlockService(session).schedule(
            task.public_id,
            BlockTime(
                starts_at_utc=NOW,
                ends_at_utc=NOW + timedelta(seconds=330, microseconds=1),
                time_zone=ZONE.key,
            ),
        )
    result = await MetricService(database, google_on=False, clock=Clock(NOW)).query(
        query("blocks.scheduled_total")
    )
    assert result.total == duration_minutes(330_000_001)
    assert result.contributions[0].duration_us == 330_000_001


async def test_logged_comparison_has_exact_percentage(database: Database) -> None:
    async with database.session() as session:
        service = MetricEntryService(session)
        definition = await service.define(SPEC, command_id=command())
        await service.create(
            definition.public_id,
            INPUT.model_copy(update={"value_scaled": 100, "local_date": DAY - timedelta(days=1)}),
            command_id=command(),
        )
        await service.create(
            definition.public_id,
            INPUT.model_copy(update={"value_scaled": 150, "local_date": DAY}),
            command_id=command(),
        )
    result = await MetricService(database, google_on=False, clock=Clock(NOW)).compare(
        query(f"custom.{definition.public_id}.amount")
    )
    assert result.difference == Decimal("0.50") and result.percent_change == 50


async def test_programmatic_query_copy_cannot_bypass_date_limits(database: Database) -> None:
    request = query("tasks.created")
    bad = request.model_copy(
        update={"dates": request.dates.model_copy(update={"last": DAY + timedelta(days=400)})}
    )
    service = MetricService(database, google_on=False, clock=Clock(NOW))
    for call in (service.query, service.compare):
        with pytest.raises(ValueError, match="366"):
            await call(bad)


async def test_database_failures_never_expose_owner_values(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import AsyncMock

    from sqlalchemy.exc import IntegrityError

    from mailbrief.services.metric_entries import MetricStorageError

    failure = IntegrityError(
        "Synthetic SQL", {"name": "private owner value"}, Exception("synthetic")
    )
    with monkeypatch.context() as patch:
        patch.setattr(session, "commit", AsyncMock(side_effect=failure))
        with pytest.raises(MetricStorageError) as error:
            await MetricEntryService(session).define(SPEC, command_id=command())
    assert "private owner value" not in str(error.value)
    assert error.value.__suppress_context__
    assert await session.scalar(select(func.count()).select_from(MetricDefinitionTable)) == 0
