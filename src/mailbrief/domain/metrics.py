"""Typed metric meaning and query validation, separate from storage and presentation."""

from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Final, Self
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, field_validator, model_validator

from mailbrief.domain.common import DomainModel, known_zone
from mailbrief.domain.metric_entries import MeasurementKind

MAX_METRIC_DAYS: Final = 366


class MetricReducer(StrEnum):
    COUNT = "count"
    DISTINCT = "distinct"
    SUM = "sum"
    LATEST = "latest"
    UNION = "union"


class MetricDimension(StrEnum):
    PROJECT = "project"
    AREA = "area"


class MetricRangeMode(StrEnum):
    INSTANTS = "instants"
    LOCAL_DATES = "local_dates"
    CURRENT = "current"
    FORWARD_PLAN = "forward_plan"


class MetricDescriptor(DomainModel):
    key: str = Field(min_length=1, max_length=100)
    version: int = Field(default=1, ge=1, strict=True)
    label: str = Field(min_length=1, max_length=120)
    unit: str = Field(min_length=1, max_length=32)
    reducer: MetricReducer
    range_mode: MetricRangeMode
    dimensions: tuple[MetricDimension, ...] = ()
    daily_series: bool = True
    explanation: str = Field(min_length=1, max_length=1000)


class MetricDateRange(DomainModel):
    """Inclusive local dates; UTC boundaries are half-open and DST-aware."""

    first: date
    last: date
    zone: str = Field(min_length=1, max_length=64)

    _zone = field_validator("zone")(known_zone)

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if self.last < self.first or (self.last - self.first).days >= MAX_METRIC_DAYS:
            raise ValueError("Choose an ordered range of at most 366 dates.")
        if self.first <= date.min + timedelta(days=1) or self.last >= date.max - timedelta(days=1):
            raise ValueError("Choose dates away from the calendar boundaries.")
        return self

    @property
    def days(self) -> int:
        return (self.last - self.first).days + 1

    def instant_bounds(self) -> tuple[datetime, datetime]:
        zone = ZoneInfo(self.zone)
        return (
            datetime.combine(self.first, time.min, zone).astimezone(UTC),
            datetime.combine(self.last + timedelta(days=1), time.min, zone).astimezone(UTC),
        )

    def previous(self) -> "MetricDateRange":
        try:
            return MetricDateRange(
                first=self.first - timedelta(days=self.days),
                last=self.first - timedelta(days=1),
                zone=self.zone,
            )
        except (OverflowError, ValueError):
            raise ValueError("The previous period is outside the supported calendar.") from None


