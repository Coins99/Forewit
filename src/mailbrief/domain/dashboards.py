"""Versioned dashboard configuration. Results are never saved in a layout."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final, Self
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, field_validator, model_validator

from mailbrief.domain.common import DomainModel
from mailbrief.domain.metric_entries import exact_decimal_input
from mailbrief.domain.metrics import (
    MetricDateRange,
    MetricDescriptor,
    MetricDimension,
    MetricQuery,
    MetricRangeMode,
    is_forward_plan,
    metric_dimensions,
    validate_metric_query,
)

MAX_DASHBOARD_WIDGETS: Final = 12


class ChartKind(StrEnum):
    NUMBER = "number"
    LINE = "line"
    BAR = "bar"


class WidgetSize(StrEnum):
    SMALL = "small"
    WIDE = "wide"
    FULL = "full"


class TargetScope(StrEnum):
    RANGE = "range"
    DAILY = "daily"


class WidgetTarget(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    value: Decimal = Field(allow_inf_nan=False, max_digits=22, decimal_places=6, repr=False)
    scope: TargetScope = TargetScope.RANGE

    _exact_value = field_validator("value", mode="before")(exact_decimal_input)


class WidgetSpec(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    public_id: str = Field(min_length=36, max_length=36)
    title: str = Field(min_length=1, max_length=120, repr=False)
    query: MetricQuery
    chart: ChartKind
    size: WidgetSize = WidgetSize.SMALL
    target: WidgetTarget | None = None
    previous_period: bool = False
    use_global_range: bool = True


class DashboardLayout(DomainModel):
    """Tuple order is layout order; one dashboard revision guards every layout mutation."""

    model_config = ConfigDict(hide_input_in_errors=True)

    schema_version: int = Field(default=1, ge=1, le=1, strict=True)
    widgets: tuple[WidgetSpec, ...] = Field(default=(), max_length=MAX_DASHBOARD_WIDGETS)

    @field_validator("widgets")
    @classmethod
    def unique_widgets(cls, value: tuple[WidgetSpec, ...]) -> tuple[WidgetSpec, ...]:
        if len({item.public_id for item in value}) != len(value):
            raise ValueError("Each widget must have its own identity.")
        return value


class SavedDashboard(DomainModel):
    revision: int = Field(ge=1, strict=True)
    layout: DashboardLayout


def resolved_widget_query(
    widget: WidgetSpec,
    dates: MetricDateRange,
    *,
    project_public_id: str | None = None,
    area_public_id: str | None = None,
    now: datetime | None = None,
) -> MetricQuery:
    """One inheritance rule for the editor preview and the saved dashboard.

    A dashboard filter reaches only a metric that can take it: the others keep their
    whole value instead of being refused. With ``now``, a forward plan (planned workload)
    drops the part of its range before today in the range's zone, so a widget on the
    dashboard's dates, or on its own, stays valid as days pass.
    """
    key = widget.query.metric_key
    dimensions = metric_dimensions(key)
    chosen = dates if widget.use_global_range else widget.query.dates
    if now is not None and is_forward_plan(key):
        today = now.astimezone(ZoneInfo(chosen.zone)).date()
        if chosen.first < today <= chosen.last:
            chosen = MetricDateRange(first=today, last=chosen.last, zone=chosen.zone)
    return MetricQuery.model_validate(
        {
            **widget.query.model_dump(),
            "dates": chosen,
            "project_public_id": widget.query.project_public_id
            or (project_public_id if MetricDimension.PROJECT in dimensions else None),
            "area_public_id": widget.query.area_public_id
            or (area_public_id if MetricDimension.AREA in dimensions else None),
        }
    )


class ValidatedWidget(DomainModel):
    """Pair a saved query with its resolved descriptor before preview or persistence."""

    widget: WidgetSpec
    descriptor: MetricDescriptor

    @model_validator(mode="after")
    def compatible(self) -> Self:
        widget, descriptor = self.widget, self.descriptor
        validate_metric_query(widget.query, descriptor)
        if widget.chart is ChartKind.LINE and widget.query.group_by is not None:
            raise ValueError("Use bars for category comparisons.")
        if (
            not descriptor.daily_series
            and widget.chart is not ChartKind.NUMBER
            and (widget.chart is not ChartKind.BAR or widget.query.group_by is None)
        ):
            raise ValueError("This metric supports a current card or category bars only.")
        if widget.chart is ChartKind.NUMBER and widget.query.group_by is not None:
            raise ValueError("A number card needs an ungrouped total.")
        if widget.previous_period and descriptor.range_mode is MetricRangeMode.CURRENT:
            raise ValueError("Current-state metrics have no historical comparison.")
        if widget.target is not None and widget.target.scope is TargetScope.DAILY:
            if widget.chart is ChartKind.NUMBER or widget.query.group_by is not None:
                raise ValueError("Daily targets need an ungrouped daily chart.")
            if not descriptor.daily_series:
                raise ValueError("This metric has no daily target series.")
        return self
