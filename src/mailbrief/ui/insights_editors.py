"""Plain, exact-value Insights editors. Owner writes run independently of AI jobs."""

import asyncio
from collections.abc import Coroutine
from datetime import date, datetime
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QGuiApplication, QStandardItemModel
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.common import new_public_id, utc_now
from mailbrief.domain.dashboards import (
    ChartKind,
    TargetScope,
    ValidatedWidget,
    WidgetSize,
    WidgetSpec,
    WidgetTarget,
    resolved_widget_query,
)
from mailbrief.domain.metric_entries import (
    MeasurementKind,
    MetricDefinition,
    MetricDefinitionSpec,
    MetricEntry,
    MetricEntryInput,
    scaled_decimal,
)
from mailbrief.domain.metric_queries import MetricComparison, MetricQueryResult
from mailbrief.domain.metric_results import MetricDisplay, MetricSample, metric_number
from mailbrief.domain.metrics import (
    BUILTIN_METRICS,
    MAX_METRIC_DAYS,
    MetricDateRange,
    MetricDescriptor,
    MetricDimension,
    MetricQuery,
    MetricRangeMode,
    custom_metric,
    is_forward_plan,
)
from mailbrief.domain.projects import LifeArea, Project
from mailbrief.services.metric_entries import MetricConflictError
from mailbrief.services.metrics import assess_target
from mailbrief.ui.date_picker import calendar_popup
from mailbrief.ui.insights_backend import InsightsBackend
from mailbrief.ui.labels import outline_button, wrap_label
from mailbrief.ui.lists import SafeList
from mailbrief.ui.metric_chart import MetricChartCard

GENERAL_REFUSAL: Final = "Choose compatible chart, grouping, dates and an exact target."
NO_METRIC: Final = "No metric yet: create one below"
UNAVAILABLE: Final = (
    "Unavailable for these dates or filters. Edit this widget or change the dashboard filters."
)


class InputRefused(ValueError):
    """What the owner has to change, in the app's own words: a dialog shows it as it is."""


def named[T: QWidget](widget: T, name: str) -> T:
    widget.setObjectName("insights-" + name.lower().replace(" ", "-"))
    widget.setAccessibleName(name)
    return widget


def combo(name: str) -> QComboBox:
    """A combo box that Tab reaches on macOS too. Qt gives a combo box there a policy that
    Tab skips unless Full Keyboard Access is on; like the app's other forms, these take
    Tab and a click, and the wheel can't focus them."""
    box = named(QComboBox(), name)
    box.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
    return box


def date_field(day: date, name: str) -> QDateEdit:
    field = named(QDateEdit(QDate(day.year, day.month, day.day)), name)
    calendar_popup(field)
    field.setDisplayFormat("yyyy-MM-dd")
    field.setKeyboardTracking(False)
    field.setMaximumDate(QDate(9999, 12, 29))  # A range keeps clear of the calendar's end.
    return field


def keep_range(first: QDateEdit, last: QDateEdit, moved: QDateEdit) -> None:
    """After ``moved`` changed, move the other end so the two stay an ordered range of at
    most MAX_METRIC_DAYS dates: a range can never be one the queries refuse. The end that
    is moved here sends no change signal."""
    start, end = first.date(), last.date()
    if start <= end and start.daysTo(end) < MAX_METRIC_DAYS:
        return
    other = last if moved is first else first
    reach = (MAX_METRIC_DAYS - 1) * (1 if moved is first else -1)
    other.blockSignals(True)
    other.setDate(moved.date() if start > end else moved.date().addDays(reach))
    other.blockSignals(False)


def refusal_text(error: Exception) -> str:
    """Why a widget's fields were refused: the app's own reason when there is one."""
    if isinstance(error, InputRefused):
        return str(error)
    if isinstance(error, ValidationError):
        for detail in error.errors(include_url=False, include_input=False):
            reason = detail.get("ctx", {}).get("error")
            if detail["type"] == "value_error" and isinstance(reason, ValueError):
                text = str(reason)
                return text[:1].upper() + text[1:] + ("" if text.endswith(".") else ".")
    return GENERAL_REFUSAL


def filter_note(query: MetricQuery, project: str | None, area: str | None) -> str:
    """What to say on a widget whose metric can't take a dashboard filter that is set."""
    skipped = [
        name
        for name, chosen, used in (
            ("project", project, query.project_public_id),
            ("life area", area, query.area_public_id),
        )
        if chosen and not used
    ]
    if not skipped:
        return ""
    words = "filters do" if len(skipped) > 1 else "filter does"
    return f"The dashboard's {' and '.join(skipped)} {words} not apply to this metric."