class MetricQuery(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    metric_key: str = Field(min_length=1, max_length=100)
    definition_version: int = Field(default=1, ge=1, strict=True)
    reducer: MetricReducer
    dates: MetricDateRange
    group_by: MetricDimension | None = None
    project_public_id: str | None = Field(default=None, min_length=36, max_length=36)
    area_public_id: str | None = Field(default=None, min_length=36, max_length=36)


_GROUPS: Final = (MetricDimension.PROJECT, MetricDimension.AREA)
BUILTIN_METRICS: Final[tuple[MetricDescriptor, ...]] = (
    MetricDescriptor(
        key="tasks.created",
        label="Tasks created",
        unit="tasks",
        reducer=MetricReducer.COUNT,
        range_mode=MetricRangeMode.INSTANTS,
        dimensions=_GROUPS,
        explanation="Effective creation events, based on retained task history.",
    ),
    MetricDescriptor(
        key="tasks.completions",
        label="Task completions",
        unit="episodes",
        reducer=MetricReducer.COUNT,
        range_mode=MetricRangeMode.INSTANTS,
        dimensions=_GROUPS,
        explanation="Effective completion episodes. Reopen retains an episode; Undo corrects it.",
    ),
    MetricDescriptor(
        key="tasks.distinct_completed",
        label="Distinct tasks completed",
        unit="tasks",
        reducer=MetricReducer.DISTINCT,
        range_mode=MetricRangeMode.INSTANTS,
        dimensions=_GROUPS,
        explanation="Distinct occurrences with effective completions; daily counts do not add.",
    ),
    MetricDescriptor(
        key="tasks.current_overdue",
        label="Currently overdue",
        unit="tasks",
        reducer=MetricReducer.COUNT,
        range_mode=MetricRangeMode.CURRENT,
        dimensions=_GROUPS,
        daily_series=False,
        explanation="Current open tasks with a passed resolved deadline, as of the refresh time.",
    ),
    MetricDescriptor(
        key="plan.selected_estimates",
        label="Selected workload",
        unit="minutes",
        reducer=MetricReducer.SUM,
        range_mode=MetricRangeMode.LOCAL_DATES,
        dimensions=_GROUPS,
        explanation="Current estimates of retained selections; missing estimates are unknown.",
    ),
    MetricDescriptor(
        key="blocks.scheduled_total",
        label="Scheduled task time",
        unit="minutes",
        reducer=MetricReducer.SUM,
        range_mode=MetricRangeMode.INSTANTS,
        dimensions=_GROUPS,
        explanation="Retained block lengths clipped to range, including overlaps. Not actual work.",
    ),
    MetricDescriptor(
        key="blocks.scheduled_unique",
        label="Occupied task time",
        unit="minutes",
        reducer=MetricReducer.UNION,
        range_mode=MetricRangeMode.INSTANTS,
        explanation="Union of retained task blocks; overlapping time counts once. Not actual work.",
    ),
    MetricDescriptor(
        key="capacity.available",
        label="Available time",
        unit="minutes",
        reducer=MetricReducer.SUM,
        range_mode=MetricRangeMode.LOCAL_DATES,
        explanation="Current hours less busy time and buffers; coverage may be provisional.",
    ),
    MetricDescriptor(
        key="workload.planned",
        label="Planned workload",
        unit="minutes",
        reducer=MetricReducer.SUM,
        range_mode=MetricRangeMode.FORWARD_PLAN,
        explanation="P3 planned workload for today/future dates, with missing estimates disclosed.",
    ),
)


CUSTOM_DIMENSIONS: Final = (MetricDimension.PROJECT,)


def builtin_metric(key: str) -> MetricDescriptor:
    for descriptor in BUILTIN_METRICS:
        if descriptor.key == key:
            return descriptor
    raise ValueError("The metric is unavailable or its definition is unsupported.")


def metric_dimensions(key: str) -> tuple[MetricDimension, ...]:
    """The filters and groupings the metric ``key`` names can take; none for an unknown key."""
    if key.startswith("custom."):
        return CUSTOM_DIMENSIONS
    return next((item.dimensions for item in BUILTIN_METRICS if item.key == key), ())


def is_forward_plan(key: str) -> bool:
    """Whether ``key`` names a metric of today and later dates only (planned workload)."""
    return any(
        item.key == key and item.range_mode is MetricRangeMode.FORWARD_PLAN
        for item in BUILTIN_METRICS
    )


def custom_metric(key: str, label: str, unit: str, kind: MeasurementKind) -> MetricDescriptor:
    """The caller supplies a resolved definition; an arbitrary key grants no source access."""
    return MetricDescriptor(
        key=key,
        label=label,
        unit=unit,
        reducer=MetricReducer.SUM if kind is MeasurementKind.AMOUNT else MetricReducer.LATEST,
        range_mode=MetricRangeMode.LOCAL_DATES,
        dimensions=CUSTOM_DIMENSIONS,
        explanation=(
            "Sum of valid recorded amounts. Missing observations are not measured zeros."
            if kind is MeasurementKind.AMOUNT
            else "Latest observation on or before the end date, with its original observation date."
        ),
    )


def validate_metric_query(query: MetricQuery, descriptor: MetricDescriptor) -> None:
    """Service boundary validation; UI choices never replace these checks."""
    if query.metric_key != descriptor.key or query.definition_version != descriptor.version:
        raise ValueError("The metric definition changed or is unsupported.")
    if query.reducer is not descriptor.reducer:
        raise ValueError("This calculation is not supported by the metric.")
    requested = {
        dimension
        for dimension, active in (
            (MetricDimension.PROJECT, query.project_public_id is not None),
            (MetricDimension.AREA, query.area_public_id is not None),
            (query.group_by, query.group_by is not None),
        )
        if active
    }
    if not requested.issubset(descriptor.dimensions):
        raise ValueError("This metric does not support the selected filters or grouping.")
