"""Consistent offline metric queries, comparisons and stale-safe contributing records."""

import asyncio
from collections.abc import Callable
from datetime import datetime
from decimal import localcontext
from zoneinfo import ZoneInfo

from sqlalchemy import text

from mailbrief.domain.common import normalize_utc, utc_now
from mailbrief.domain.dashboards import TargetScope, WidgetTarget
from mailbrief.domain.metric_queries import (
    MetricComparison,
    MetricContribution,
    MetricQueryResult,
    MetricTargetResult,
)
from mailbrief.domain.metrics import (
    MetricQuery,
    MetricRangeMode,
    builtin_metric,
    validate_metric_query,
)
from mailbrief.services.metric_calculations import calculate_metric
from mailbrief.storage.database import Database
from mailbrief.storage.metric_sources import read_metric_sources


class MetricDataError(ValueError):
    def __init__(self) -> None:
        super().__init__(
            "Saved metric source data could not be validated. Review the source records."
        )


class MetricResultChangedError(ValueError):
    def __init__(self) -> None:
        super().__init__(
            "Metric data changed. Refresh the result and contributing records together."
        )


def _validated_query(query: MetricQuery, now: datetime) -> MetricQuery:
    # Revalidate even a caller's model_copy: updates cannot bypass query caps.
    query = MetricQuery.model_validate(query.model_dump())
    if not query.metric_key.startswith("custom."):
        descriptor = builtin_metric(query.metric_key)
        validate_metric_query(query, descriptor)
        if (
            descriptor.range_mode is MetricRangeMode.FORWARD_PLAN
            and query.dates.first < now.astimezone(ZoneInfo(query.dates.zone)).date()
        ):
            raise ValueError("Planned workload supports today and future dates only.")
    return query


class MetricService:
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

    async def query(
        self, query: MetricQuery, *, captured_at: datetime | None = None
    ) -> MetricQueryResult:
        now = normalize_utc(self.clock() if captured_at is None else captured_at)
        query = _validated_query(query, now)
        async with self.database.session() as session:
            await session.execute(
                text("BEGIN")
            )  # Physical SQLite read snapshot, not autobegin alone.
            sources = await read_metric_sources(session, query, now, google_on=self.google_on)
        try:
            return await asyncio.to_thread(
                calculate_metric, query, sources, now, google_on=self.google_on
            )
        except (ValueError, KeyError, TypeError, OverflowError):
            raise MetricDataError() from None

    async def compare(self, query: MetricQuery) -> MetricComparison:
        now = normalize_utc(self.clock())
        query = _validated_query(query, now)
        previous = query.model_copy(update={"dates": query.dates.previous()})
        async with self.database.session() as session:
            await session.execute(text("BEGIN"))
            primary_sources = await read_metric_sources(
                session, query, now, google_on=self.google_on
            )
            if query.metric_key in {"tasks.current_overdue", "workload.planned"}:
                prior_sources = None
            else:
                prior_sources = await read_metric_sources(
                    session, previous, now, google_on=self.google_on
                )
        try:
            current = await asyncio.to_thread(
                calculate_metric, query, primary_sources, now, google_on=self.google_on
            )
            if prior_sources is None:
                return MetricComparison(
                    current=current,
                    explanation="Historical comparison needs a supported historical source.",
                )
            prior = await asyncio.to_thread(
                calculate_metric, previous, prior_sources, now, google_on=self.google_on
            )
        except (ValueError, KeyError, TypeError, OverflowError):
            raise MetricDataError() from None
        with localcontext() as context:
            context.prec = 60
            difference = (
                None
                if current.total is None or prior.total is None
                else current.total - prior.total
            )
            percent = (
                None
                if difference is None or prior.total is None or prior.total <= 0
                else difference / prior.total * 100
            )
        explanation = "Previous period has the same number of local dates."
        if query.dates.last >= now.astimezone(ZoneInfo(query.dates.zone)).date():
            explanation += " Current period is in progress; compared with a full previous period."
        if current.coverage.provisional or prior.coverage.provisional:
            explanation += " Partial or provisional source coverage applies to this comparison."
        return MetricComparison(
            current=current,
            previous=prior,
            difference=difference,
            percent_change=percent,
            explanation=explanation,
        )

    async def contributors(
        self,
        result: MetricQueryResult,
        *,
        bucket: str | None = None,
        after: str | None = None,
        limit: int = 100,
    ) -> tuple[MetricContribution, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("Choose a page size between 1 and 200.")
        fresh = await self.query(result.query, captured_at=result.captured_at_utc)
        if fresh.fingerprint != result.fingerprint:
            raise MetricResultChangedError()
        keys: set[str] | None = None
        if bucket is not None:
            selected = next((item for item in fresh.buckets if item.key == bucket), None)
            if selected is None:
                raise ValueError("That metric bucket is unavailable.")
            keys = set(selected.contribution_keys)
        rows = tuple(row for row in fresh.contributions if keys is None or row.key in keys)
        if after is not None:
            found = next((index for index, row in enumerate(rows) if row.key == after), None)
            if found is None:
                raise ValueError("The contributing-record cursor is unavailable.")
            rows = rows[found + 1 :]
        return rows[:limit]


def assess_target(result: MetricQueryResult, target: WidgetTarget) -> MetricTargetResult:
    with localcontext() as context:
        context.prec = 60
        if target.scope is TargetScope.DAILY:
            if result.query.group_by is not None or not result.descriptor.daily_series:
                raise ValueError("Daily targets require an ungrouped daily metric.")
            return MetricTargetResult(
                target=target,
                bucket_differences=tuple(
                    (bucket.key, None if bucket.value is None else bucket.value - target.value)
                    for bucket in result.buckets
                ),
            )
        return MetricTargetResult(
            target=target, difference=None if result.total is None else result.total - target.value
        )