def unavailable_text(query: MetricQuery, now: datetime) -> str:
    """Why ``query`` has no value, as far as the page can tell."""
    today = now.astimezone(ZoneInfo(query.dates.zone)).date()
    if is_forward_plan(query.metric_key) and query.dates.last < today:
        return "Planned workload covers today and later. Choose dates that include today."
    return UNAVAILABLE


def custom_descriptor(definition: MetricDefinition) -> MetricDescriptor:
    suffix = "amount" if definition.kind is MeasurementKind.AMOUNT else "point"
    return custom_metric(
        f"custom.{definition.public_id}.{suffix}", definition.name, definition.unit, definition.kind
    )


def comparison_caption(comparison: MetricComparison) -> str:
    prior = metric_number(comparison.previous.total) if comparison.previous else "unavailable"
    return (
        f"Previous: {prior}. Difference: {metric_number(comparison.difference)}. "
        + comparison.explanation
    )


def display_result(result: MetricQueryResult, widget: WidgetSpec, extra: str = "") -> MetricDisplay:
    notes = list(dict.fromkeys((result.descriptor.explanation, *result.coverage.notes)))
    if result.coverage.partial_tasks:
        notes.append(f"Partial task histories: {result.coverage.partial_tasks}.")
    if result.coverage.missing_estimates:
        notes.append(f"Missing estimates: {result.coverage.missing_estimates}.")
    if result.coverage.missing_observation_dates:
        notes.append(f"Dates without observations: {result.coverage.missing_observation_dates}.")
    if result.coverage.calendar_status:
        states = sorted({state for _, state in result.coverage.calendar_status})
        notes.append("Calendar coverage: " + "; ".join(states) + ".")
    if result.observation_date is not None:
        notes.append(f"Observed on {result.observation_date.isoformat()}.")
    # In the zone the line names, to the minute: never UTC with microseconds.
    refreshed = result.captured_at_utc.astimezone(ZoneInfo(result.query.dates.zone))
    notes.append(
        f"{result.query.dates.first} to {result.query.dates.last}; "
        f"{result.query.dates.zone}. Refreshed {refreshed:%Y-%m-%d %H:%M}."
    )
    if widget.target is not None and widget.target.scope is TargetScope.RANGE:
        notes.append(
            f"Range target: {metric_number(widget.target.value)} {result.descriptor.unit}."
        )
        target = assess_target(result, widget.target)
        notes.append(f"Difference from target: {metric_number(target.difference)}.")
    if extra:
        notes.append(extra)
    return MetricDisplay(
        title=widget.title,
        unit=result.descriptor.unit,
        chart=widget.chart,
        total=result.total,
        samples=tuple(
            MetricSample(key=b.key, label=b.label, value=b.value) for b in result.buckets
        ),
        caption=" ".join(notes)[:2000],
        daily_target=(
            widget.target.value
            if widget.target is not None and widget.target.scope is TargetScope.DAILY
            else None
        ),
        categorical=result.query.group_by is not None,
    )


