"""Bounded prepared chart values. Query semantics stay outside the renderer."""

from decimal import Decimal
from typing import Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from mailbrief.domain.common import DomainModel
from mailbrief.domain.dashboards import ChartKind
from mailbrief.domain.metric_entries import exact_decimal_input


class MetricSample(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    key: str = Field(min_length=1, max_length=120)
    label: str = Field(min_length=1, max_length=200, repr=False)
    value: Decimal | None = Field(
        default=None, allow_inf_nan=False, ge=-(10**30), le=10**30, repr=False
    )
    explanation: str = Field(default="", max_length=1000, repr=False)

    _exact_value = field_validator("value", mode="before")(exact_decimal_input)


class MetricDisplay(DomainModel):
    """In-memory presentation only; a total is explicit, never inferred from buckets.

    Distinct and latest-value totals may differ from the sum of displayed samples.
    A line's missing sample is a gap. Category ordering is supplied by the query.
    """

    model_config = ConfigDict(hide_input_in_errors=True)

    title: str = Field(min_length=1, max_length=120, repr=False)
    unit: str = Field(min_length=1, max_length=32, repr=False)
    chart: ChartKind
    total: Decimal | None = Field(
        default=None, allow_inf_nan=False, ge=-(10**30), le=10**30, repr=False
    )
    samples: tuple[MetricSample, ...] = Field(default=(), max_length=366)
    caption: str = Field(default="", max_length=2000, repr=False)
    # Only a query-approved DAILY target reaches this field; range targets belong in caption.
    daily_target: Decimal | None = Field(
        default=None, allow_inf_nan=False, ge=-(10**30), le=10**30, repr=False
    )

    _exact_values = field_validator("total", "daily_target", mode="before")(exact_decimal_input)

    @model_validator(mode="after")
    def unique_samples(self) -> Self:
        if len({sample.key for sample in self.samples}) != len(self.samples):
            raise ValueError("Each chart sample must have a distinct key.")
        if self.chart is ChartKind.NUMBER and self.daily_target is not None:
            raise ValueError("A number card cannot draw a daily target.")
        return self


def metric_number(value: Decimal | None) -> str:
    """Exact owner-visible value. No float conversion or silent precision loss."""
    if value is None:
        return "Unknown"
    return format(value, "f")
