"""Versioned dashboard configuration. Results are never saved in a layout."""

from decimal import Decimal
from enum import StrEnum
from typing import Final, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from mailbrief.domain.common import DomainModel
from mailbrief.domain.metric_entries import exact_decimal_input
from mailbrief.domain.metrics import (
    MetricDescriptor,
    MetricQuery,
    MetricRangeMode,
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


class DashboardLayout(DomainModel):
    """Tuple order is layout order; one dashboard revision guards every layout mutation."""

    schema_version: int = Field(default=1, ge=1, le=1, strict=True)
    widgets: tuple[WidgetSpec, ...] = Field(default=(), max_length=MAX_DASHBOARD_WIDGETS)

    @field_validator("widgets")
    @classmethod
    def unique_widgets(cls, value: tuple[WidgetSpec, ...]) -> tuple[WidgetSpec, ...]:
        if len({item.public_id for item in value}) != len(value):
            raise ValueError("Each widget must have its own identity.")
        return value


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