class InsightsDialog(QDialog):
    changed = Signal()

    def __init__(self, title: str, parent: QWidget, *, width: int = 560, height: int = 600) -> None:
        super().__init__(parent)
        named(self, title)
        self.setWindowTitle(title)
        screen = QGuiApplication.primaryScreen()
        room = screen.availableGeometry().height() - 80 if screen is not None else height
        self.resize(width, max(360, min(height, room)))
        outer = QVBoxLayout(self)
        scroll = named(QScrollArea(), title + " fields")
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidgetResizable(True)
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # Tab goes to the fields inside.
        content = named(QWidget(), title + " content")
        self.body = QVBoxLayout(content)
        scroll.setWidget(content)
        outer.addWidget(scroll, 1)
        # Under the scrolled fields, never inside them: what a button answered is on
        # screen wherever the owner has scrolled to. It lines up with the fields above.
        self.message = wrap_label("")
        named(self.message, "Editor status")
        status_row = QHBoxLayout()
        inset = self.body.contentsMargins()
        status_row.setContentsMargins(inset.left(), 0, inset.right(), 0)
        status_row.addWidget(self.message)
        outer.addLayout(status_row)
        self.jobs: set[asyncio.Task[None]] = set()
        self._commands: dict[str, str] = {}
        self._saving = False
        self._focus_after: QWidget | None = None

    def command(self, payload: str) -> str:
        return self._commands.setdefault(payload, new_public_id())

    def run(self, operation: Coroutine[object, object, None], *, writing: bool = False) -> None:
        async def execute() -> None:
            try:
                await operation
            except InputRefused as refused:
                self.message.setText(str(refused))
            except MetricConflictError:
                self.message.setText(
                    "This record changed. Your input is kept; close and reopen "
                    "to review the latest version."
                )
            except (ValueError, LookupError, RuntimeError):
                self.message.setText(
                    "Unable to complete this change. Check the fields, "
                    "then retry or reload. Your input is kept."
                )
            except Exception:
                self.message.setText("Unable to read or save Insights. Retry or reload.")
            finally:
                if writing:
                    self._saving = False
                    self.setEnabled(True)
                    self._give_focus_back()

        task = asyncio.create_task(execute())
        self.jobs.add(task)

        def finished(task: asyncio.Task[None]) -> None:
            self.jobs.discard(task)
            operation.close()

        task.add_done_callback(finished)

    def write(self, operation: Coroutine[object, object, None]) -> None:
        if self._saving:
            operation.close()
            return
        self._saving = True
        # Disabling the dialog takes focus from its field; the write gives it back.
        self._focus_after = self.focusWidget()
        self.setEnabled(False)
        self.before_write()
        self.run(operation, writing=True)

    def _give_focus_back(self) -> None:
        """Focus where it was before the write, or where the write asked for it."""
        target, self._focus_after = self._focus_after, None
        if target is None or not self.isVisible():
            return
        try:
            if target.isEnabled() and target.isVisibleTo(self):
                target.setFocus(Qt.FocusReason.OtherFocusReason)
        except RuntimeError:  # Qt has deleted it since.
            return

    def before_write(self) -> None:
        """Invalidate pending read generations before a subclass's write starts."""

    def has_unsaved_input(self) -> bool:
        return False

    def reject(self) -> None:
        if not self._saving:
            super().reject()

    async def drain(self) -> None:
        while pending := [job for job in self.jobs if not job.done()]:
            await asyncio.gather(*pending, return_exceptions=True)


