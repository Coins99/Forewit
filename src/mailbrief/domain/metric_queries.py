"""Explainable metric results and exact contribution reducers, independent of Qt/SQL."""

from datetime import UTC, date, datetime
from decimal import Decimal, localcontext

from pydantic import ConfigDict, Field, field_validator

from mailbrief.domain.common import DomainModel, normalize_utc
from mailbrief.domain.dashboards import WidgetTarget
from mailbrief.domain.intervals import Interval
from mailbrief.domain.metrics import MetricDescriptor, MetricQuery, MetricReducer


class MetricContribution(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    key: str
    source_id: str
    source_kind: str
    day: date
    value: Decimal | None = Field(repr=False)
    label: str = Field(default="", repr=False)
    project_id: str | None = None
    area_id: str | None = None
    unknown_group: bool = False
    revision: int = 0
    occurred_at_utc: datetime | None = None
    duration_us: int | None = None
    intervals: tuple[Interval, ...] = ()
    working_intervals: tuple[Interval, ...] = ()
    busy_intervals: tuple[Interval, ...] = ()
    related_ids: tuple[str, ...] = ()
    explanation: str = Field(default="", repr=False)

    @field_validator("occurred_at_utc")
    @classmethod
    def aware(cls, value: datetime | None) -> datetime | None:
        return None if value is None else normalize_utc(value)


class MetricBucket(DomainModel):
    key: str
    label: str = Field(repr=False)
    value: Decimal | None = Field(repr=False)
    contribution_keys: tuple[str, ...] = ()
    group_members: tuple[str, ...] = ()


class MetricCoverage(DomainModel):
    retained_records: bool = False
    partial_tasks: int = 0
    missing_estimates: int = 0
    missing_observation_dates: int = 0
    calendar_status: tuple[tuple[date, str], ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def provisional(self) -> bool:
        return bool(
            self.partial_tasks
            or self.missing_estimates
            or any(
                state not in {"local calendar only", "fresh shown-calendar coverage"}
                for _, state in self.calendar_status
            )
        )


class MetricQueryResult(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    query: MetricQuery
    descriptor: MetricDescriptor
    captured_at_utc: datetime
    total: Decimal | None = Field(repr=False)
    buckets: tuple[MetricBucket, ...]
    contributions: tuple[MetricContribution, ...] = Field(repr=False)
    coverage: MetricCoverage
    fingerprint: str
    observation_date: date | None = None

    _time = field_validator("captured_at_utc")(normalize_utc)


class MetricComparison(DomainModel):
    current: MetricQueryResult
    previous: MetricQueryResult | None = None
    difference: Decimal | None = Field(default=None, repr=False)
    percent_change: Decimal | None = Field(default=None, repr=False)
    explanation: str = ""


class MetricTargetResult(DomainModel):
    target: WidgetTarget
    difference: Decimal | None = Field(default=None, repr=False)
    bucket_differences: tuple[tuple[str, Decimal | None], ...] = Field(default=(), repr=False)


def duration_minutes(microseconds: int) -> Decimal:
    # Keep exact integer microseconds in contributors; repeating minute fractions are
    # represented at high precision only after summing, never rounded per source.
    with localcontext() as context:
        context.prec = 50
        return Decimal(microseconds) / Decimal(60_000_000)


def reduce_contributions(
    rows: tuple[MetricContribution, ...],
    reducer: MetricReducer,
    *,
    empty_unknown: bool = False,
) -> Decimal | None:
    valid = [row for row in rows if row.value is not None]
    if not valid:
        return None if empty_unknown or rows else Decimal(0)
    if reducer is MetricReducer.DISTINCT:
        return Decimal(len({row.source_id for row in valid}))
    if reducer is MetricReducer.LATEST:
        latest = max(
            valid,
            key=lambda row: (
                row.day,
                row.occurred_at_utc or datetime.min.replace(tzinfo=UTC),
                row.source_id,
            ),
        )
        return latest.value
    if any(row.duration_us is not None for row in valid):
        return duration_minutes(sum(row.duration_us or 0 for row in valid))
    with localcontext() as context:
        context.prec = 60
        return sum((row.value for row in valid if row.value is not None), Decimal(0))
