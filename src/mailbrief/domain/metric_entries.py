"""Exact owner-entered measurements; no database, inference or floating-point input."""

import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Final, Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from mailbrief.domain.common import DomainModel, known_zone, normalize_utc

MAX_SCALED_VALUE: Final = 10**15
MAX_METRIC_SCALE: Final = 6
_NUMBER: Final = re.compile(r"[+-]?[0-9]+(?:\.[0-9]+)?\Z")


class MeasurementKind(StrEnum):
    AMOUNT = "amount"
    POINT_VALUE = "point_value"


class MetricDefinitionSpec(DomainModel):
    """Semantic fields are immutable once saved; changing units creates a new metric."""

    model_config = ConfigDict(hide_input_in_errors=True)

    name: str = Field(min_length=1, max_length=120, repr=False)
    unit: str = Field(min_length=1, max_length=32, repr=False)
    kind: MeasurementKind
    scale: int = Field(default=0, ge=0, le=MAX_METRIC_SCALE, strict=True)
    allow_negative: bool = False

    @field_validator("name", "unit", mode="before")
    @classmethod
    def clean_label(cls, value: object) -> object:
        if isinstance(value, str):
            return " ".join(
                "".join(c for c in value if unicodedata.category(c) not in {"Cc", "Cf"}).split()
            )
        return value

    def parse_value(self, text: str) -> int:
        return parse_scaled_value(text, self.scale, allow_negative=self.allow_negative)


def parse_scaled_value(text: str, scale: int, *, allow_negative: bool = False) -> int:
    """Parse bounded ASCII decimal text without rounding, float or Decimal context effects.

    Locale-specific grouping/decimal separators belong in the future input control;
    this boundary accepts only the canonical decimal representation.
    """
    if type(scale) is not int or not 0 <= scale <= MAX_METRIC_SCALE:
        raise ValueError("Metric precision must be between 0 and 6.")
    cleaned = text.strip()
    if len(cleaned) > 32 or not _NUMBER.fullmatch(cleaned):
        raise ValueError("Enter a finite decimal number without grouping separators.")
    whole, _, fraction = cleaned.lstrip("+-").partition(".")
    if len(fraction) > scale:
        raise ValueError("The value has more decimal places than this metric allows.")
    value = int(whole) * int(10**scale) + int((fraction + "0" * scale)[:scale] or "0")
    if cleaned.startswith("-"):
        value = -value
    if abs(value) > MAX_SCALED_VALUE:
        raise ValueError("The value is too large for this metric.")
    if value < 0 and not allow_negative:
        raise ValueError("This metric does not allow negative values.")
    return value


def scaled_decimal(value: int, scale: int) -> Decimal:
    """Exact conversion, including aggregate integers larger than an individual entry."""
    if type(value) is not int or type(scale) is not int or not 0 <= scale <= MAX_METRIC_SCALE:
        raise ValueError("A metric value needs an integer and a valid precision.")
    negative = value < 0
    digits = tuple(int(c) for c in str(abs(value)))
    return Decimal((int(negative), digits, -scale))


def exact_decimal_input(value: object) -> object:
    """Owner values and prepared results must never arrive through a binary float."""
    if isinstance(value, (bool, float)):
        raise ValueError("Use exact decimal text or an integer for a metric value.")
    return value


class MetricEntryInput(DomainModel):
    """Date-only diary semantics: changing the display zone never moves this date."""

    model_config = ConfigDict(hide_input_in_errors=True)

    value_scaled: int = Field(ge=-MAX_SCALED_VALUE, le=MAX_SCALED_VALUE, strict=True, repr=False)
    local_date: date
    zone: str = Field(min_length=1, max_length=64)
    project_public_id: str | None = Field(default=None, min_length=36, max_length=36)

    _zone = field_validator("zone")(known_zone)


class DefinedMetricEntry(DomainModel):
    """Validate a log against its definition before any persistence operation."""

    model_config = ConfigDict(hide_input_in_errors=True)

    definition: MetricDefinitionSpec
    entry: MetricEntryInput

    @model_validator(mode="after")
    def valid_sign(self) -> Self:
        if self.entry.value_scaled < 0 and not self.definition.allow_negative:
            raise ValueError("This metric does not allow negative values.")
        return self


class MetricDefinition(MetricDefinitionSpec):
    public_id: str = Field(min_length=36, max_length=36)
    version: int = Field(default=1, ge=1, le=1)
    revision: int = Field(ge=1)
    archived: bool
    created_at_utc: datetime
    updated_at_utc: datetime

    _times = field_validator("created_at_utc", "updated_at_utc")(normalize_utc)


class MetricEntryState(MetricEntryInput):
    deleted: bool = False


class MetricEntry(MetricEntryState):
    public_id: str = Field(min_length=36, max_length=36)
    definition_id: str = Field(min_length=36, max_length=36)
    definition_version: int = Field(default=1, ge=1, le=1)
    revision: int = Field(ge=1)
    created_at_utc: datetime
    updated_at_utc: datetime

    _times = field_validator("created_at_utc", "updated_at_utc")(normalize_utc)


class MetricEntryChange(DomainModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    revision: int = Field(ge=1)
    operation: Literal["create", "edit", "delete", "restore", "undo"]
    state: MetricEntryState = Field(repr=False)
    corrects_revision: int | None = Field(default=None, ge=1)
    recorded_at_utc: datetime

    _time = field_validator("recorded_at_utc")(normalize_utc)


def validate_entry_history(
    changes: tuple[MetricEntryChange, ...],
    state: MetricEntryState,
    revision: int,
) -> None:
    """One correction protocol for normal reads and untrusted backup verification."""
    invalid = "Saved metric correction history is invalid."
    if len(changes) != revision or not changes or changes[-1].state != state:
        raise ValueError(invalid)
    for index, change in enumerate(changes):
        if change.revision != index + 1 or (index == 0) != (change.operation == "create"):
            raise ValueError(invalid)
        previous = changes[index - 1].state if index else None
        if change.operation == "undo":
            if previous is None:
                raise ValueError(invalid)
            expected = (
                changes[index - 2].state
                if index > 1
                else previous.model_copy(update={"deleted": True})
            )
            if change.corrects_revision != index or change.state != expected:
                raise ValueError(invalid)
        elif change.corrects_revision is not None:
            raise ValueError(invalid)
        if change.operation in {"create", "edit", "restore"} and change.state.deleted:
            raise ValueError(invalid)
        if change.operation in {"delete", "restore"}:
            deleted = change.operation == "delete"
            if (
                previous is None
                or previous.deleted == deleted
                or change.state != previous.model_copy(update={"deleted": deleted})
            ):
                raise ValueError(invalid)
        if change.operation == "edit" and previous is not None and previous.deleted:
            raise ValueError(invalid)
