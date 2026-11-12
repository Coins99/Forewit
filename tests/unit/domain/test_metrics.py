"""P4 contracts: exact measurements, honest ranges and supported widget queries."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, localcontext
from uuid import uuid4

import pytest
from pydantic import ValidationError

from mailbrief.domain.dashboards import (
    ChartKind,
    DashboardLayout,
    TargetScope,
    ValidatedWidget,
    WidgetSpec,
    WidgetTarget,
    resolved_widget_query,
)
from mailbrief.domain.metric_entries import (
    MAX_SCALED_VALUE,
    DefinedMetricEntry,
    MeasurementKind,
    MetricDefinitionSpec,
    MetricEntryInput,
    parse_scaled_value,
    scaled_decimal,
)
from mailbrief.domain.metric_results import MetricDisplay, MetricSample, metric_number
from mailbrief.domain.metrics import (
    BUILTIN_METRICS,
    MetricDateRange,
    MetricDimension,
    MetricQuery,
    MetricReducer,
    builtin_metric,
    custom_metric,
    is_forward_plan,
    metric_dimensions,
    validate_metric_query,
)


def query(key: str = "tasks.completions") -> MetricQuery:
    descriptor = builtin_metric(key)
    return MetricQuery(
        metric_key=key,
        reducer=descriptor.reducer,
        dates=MetricDateRange(
            first=date(2026, 11, 1), last=date(2026, 11, 7), zone="America/Toronto"
        ),
    )


def widget(key: str = "tasks.completions", chart: ChartKind = ChartKind.BAR) -> WidgetSpec:
    return WidgetSpec(public_id=str(uuid4()), title="My metric", query=query(key), chart=chart)


@pytest.mark.parametrize(
    "text,scale,expected",
    [
        ("0", 0, 0),
        ("+25", 0, 25),
        ("0.1", 6, 100000),
        ("-0.125", 3, -125),
        (" 12.30 ", 2, 1230),
        ("1000000000000000", 0, MAX_SCALED_VALUE),
    ],
)
def test_decimal_input_is_exact(text: str, scale: int, expected: int) -> None:
    with localcontext() as context:
        context.prec = 2
        assert parse_scaled_value(text, scale, allow_negative=True) == expected
        assert scaled_decimal(expected, scale) == Decimal(text.strip())


@pytest.mark.parametrize("text", ["NaN", "Infinity", "1e3", "1,000", ".5", "1.", "١", "", "1_000"])
def test_noncanonical_numbers_are_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        parse_scaled_value(text, 2)


@pytest.mark.parametrize("text,scale", [("1.001", 2), ("1000000000000001", 0), ("-1", 0)])
def test_precision_bounds_and_negative_policy(text: str, scale: int) -> None:
    with pytest.raises(ValueError):
        parse_scaled_value(text, scale)


@pytest.mark.parametrize("scale", [-1, 7, True])
def test_invalid_precision_is_rejected(scale: int) -> None:
    with pytest.raises(ValueError):
        parse_scaled_value("1", scale)
    with pytest.raises(ValueError):
        scaled_decimal(1, scale)


def test_exact_decimal_conversion_ignores_small_context_and_supports_large_totals() -> None:
    with localcontext() as context:
        context.prec = 2
        assert str(scaled_decimal(12345678901234567890, 6)) == "12345678901234.567890"


def test_definition_cleans_labels_and_entry_validates_sign_and_zone() -> None:
    definition = MetricDefinitionSpec(
        name="Reading\u200b", unit="minutes\x00", kind=MeasurementKind.AMOUNT
    )
    assert (definition.name, definition.unit) == ("Reading", "minutes")
    assert definition.parse_value("25") == 25
    entry = MetricEntryInput(value_scaled=-1, local_date=date(2026, 11, 1), zone="America/Toronto")
    with pytest.raises(ValidationError, match="negative"):
        DefinedMetricEntry(definition=definition, entry=entry)
    with pytest.raises(ValidationError, match="unknown"):
        MetricEntryInput(value_scaled=1, local_date=entry.local_date, zone="Bad/Zone")
    assert entry.local_date == date(2026, 11, 1)


@pytest.mark.parametrize("day,hours", [(date(2026, 3, 8), 23), (date(2026, 11, 1), 25)])
def test_local_day_bounds_follow_dst(day: date, hours: int) -> None:
    dates = MetricDateRange(first=day, last=day, zone="America/Toronto")
    first, end = dates.instant_bounds()
    assert end - first == timedelta(hours=hours)
    assert dates.previous().last == day - timedelta(days=1)


def test_date_range_limits_and_previous_local_period() -> None:
    dates = MetricDateRange(first=date(2026, 1, 1), last=date(2027, 1, 1), zone="UTC")
    assert dates.days == 366
    assert dates.previous().days == 366
    for first, last in [
        (date(2026, 1, 1), date(2027, 1, 2)),
        (date(2026, 2, 1), date(2026, 1, 1)),
        (date.min, date.min),
        (date.max, date.max),
    ]:
        with pytest.raises(ValidationError):
            MetricDateRange(first=first, last=last, zone="UTC")


def test_registry_keys_are_unique_and_unknown_keys_fail_closed() -> None:
    assert len({item.key for item in BUILTIN_METRICS}) == len(BUILTIN_METRICS)
    for descriptor in BUILTIN_METRICS:
        validate_metric_query(query(descriptor.key), descriptor)
    with pytest.raises(ValueError, match="unavailable"):
        builtin_metric("unknown")


def test_filters_and_versions_cannot_be_silently_ignored() -> None:
    original = query("capacity.available")
    descriptor = builtin_metric(original.metric_key)
    for changed in [
        original.model_copy(update={"reducer": MetricReducer.COUNT}),
        original.model_copy(update={"definition_version": 2}),
        original.model_copy(update={"group_by": MetricDimension.PROJECT}),
        original.model_copy(update={"area_public_id": str(uuid4())}),
        original.model_copy(update={"metric_key": "other"}),
    ]:
        with pytest.raises(ValueError):
            validate_metric_query(changed, descriptor)


def test_point_value_cannot_be_summed_and_custom_area_is_unsupported() -> None:
    descriptor = custom_metric("custom.balance", "Balance", "CAD", MeasurementKind.POINT_VALUE)
    valid = query().model_copy(
        update={"metric_key": descriptor.key, "reducer": MetricReducer.LATEST}
    )
    validate_metric_query(valid, descriptor)
    with pytest.raises(ValueError, match="calculation"):
        validate_metric_query(valid.model_copy(update={"reducer": MetricReducer.SUM}), descriptor)
    with pytest.raises(ValueError, match="filters"):
        validate_metric_query(valid.model_copy(update={"area_public_id": str(uuid4())}), descriptor)
    assert (
        custom_metric("custom.read", "Reading", "minutes", MeasurementKind.AMOUNT).reducer
        is MetricReducer.SUM
    )


@pytest.mark.parametrize(
    "spec",
    [
        widget("tasks.current_overdue", ChartKind.LINE),
        widget("tasks.current_overdue").model_copy(update={"previous_period": True}),
        widget().model_copy(
            update={
                "chart": ChartKind.LINE,
                "query": query().model_copy(update={"group_by": MetricDimension.PROJECT}),
            }
        ),
        widget(chart=ChartKind.NUMBER).model_copy(
            update={"target": WidgetTarget(value=Decimal(5), scope=TargetScope.DAILY)}
        ),
        widget(chart=ChartKind.NUMBER).model_copy(
            update={"query": query().model_copy(update={"group_by": MetricDimension.PROJECT})}
        ),
    ],
)
def test_incompatible_widget_configuration_is_rejected(spec: WidgetSpec) -> None:
    with pytest.raises(ValidationError):
        ValidatedWidget(widget=spec, descriptor=builtin_metric(spec.query.metric_key))


def test_category_overdue_and_daily_targets_are_supported() -> None:
    spec = widget("tasks.current_overdue").model_copy(
        update={
            "query": query("tasks.current_overdue").model_copy(
                update={"group_by": MetricDimension.PROJECT}
            )
        }
    )
    ValidatedWidget(widget=spec, descriptor=builtin_metric(spec.query.metric_key))
    spec = widget().model_copy(
        update={"target": WidgetTarget(value=Decimal(2), scope=TargetScope.DAILY)}
    )
    ValidatedWidget(widget=spec, descriptor=builtin_metric(spec.query.metric_key))


def test_layout_round_trip_limits_duplicates_and_future_versions() -> None:
    layout = DashboardLayout(widgets=tuple(widget() for _ in range(12)))
    assert DashboardLayout.model_validate_json(layout.model_dump_json()) == layout
    for data in [
        {"widgets": (*layout.widgets, widget())},
        {"widgets": (layout.widgets[0], layout.widgets[0])},
        {"schema_version": 2},
    ]:
        with pytest.raises(ValidationError):
            DashboardLayout.model_validate(data)


def test_display_preserves_missing_zero_and_explicit_nonadditive_total() -> None:
    data = MetricDisplay(
        title="Distinct tasks",
        unit="tasks",
        chart=ChartKind.BAR,
        total=Decimal(1),
        samples=(
            MetricSample(key="a", label="Monday", value=Decimal(1)),
            MetricSample(key="b", label="Tuesday", value=Decimal(1)),
            MetricSample(key="c", label="Wednesday"),
            MetricSample(key="d", label="Thursday", value=Decimal(0)),
        ),
    )
    assert data.total == 1
    assert metric_number(data.samples[2].value) == "Unknown"
    assert metric_number(data.samples[3].value) == "0"
    with pytest.raises(ValidationError, match="distinct"):
        MetricDisplay(
            title="Bad", unit="tasks", chart=ChartKind.LINE, samples=(data.samples[0],) * 2
        )
    with pytest.raises(ValidationError):
        MetricSample(key="bad", label="Bad", value=Decimal("NaN"))


@pytest.mark.parametrize("value", [True, 0.1])
def test_binary_float_and_boolean_values_are_rejected(value: object) -> None:
    with pytest.raises(ValidationError):
        WidgetTarget.model_validate({"value": value})
    with pytest.raises(ValidationError):
        MetricSample.model_validate({"key": "a", "label": "Observation", "value": value})
    with pytest.raises(ValidationError):
        MetricDisplay.model_validate(
            {"title": "Observation", "unit": "units", "chart": "number", "total": value}
        )


def test_owner_values_are_hidden_from_repr_and_validation_errors() -> None:
    sample = MetricSample(key="a", label="private label", value=Decimal("123456.789"))
    assert "123456.789" not in repr(sample)
    assert "private label" not in repr(sample)
    with pytest.raises(ValidationError) as error:
        MetricSample.model_validate(
            {"key": "a", "label": "private label", "value": "private-invalid"}
        )
    assert "private-invalid" not in str(error.value)


def test_a_dashboard_filter_reaches_only_a_metric_that_can_take_it() -> None:
    project, area = str(uuid4()), str(uuid4())
    dates = query().dates

    def resolve(spec: WidgetSpec) -> MetricQuery:
        return resolved_widget_query(spec, dates, project_public_id=project, area_public_id=area)

    tasks = resolve(widget("tasks.completions"))
    assert (tasks.project_public_id, tasks.area_public_id) == (project, area)
    # Time metrics take neither filter: they keep their whole value and stay valid.
    capacity = resolve(widget("capacity.available"))
    assert (capacity.project_public_id, capacity.area_public_id) == (None, None)
    validate_metric_query(capacity, builtin_metric("capacity.available"))
    # A custom metric takes a project and has no life area.
    key = f"custom.{uuid4()}.amount"
    custom = WidgetSpec(
        public_id=str(uuid4()),
        title="Mine",
        chart=ChartKind.BAR,
        query=query().model_copy(update={"metric_key": key, "reducer": MetricReducer.SUM}),
    )
    resolved = resolve(custom)
    assert (resolved.project_public_id, resolved.area_public_id) == (project, None)
    # A widget's own filter is saved with it and never replaced by the dashboard's.
    own = str(uuid4())
    kept = widget().model_copy(
        update={"query": query().model_copy(update={"project_public_id": own})}
    )
    assert resolve(kept).project_public_id == own
    assert metric_dimensions("tasks.created") == (MetricDimension.PROJECT, MetricDimension.AREA)
    assert metric_dimensions(key) == (MetricDimension.PROJECT,)
    assert metric_dimensions("unknown.metric") == ()


def test_a_forward_plan_starts_today_and_other_metrics_keep_their_dates() -> None:
    zone = "America/Toronto"
    dates = MetricDateRange(first=date(2026, 11, 3), last=date(2026, 11, 9), zone=zone)
    now = datetime(2026, 11, 9, 15, tzinfo=UTC)  # 10:00 on the 9th in Toronto.
    planned = widget("workload.planned")
    today = MetricDateRange(first=date(2026, 11, 9), last=date(2026, 11, 9), zone=zone)
    assert resolved_widget_query(planned, dates, now=now).dates == today
    # The range's zone decides today: 03:30 UTC on the 10th is still the 9th in Toronto.
    late = datetime(2026, 11, 10, 3, 30, tzinfo=UTC)
    assert resolved_widget_query(planned, dates, now=late).dates == today
    # No clock, a range wholly past or one that starts later: nothing is moved, and the
    # query service still refuses a past start.
    past = MetricDateRange(first=date(2026, 11, 1), last=date(2026, 11, 8), zone=zone)
    ahead = MetricDateRange(first=date(2026, 11, 12), last=date(2026, 11, 14), zone=zone)
    assert resolved_widget_query(planned, dates).dates == dates
    assert resolved_widget_query(planned, past, now=now).dates == past
    assert resolved_widget_query(planned, ahead, now=now).dates == ahead
    # A widget's own range shrinks from today in the same way, so it outlives the day
    # it was saved on.
    own = MetricDateRange(first=date(2026, 11, 8), last=date(2026, 11, 14), zone=zone)
    saved = planned.model_copy(
        update={
            "use_global_range": False,
            "query": planned.query.model_copy(update={"dates": own}),
        }
    )
    assert resolved_widget_query(saved, dates, now=now).dates == MetricDateRange(
        first=date(2026, 11, 9), last=date(2026, 11, 14), zone=zone
    )
    assert resolved_widget_query(widget(), dates, now=now).dates == dates
    assert is_forward_plan("workload.planned")
    assert not is_forward_plan("tasks.created") and not is_forward_plan("custom.x.amount")