class WidgetEditor(InsightsDialog):
    saved = Signal(object)

    def __init__(
        self,
        backend: InsightsBackend,
        dates: MetricDateRange,
        definitions: tuple[MetricDefinition, ...],
        projects: tuple[Project, ...],
        parent: QWidget,
        widget: WidgetSpec | None = None,
        areas: tuple[LifeArea, ...] = (),
        global_project: str | None = None,
        global_area: str | None = None,
    ) -> None:
        super().__init__("Edit widget" if widget else "Add widget", parent, height=680)
        self.backend = backend
        self.dates = dates
        self.global_project, self.global_area = global_project, global_area
        self.identity = widget.public_id if widget else new_public_id()
        self.descriptors = (
            *BUILTIN_METRICS,
            *(
                custom_descriptor(d)
                for d in definitions
                if not d.archived
                or widget is not None
                and widget.query.metric_key == custom_descriptor(d).key
            ),
        )
        form = QFormLayout()
        self.metric_choice = combo("Metric")
        for descriptor in self.descriptors:
            self.metric_choice.addItem(descriptor.label, descriptor.key)
        # A title left blank is the metric's name. It was saved as that name, so it is
        # shown blank again: the title then follows the metric when the owner changes it.
        named_after = next(
            (d.label for d in self.descriptors if widget and d.key == widget.query.metric_key),
            None,
        )
        own_title = widget.title if widget and widget.title != named_after else ""
        self.title = named(QLineEdit(own_title), "Widget title")
        self.title.setMaxLength(120)
        self.chart = combo("Chart")
        for kind in ChartKind:
            self.chart.addItem(kind.value.title(), kind.value)
        self.group = combo("Group by")
        self.group.addItem("Daily / total", "")
        self.group.addItem("Project", "project")
        self.group.addItem("Life area", "area")
        self.project = combo("Project filter")
        self.project.addItem("Use dashboard project", "")
        for project in projects:
            self.project.addItem(project.name, project.public_id)
        self.area = combo("Life area filter")
        self.area.addItem("Use dashboard life area", "")
        for area in areas:
            self.area.addItem(area.name, area.public_id)
        self.global_range = named(QCheckBox("Use dashboard date range"), "Use dashboard range")
        self.global_range.setChecked(True if widget is None else widget.use_global_range)
        chosen = dates if widget is None else widget.query.dates
        self.first = date_field(chosen.first, "First date")
        self.last = date_field(chosen.last, "Last date")
        self.zone_field = named(QLineEdit(chosen.zone), "Widget time zone")
        self.size_choice = combo("Widget size")
        for size in WidgetSize:
            self.size_choice.addItem(size.value.title(), size.value)
        self.target = named(QLineEdit(), "Target")
        self.target.setPlaceholderText("Optional exact decimal")
        self.scope = combo("Target period")
        for scope in TargetScope:
            self.scope.addItem(scope.value.title(), scope.value)
        self.previous = named(QCheckBox("Compare with previous period"), "Previous period")
        for label, control in (
            ("Metric", self.metric_choice),
            ("Title (optional)", self.title),
            ("Chart", self.chart),
            ("Group by", self.group),
            ("Project", self.project),
            ("Life area", self.area),
            ("Range", self.global_range),
            ("From", self.first),
            ("Through", self.last),
            ("Time zone", self.zone_field),
            ("Size", self.size_choice),
            ("Target", self.target),
            ("Target period", self.scope),
            ("Comparison", self.previous),
        ):
            form.addRow(label, control)
        self.body.addLayout(form)
        project_label = next(
            (p.name for p in projects if p.public_id == global_project),
            "All projects" if global_project is None else "Unavailable project",
        )
        area_label = next(
            (a.name for a in areas if a.public_id == global_area),
            "All life areas" if global_area is None else "Unavailable life area",
        )
        self.body.addWidget(wrap_label(f"Dashboard filters: {project_label}; {area_label}."))
        self.hint = wrap_label(
            "Calculations and units follow the selected metric. "
            "Incompatible charts or grouping must be changed before saving."
        )
        self.body.addWidget(self.hint)
        self.preview_host = QVBoxLayout()
        self.body.addLayout(self.preview_host)
        row = QHBoxLayout()
        self.preview_button = outline_button("Preview", "insights-preview")
        self.save_button = outline_button("Save widget", "insights-save-widget", primary=True)
        close = outline_button("Cancel", "insights-cancel-widget")
        row.addWidget(self.preview_button)
        row.addWidget(self.save_button)
        row.addWidget(close)
        self.body.addLayout(row)
        close.clicked.connect(self.reject)
        self.preview_button.clicked.connect(self._preview)
        self.save_button.clicked.connect(self._save)
        self.metric_choice.currentIndexChanged.connect(self._metric_changed)
        self.group.currentIndexChanged.connect(self._metric_changed)
        self.global_range.toggled.connect(self._range_changed)
        self.first.dateChanged.connect(lambda: keep_range(self.first, self.last, self.first))
        self.last.dateChanged.connect(lambda: keep_range(self.first, self.last, self.last))
        self._metric_changed()
        if widget is not None:
            if (
                widget.query.project_public_id
                and self.project.findData(widget.query.project_public_id) < 0
            ):
                self.project.addItem("Unavailable project", widget.query.project_public_id)
            if widget.query.area_public_id and self.area.findData(widget.query.area_public_id) < 0:
                self.area.addItem("Unavailable life area", widget.query.area_public_id)
            self.metric_choice.setCurrentIndex(self.metric_choice.findData(widget.query.metric_key))
            for box, value in (
                (self.chart, widget.chart.value),
                (self.group, widget.query.group_by.value if widget.query.group_by else ""),
                (self.project, widget.query.project_public_id or ""),
                (self.size_choice, widget.size.value),
            ):
                box.setCurrentIndex(box.findData(value))
            self.area.setCurrentIndex(max(0, self.area.findData(widget.query.area_public_id or "")))
            self.previous.setChecked(widget.previous_period)
            if widget.target is not None:
                self.target.setText(metric_number(widget.target.value))
                self.scope.setCurrentIndex(self.scope.findData(widget.target.scope.value))
        self._range_changed()
        self._initial = self._input()

    def _range_changed(self) -> None:
        self.first.setEnabled(not self.global_range.isChecked())
        self.last.setEnabled(not self.global_range.isChecked())
        self.zone_field.setEnabled(not self.global_range.isChecked())

    def _metric_changed(self) -> None:
        key = self.metric_choice.currentData()
        descriptor = next((d for d in self.descriptors if d.key == key), None)
        if descriptor is not None:
            self.title.setPlaceholderText(descriptor.label)
            self.hint.setText(
                descriptor.explanation
                + " Unit: "
                + descriptor.unit
                + "."
                + (
                    " Dates before today are left out."
                    if descriptor.range_mode is MetricRangeMode.FORWARD_PLAN
                    else ""
                )
            )
            for index in range(1, self.group.count()):
                enabled = MetricDimension(self.group.itemData(index)) in descriptor.dimensions
                model = self.group.model()
                if isinstance(model, QStandardItemModel) and (item := model.item(index)):
                    item.setEnabled(enabled)
                    item.setToolTip(
                        "" if enabled else "This metric does not support this grouping."
                    )
            model = self.chart.model()
            for index in range(self.chart.count()):
                chart = ChartKind(self.chart.itemData(index))
                enabled = not (
                    chart is ChartKind.NUMBER
                    and self.group.currentData()
                    or chart is ChartKind.LINE
                    and (self.group.currentData() or not descriptor.daily_series)
                    or chart is ChartKind.BAR
                    and not descriptor.daily_series
                    and not self.group.currentData()
                )
                if isinstance(model, QStandardItemModel) and (item := model.item(index)):
                    item.setEnabled(enabled)
                    item.setToolTip("" if enabled else "Choose a compatible grouping or chart.")
            self.previous.setEnabled(
                descriptor.range_mode
                not in {
                    MetricRangeMode.CURRENT,
                    MetricRangeMode.FORWARD_PLAN,
                }
            )
            if not self.previous.isEnabled():
                self.previous.setChecked(False)

    def _input(self) -> tuple[object, ...]:
        return (
            self.metric_choice.currentData(),
            self.title.text(),
            self.chart.currentData(),
            self.group.currentData(),
            self.project.currentData(),
            self.area.currentData(),
            self.first.text(),
            self.last.text(),
            self.zone_field.text(),
            self.size_choice.currentData(),
            self.target.text(),
            self.scope.currentData(),
            self.previous.isChecked(),
            self.global_range.isChecked(),
        )

    def has_unsaved_input(self) -> bool:
        return not self._saving and self._input() != self._initial

    def spec(self) -> WidgetSpec:
        descriptor = next(
            (d for d in self.descriptors if d.key == self.metric_choice.currentData()), None
        )
        if descriptor is None:
            raise InputRefused("Choose a metric for this widget.")
        target = self.target.text().strip()
        try:
            target_value = Decimal(target) if target else None
        except ArithmeticError:
            raise InputRefused("Enter the target as a plain number, such as 8000 or 2.5.") from None
        dates = (
            self.dates
            if self.global_range.isChecked()
            else MetricDateRange(
                first=self.first.date().toPython(),
                last=self.last.date().toPython(),
                zone=self.zone_field.text().strip(),
            )
        )
        widget = WidgetSpec(
            public_id=self.identity,
            title=self.title.text().strip() or descriptor.label,
            query=MetricQuery(
                metric_key=descriptor.key,
                reducer=descriptor.reducer,
                dates=dates,
                group_by=MetricDimension(self.group.currentData())
                if self.group.currentData()
                else None,
                project_public_id=self.project.currentData() or None,
                area_public_id=self.area.currentData() or None,
            ),
            chart=ChartKind(self.chart.currentData()),
            size=WidgetSize(self.size_choice.currentData()),
            use_global_range=self.global_range.isChecked(),
            previous_period=self.previous.isChecked(),
            target=None
            if target_value is None
            else WidgetTarget(value=target_value, scope=TargetScope(self.scope.currentData())),
        )
        ValidatedWidget(widget=widget, descriptor=descriptor)
        return widget

    def _checked(self) -> WidgetSpec | None:
        """The widget the fields describe, or None after saying what to change. Save and
        Preview refuse the same input with the same words."""
        try:
            return self.spec()
        except (ValueError, ArithmeticError) as error:
            self.message.setText(refusal_text(error))
            return None

    def _save(self) -> None:
        if (widget := self._checked()) is not None:
            self.saved.emit(widget)

    def _preview(self) -> None:
        widget = self._checked()
        if widget is None:
            return

        async def preview() -> None:
            now = self.backend.metrics.clock()
            query = resolved_widget_query(
                widget,
                self.dates,
                project_public_id=self.global_project,
                area_public_id=self.global_area,
                now=now,
            )
            extra = filter_note(query, self.global_project, self.global_area)
            try:
                if widget.previous_period:
                    comparison = await self.backend.metrics.compare(query, captured_at=now)
                    result = comparison.current
                    extra = " ".join(filter(None, (comparison_caption(comparison), extra)))
                else:
                    result = await self.backend.metrics.query(query, captured_at=now)
            except (ValueError, LookupError):
                raise InputRefused(unavailable_text(query, now)) from None
            if self._input() != before:
                return
            while self.preview_host.count():
                item = self.preview_host.takeAt(0)
                if item is not None and (old := item.widget()) is not None:
                    old.hide()
                    old.deleteLater()
            self.preview_host.addWidget(
                MetricChartCard(display_result(result, widget, extra), self)
            )
            self.message.setText("Preview only. Save widget to keep this configuration.")

        before = self._input()
        self.write(preview())


class MetricManager(InsightsDialog):
    def __init__(
        self, backend: InsightsBackend, zone: str, projects: tuple[Project, ...], parent: QWidget
    ) -> None:
        super().__init__("Manage metrics", parent, height=800)
        self.backend, self.zone = backend, zone
        self.definitions: tuple[MetricDefinition, ...] = ()
        self.rows: tuple[MetricEntry, ...] = ()
        self.selected: MetricEntry | None = None
        self._definition_generation = 0
        self._entry_generation = 0
        self._history_generation = 0
        self._shown_definition_id: str | None = None
        self.metric_choice = combo("Logged metric")
        self.body.addWidget(self.metric_choice)
        form = QFormLayout()
        self.name = named(QLineEdit(), "Metric name")
        self.unit = named(QLineEdit(), "Metric unit")
        self.kind = combo("Measurement kind")
        self.kind.addItem("Amount (add recorded values)", MeasurementKind.AMOUNT.value)
        self.kind.addItem("Point value (latest observation)", MeasurementKind.POINT_VALUE.value)
        self.scale = named(QSpinBox(), "Decimal places")
        self.scale.setRange(0, 6)
        self.negative = named(QCheckBox("Allow negative values"), "Allow negative values")
        for label, control in (
            ("New metric", self.name),
            ("Unit", self.unit),
            ("Kind", self.kind),
            ("Decimal places", self.scale),
            ("Sign", self.negative),
        ):
            form.addRow(label, control)
        self.body.addLayout(form)
        controls = QHBoxLayout()
        create = outline_button("Create metric", "insights-create-metric")
        self.archive_button = archive = outline_button("Archive metric", "insights-archive-metric")
        controls.addWidget(create)
        controls.addWidget(archive)
        self.body.addLayout(controls)
        self.entries_list = named(SafeList(), "Metric observations")
        self.body.addWidget(self.entries_list)
        self.body.addWidget(
            wrap_label(
                "Select a record to correct it. New value creates a separate "
                "observation. Deleted records remain available to restore."
            )
        )
        form = QFormLayout()
        self.value = named(QLineEdit(), "Exact value")
        self.day = date_field(utc_now().astimezone(ZoneInfo(zone)).date(), "Observation date")
        self.project = combo("Observation project")
        self.project.addItem("Unassigned", "")
        for project in projects:
            self.project.addItem(project.name, project.public_id)
        form.addRow("Value", self.value)
        form.addRow("Date", self.day)
        form.addRow("Project", self.project)
        self.body.addLayout(form)
        controls = QHBoxLayout()
        self.entry_buttons: dict[str, QPushButton] = {}
        for label, operation in (
            ("Log new value", "create"),
            ("Save correction", "edit"),
            ("Delete", "delete"),
            ("Restore", "restore"),
            ("Undo", "undo"),
        ):
            button = outline_button(
                label, "insights-entry-" + operation, primary=operation == "create", compact=True
            )
            button.clicked.connect(lambda _checked=False, op=operation: self._mutate(op))
            self.entry_buttons[operation] = button
            controls.addWidget(button)
        self.body.addLayout(controls)
        self.history_label = wrap_label("")
        self.body.addWidget(self.history_label)
        controls = QHBoxLayout()
        self.more = outline_button("Next records", "insights-more-entries")
        reload_button = outline_button("Reload from start", "insights-reload-entries")
        discard = outline_button("Discard input", "insights-discard-log-input")
        close = outline_button("Close", "insights-close-metrics")
        for button in (self.more, reload_button, discard, close):
            controls.addWidget(button)
        self.body.addLayout(controls)
        create.clicked.connect(lambda: self.write(self._define()))
        archive.clicked.connect(lambda: self.write(self._archive()))
        close.clicked.connect(self.reject)
        discard.clicked.connect(self._discard_input)
        self.more.clicked.connect(lambda: self.run(self._entries(next_page=True)))
        reload_button.clicked.connect(lambda: self.run(self.reload()))
        self.metric_choice.currentIndexChanged.connect(self._metric_selected)
        self.entries_list.currentRowChanged.connect(self._select)
        # Return in the value logs it, as the primary button does.
        self.value.returnPressed.connect(lambda: self._mutate("create"))
        self._initial = self._input()
        self._sync_buttons()

    def _sync_buttons(self) -> None:
        """Only what can act on the metric and the observation chosen is enabled, so no
        button answers with a refusal the owner could not have foreseen."""
        chosen = self.metric_choice.currentData()
        current = next((d for d in self.definitions if d.public_id == chosen), None)
        archived = current is not None and current.archived
        self.archive_button.setEnabled(current is not None)
        self.archive_button.setText("Reactivate metric" if archived else "Archive metric")
        entry = self.selected
        live = entry is not None and not entry.deleted
        for operation, enabled in (
            ("create", current is not None and not archived),
            ("edit", live),
            ("delete", live),
            ("restore", entry is not None and entry.deleted),
            ("undo", entry is not None),
        ):
            self.entry_buttons[operation].setEnabled(enabled)

    def _input(self) -> tuple[object, ...]:
        return (
            self.name.text(),
            self.unit.text(),
            self.kind.currentData(),
            self.scale.value(),
            self.negative.isChecked(),
            self.value.text(),
            self.day.text(),
            self.project.currentData(),
        )

    def has_unsaved_input(self) -> bool:
        return not self._saving and self._input() != self._initial

    def before_write(self) -> None:
        self._definition_generation += 1
        self._entry_generation += 1
        self._history_generation += 1
        self.entries_list.setEnabled(True)

    def _metric_selected(self) -> None:
        self._definition_generation += 1
        if self.has_unsaved_input():
            self.metric_choice.blockSignals(True)
            self.metric_choice.setCurrentIndex(
                self.metric_choice.findData(self._shown_definition_id)
            )
            self.metric_choice.blockSignals(False)
            self.message.setText("Save or discard your input before changing metrics.")
            return
        self.run(self._entries())

    async def reload(self, *, select: str | None = None) -> None:
        identity = select or self.metric_choice.currentData()
        self._definition_generation += 1
        generation = self._definition_generation
        definitions = await self.backend.definitions()
        if generation != self._definition_generation:
            return
        self.definitions = definitions
        self.metric_choice.blockSignals(True)
        self.metric_choice.clear()
        # An empty box alone says nothing. Set only while there is no metric: a combo box
        # with a placeholder stops choosing its first item by itself.
        self.metric_choice.setPlaceholderText("" if self.definitions else NO_METRIC)
        for definition in self.definitions:
            self.metric_choice.addItem(
                definition.name + (" (archived)" if definition.archived else ""),
                definition.public_id,
            )
        index = self.metric_choice.findData(identity)
        if index >= 0:
            self.metric_choice.setCurrentIndex(index)
        self.metric_choice.blockSignals(False)
        self._sync_buttons()
        await self._entries()

    def definition(self) -> MetricDefinition:
        return next(d for d in self.definitions if d.public_id == self.metric_choice.currentData())

    async def _define(self) -> None:
        if not self.name.text().strip() or not self.unit.text().strip():
            raise InputRefused("Give the new metric a name and a unit.")
        spec = MetricDefinitionSpec(
            name=self.name.text(),
            unit=self.unit.text(),
            kind=MeasurementKind(self.kind.currentData()),
            scale=self.scale.value(),
            allow_negative=self.negative.isChecked(),
        )
        saved = await self.backend.define(spec, self.command("define:" + spec.model_dump_json()))
        self.name.clear()
        self.unit.clear()
        self._commands.clear()
        self._initial = self._input()
        self._focus_after = self.value  # Next, its first value.
        self.changed.emit()
        await self._reload_after_save(select=saved.public_id)

    async def _archive(self) -> None:
        if not self.metric_choice.currentData():
            raise InputRefused("Create a metric first.")
        definition = self.definition()
        await self.backend.configure(
            definition,
            not definition.archived,
            self.command(f"archive:{definition.public_id}:{definition.revision}"),
        )
        self.changed.emit()
        await self._reload_after_save()

    async def _reload_after_save(
        self, *, select: str | None = None, entries_only: bool = False
    ) -> None:
        # A write's acknowledgement is final even if the following display read fails.
        try:
            if entries_only:
                await self._entries()
            else:
                await self.reload(select=select)
        except Exception:
            self.message.setText("Saved. Could not reload observations; use Reload from start.")

    async def _entries(self, *, next_page: bool = False) -> None:
        if self.has_unsaved_input():
            self.message.setText("Save or discard your input before loading other observations.")
            return
        identity = self.metric_choice.currentData()
        if not identity:
            return
        after = (
            (str(self.rows[-1].local_date), self.rows[-1].public_id)
            if next_page and self.rows
            else None
        )
        self._entry_generation += 1
        generation = self._entry_generation
        self._history_generation += 1
        self.selected = None
        self.rows = ()
        self.entries_list.clear()
        self.entries_list.setEnabled(False)
        self.more.setEnabled(False)
        self.value.clear()
        self.history_label.clear()
        self._initial = self._input()
        self._sync_buttons()
        try:
            rows = await self.backend.entries(identity, after)
        finally:
            if generation == self._entry_generation:
                self.entries_list.setEnabled(True)
        if generation != self._entry_generation or self.metric_choice.currentData() != identity:
            return
        self._shown_definition_id = identity
        self.rows = rows
        self.selected = None
        self.entries_list.clear()
        definition = self.definition()
        for entry in rows:
            self.entries_list.addItem(
                QListWidgetItem(
                    f"{entry.local_date}  {scaled_decimal(entry.value_scaled, definition.scale)} "
                    f"{definition.unit} — revision {entry.revision}"
                    + (" (deleted)" if entry.deleted else "")
                )
            )
        self.more.setEnabled(len(rows) == 100)
        self._sync_buttons()
        self.message.setText(
            "This metric is archived. Reactivate it to log new values."
            if definition.archived
            else f"{definition.unit}; {definition.scale} decimal places. Newest first. "
            "Dates stay on the date you record, even after travel."
        )

    def _discard_input(self) -> None:
        self._history_generation += 1
        self.name.clear()
        self.unit.clear()
        self.value.clear()
        self.selected = None
        self.entries_list.blockSignals(True)
        self.entries_list.setCurrentRow(-1)
        self.entries_list.blockSignals(False)
        self._initial = self._input()
        self._sync_buttons()
        self.message.setText("Unsaved input discarded.")

    def _select(self, index: int) -> None:
        if 0 <= index < len(self.rows) and self.has_unsaved_input():
            self.message.setText(
                "Save or log your input first, or use Discard input before "
                "selecting another observation."
            )
            previous = self.rows.index(self.selected) if self.selected in self.rows else -1
            self.entries_list.blockSignals(True)
            self.entries_list.setCurrentRow(previous)
            self.entries_list.blockSignals(False)
            return
        self._history_generation += 1
        generation = self._history_generation
        self.selected = self.rows[index] if 0 <= index < len(self.rows) else None
        self._sync_buttons()
        entry = self.selected
        if entry is None:
            return
        self.value.setText(str(scaled_decimal(entry.value_scaled, self.definition().scale)))
        self.day.setDate(QDate(entry.local_date.year, entry.local_date.month, entry.local_date.day))
        if entry.project_public_id and self.project.findData(entry.project_public_id) < 0:
            self.project.addItem("Unavailable project", entry.project_public_id)
        self.project.setCurrentIndex(max(0, self.project.findData(entry.project_public_id or "")))
        self._initial = self._input()

        async def show_history() -> None:
            history = await self.backend.history(entry.public_id)
            if generation != self._history_generation or self.selected != entry:
                return
            if not history or history[-1].revision != entry.revision:
                self.history_label.setText("This observation changed. Reload before editing it.")
                return
            self.history_label.setText(
                "Corrections: "
                + "; ".join(
                    f"{change.revision} {change.operation}: {change.state.local_date}, "
                    f"{scaled_decimal(change.state.value_scaled, self.definition().scale)}"
                    for change in history
                )
            )

        self.run(show_history())

    def _mutate(self, operation: str) -> None:
        async def save() -> None:
            if not self.metric_choice.currentData():
                raise InputRefused("Create a metric first, then log its values.")
            definition = self.definition()
            selected = self.selected
            if selected is not None and selected.definition_id != definition.public_id:
                raise ValueError("Reload observations before editing another metric.")
            if operation != "create" and selected is None:
                raise InputRefused("Select an observation in the list first.")
            if operation == "create" and definition.archived:
                raise InputRefused("This metric is archived. Reactivate it to log new values.")
            entry = None
            if operation in {"create", "edit"}:
                try:
                    value = definition.parse_value(self.value.text())
                except ValueError as refused:  # The parser's own fixed wording.
                    raise InputRefused(str(refused)) from None
                entry = MetricEntryInput(
                    value_scaled=value,
                    local_date=self.day.date().toPython(),
                    zone=selected.zone
                    if operation == "edit" and selected is not None
                    else self.zone,
                    project_public_id=self.project.currentData() or None,
                )
            command = self.command(
                f"{operation}:{definition.public_id}:{selected}:"
                + (entry.model_dump_json() if entry else "")
            )
            if operation == "create":
                assert entry is not None
                await self.backend.create(definition.public_id, entry, command)
                self._focus_after = self.value  # Ready for the next value.
            else:
                assert selected is not None
                await self.backend.mutate(selected, operation, command, entry)
            self.value.clear()
            self._commands.clear()
            self._initial = self._input()
            self.changed.emit()
            await self._reload_after_save(entries_only=True)

        self.write(save())
