"""Durable local dashboard, responsive cards and generation-checked record inspection."""

import asyncio
from collections.abc import Coroutine
from datetime import date, datetime, timedelta

from PySide6.QtCore import QDate, Qt, QTimer, Signal
from PySide6.QtGui import QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mailbrief.domain.common import new_public_id, utc_now
from mailbrief.domain.dashboards import (
    DashboardLayout,
    SavedDashboard,
    WidgetSize,
    WidgetSpec,
    resolved_widget_query,
)
from mailbrief.domain.metric_entries import MetricDefinition
from mailbrief.domain.metric_queries import MetricContribution, MetricQueryResult
from mailbrief.domain.metric_results import metric_number
from mailbrief.domain.metrics import MetricDateRange, MetricQuery
from mailbrief.domain.projects import LifeArea, Project
from mailbrief.domain.routes import CalendarRoute, TaskRoute
from mailbrief.services.dashboards import DashboardRepairError, starter_layout
from mailbrief.services.metrics import MetricResultChangedError
from mailbrief.ui.insights_backend import InsightsBackend
from mailbrief.ui.insights_editors import (
    InsightsDialog,
    MetricManager,
    WidgetEditor,
    comparison_caption,
    date_field,
    display_result,
    named,
)
from mailbrief.ui.labels import outline_button, wrap_label
from mailbrief.ui.layouts import take_widget
from mailbrief.ui.metric_chart import MetricChartCard


class MetricDetails(InsightsDialog):
    open_requested = Signal(object)
    stale = Signal(object)

    def __init__(
        self,
        backend: InsightsBackend,
        result: MetricQueryResult,
        bucket: str | None,
        parent: QWidget,
    ) -> None:
        super().__init__("Contributing records", parent)
        self.backend, self.query_result, self.bucket = backend, result, bucket
        self.rows: tuple[MetricContribution, ...] = ()
        self.table = named(QTableWidget(0, 5), "Contributing records table")
        self.table.setHorizontalHeaderLabels(
            ["Date", "Record", result.descriptor.unit, "Revision", "Calculation and coverage"]
        )
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.body.addWidget(self.table)
        self.body.addWidget(
            wrap_label(
                "Totals use the metric's calculation. Distinct tasks and overlapping time "
                "must not be added as ordinary rows. Deleted tasks may need restoring."
            )
        )
        row = QHBoxLayout()
        self.more = outline_button("Next page", "insights-detail-next")
        self.open_button = outline_button("Open source", "insights-detail-open")
        close = outline_button("Close", "insights-detail-close")
        for button in (self.more, self.open_button, close):
            row.addWidget(button)
        self.body.addLayout(row)
        self.more.clicked.connect(lambda: self.run(self.load(next_page=True)))
        self.open_button.clicked.connect(self._open)
        self.table.itemActivated.connect(lambda _item: self._open())
        close.clicked.connect(self.reject)

    async def load(self, *, next_page: bool = False) -> None:
        after = self.rows[-1].key if next_page and self.rows else None
        try:
            rows = await self.backend.metrics.contributors(
                self.query_result, bucket=self.bucket, after=after
            )
        except MetricResultChangedError:
            self.query_result = await self.backend.metrics.query(
                self.query_result.query,
                captured_at=self.query_result.captured_at_utc,
            )
            self.bucket = None
            self.rows = ()
            self.table.setRowCount(0)
            self.message.setText("Data changed; refreshed. Showing all contributing records.")
            self.stale.emit(self.query_result)
            rows = await self.backend.metrics.contributors(self.query_result)
        self.rows = rows
        self.table.setRowCount(len(rows))
        for index, record in enumerate(rows):
            intervals = "; ".join(
                f"{start.isoformat()} to {end.isoformat()}" for start, end in record.intervals
            )
            working = "; ".join(
                f"{start.isoformat()} to {end.isoformat()}"
                for start, end in record.working_intervals
            )
            busy = "; ".join(
                f"{start.isoformat()} to {end.isoformat()}" for start, end in record.busy_intervals
            )
            explanation = record.explanation
            if intervals:
                explanation += " Contributing spans: " + intervals
            if working or busy:
                explanation += (
                    f" Working windows: {working or 'none'}. Busy/buffers: {busy or 'none'}."
                )
            if record.related_ids:
                explanation += " Related blocks: " + ", ".join(record.related_ids)
            for column, value in enumerate(
                (
                    str(record.day),
                    record.label or record.source_kind,
                    metric_number(record.value),
                    str(record.revision),
                    explanation,
                )
            ):
                self.table.setItem(index, column, QTableWidgetItem(value))
        self.more.setEnabled(len(rows) == 100)
        if rows:
            self.table.selectRow(0)

    def _open(self) -> None:
        index = self.table.currentRow()
        if not 0 <= index < len(self.rows):
            return
        record = self.rows[index]
        if record.source_kind in {"task", "task_event", "day_plan"}:
            self.open_requested.emit(TaskRoute(public_id=record.source_id))
        elif record.source_kind == "block":
            self.open_requested.emit(
                CalendarRoute(day=record.day, entry=("block", record.source_id))
            )
        elif record.source_kind in {"day", "interval"}:
            self.open_requested.emit(CalendarRoute(day=record.day))
        else:
            self.message.setText("Open Manage metrics to inspect or correct this observation.")
            return
        self.accept()


class InsightsPage(QWidget):
    dialog_requested = Signal(object)
    open_requested = Signal(object)
    data_ready = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        named(self, "Insights")
        self.backend: InsightsBackend | None = None
        self.saved: SavedDashboard | None = None
        self.repair_revision: int | None = None
        self.definitions: tuple[MetricDefinition, ...] = ()
        self.projects: tuple[Project, ...] = ()
        self.areas: tuple[LifeArea, ...] = ()
        self.zone = "UTC"
        self._following_dates: tuple[date, date] | None = None
        self.jobs: set[asyncio.Task[None]] = set()
        self.dialogs: list[InsightsDialog] = []
        self.cards: list[QWidget] = []
        self.rendered_widgets: tuple[WidgetSpec, ...] = ()
        self._rendered_revision: int | None = None
        self._draining = False
        self.generation = 0
        self._next_capture: datetime | None = None
        self._refresh_task: asyncio.Task[None] | None = None
        self._write_task: asyncio.Task[None] | None = None
        self._commands: dict[str, str] = {}
        self._removed: tuple[DashboardLayout, int] | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        row = QHBoxLayout()
        today = utc_now().date()
        self.first = date_field(today - timedelta(days=6), "Dashboard first date")
        self.last = date_field(today, "Dashboard last date")
        self.project = named(QComboBox(), "Dashboard project")
        self.project.addItem("All projects", "")
        self.area = named(QComboBox(), "Dashboard life area")
        self.area.addItem("All life areas", "")
        self.refresh_button = outline_button("Refresh", "insights-refresh")
        for control in (self.first, self.last, self.refresh_button):
            row.addWidget(control)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(self.project)
        row.addWidget(self.area)
        layout.addLayout(row)
        row = QHBoxLayout()
        self.add = outline_button("Add widget", "insights-add", primary=True)
        self.edit_layout = named(QCheckBox("Edit layout"), "Edit layout")
        self.manage = outline_button("Manage metrics", "insights-manage")
        self.reset = outline_button("Reset layout", "insights-reset")
        self.undo = outline_button("Undo removal / reset", "insights-undo-remove")
        self.undo.hide()
        for action_control in (self.add, self.edit_layout):
            row.addWidget(action_control)
        layout.addLayout(row)
        row = QHBoxLayout()
        row.addWidget(self.manage)
        row.addWidget(self.reset)
        layout.addLayout(row)
        layout.addWidget(self.undo, 0, Qt.AlignmentFlag.AlignLeft)
        self.status = named(
            wrap_label("Open Insights to load your local dashboard."), "Insights status"
        )
        layout.addWidget(self.status)
        self.scroll_area = named(QScrollArea(), "Dashboard widgets")
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidgetResizable(True)
        self.host = named(QWidget(), "Dashboard grid")
        self.grid = QGridLayout(self.host)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setWidget(self.host)
        layout.addWidget(self.scroll_area)
        self.refresh_button.clicked.connect(self.refresh)
        self.first.dateChanged.connect(self.refresh)
        self.last.dateChanged.connect(self.refresh)
        self.project.currentIndexChanged.connect(self.refresh)
        self.area.currentIndexChanged.connect(self.refresh)
        self.edit_layout.toggled.connect(self.refresh)
        self.add.clicked.connect(lambda: self._edit_widget())
        self.manage.clicked.connect(self._manage)
        self.reset.clicked.connect(self._reset)
        self.undo.clicked.connect(self._undo)
        self.timer = QTimer(self)
        self.timer.setInterval(60_000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()

    def follow_day(self, today: date, zone: str) -> None:
        changed = self.zone != zone
        self.zone = zone
        current = (self.first.date().toPython(), self.last.date().toPython())
        if current == self._following_dates and current[1] != today:
            first = today - timedelta(days=6)
            self.first.blockSignals(True)
            self.last.blockSignals(True)
            self.first.setDate(QDate(first.year, first.month, first.day))
            self.last.setDate(QDate(today.year, today.month, today.day))
            self.first.blockSignals(False)
            self.last.blockSignals(False)
            self._following_dates = (first, today)
            changed = True
        if changed:
            self.refresh()

    def dates(self) -> MetricDateRange:
        return MetricDateRange(
            first=self.first.date().toPython(), last=self.last.date().toPython(), zone=self.zone
        )

    def run(self, operation: Coroutine[object, object, None]) -> asyncio.Task[None]:
        async def execute() -> None:
            try:
                await operation
            except asyncio.CancelledError:
                raise
            except DashboardRepairError as exc:
                self.repair_revision = exc.revision
                self.status.setText(str(exc))
            except Exception:
                self.status.setText(
                    "Insights could not load or save. Your existing layout and "
                    "editor input are kept. Refresh to review, then retry."
                )

        task = asyncio.create_task(execute())
        self.jobs.add(task)

        def finished(task: asyncio.Task[None]) -> None:
            self.jobs.discard(task)
            operation.close()

        task.add_done_callback(finished)
        return task

    def attach(
        self,
        backend: InsightsBackend,
        zone: str,
        projects: tuple[Project, ...],
        areas: tuple[LifeArea, ...],
    ) -> None:
        first_open = self.backend is None
        if self.backend is not None and self.backend.database is backend.database:
            self.backend.metrics.google_on = backend.metrics.google_on
        else:
            self.backend = backend
        self.zone, self.projects, self.areas = zone, projects, areas
        if first_open:
            from zoneinfo import ZoneInfo

            today = utc_now().astimezone(ZoneInfo(zone)).date()
            self.first.blockSignals(True)
            self.last.blockSignals(True)
            first = today - timedelta(days=6)
            self.first.setDate(QDate(first.year, first.month, first.day))
            self.last.setDate(QDate(today.year, today.month, today.day))
            self.first.blockSignals(False)
            self.last.blockSignals(False)
            self._following_dates = (first, today)
        for box, rows in ((self.project, projects), (self.area, areas)):
            selected = box.currentData()
            box.blockSignals(True)
            box.clear()
            box.addItem("All projects" if box is self.project else "All life areas", "")
            for record in rows:
                box.addItem(record.name, record.public_id)
            box.setCurrentIndex(max(0, box.findData(selected)))
            box.blockSignals(False)
        self.refresh()

    def refresh(self) -> None:
        if self.backend is None or not self.isVisible() or self._draining:
            return
        self.generation += 1
        if self._refresh_task is not None and not self._refresh_task.done():
            self._refresh_task.cancel()
        self._refresh_task = self.run(self._refresh(self.generation))

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        self.refresh()

    async def _refresh(self, generation: int) -> None:
        backend = self.backend
        assert backend is not None
        dates = self.dates()
        captured_at, self._next_capture = self._next_capture, None
        self.status.setText("Loading Insights…")
        saved = await backend.load(dates)
        definitions = await backend.definitions()
        prepared: list[tuple[WidgetSpec, MetricQueryResult | None, str]] = []
        queries: list[MetricQuery] = []
        for widget in saved.layout.widgets:
            query = resolved_widget_query(
                widget,
                dates,
                project_public_id=self.project.currentData() or None,
                area_public_id=self.area.currentData() or None,
            )
            queries.append(query)
        # First use can show a cheap current-state card while retained history loads.
        # Range changes keep the existing dashboard visible until its replacement is ready.
        if not self.cards:
            overdue = next(
                (
                    i
                    for i, query in enumerate(queries)
                    if query.metric_key == "tasks.current_overdue"
                ),
                None,
            )
            if overdue is not None:
                try:
                    ready = await backend.metrics.query(queries[overdue], captured_at=captured_at)
                    if generation != self.generation:
                        return
                    initial = [
                        (widget, ready if i == overdue else None, "Loading records...")
                        for i, widget in enumerate(saved.layout.widgets)
                    ]
                    self._publish(saved, definitions, initial)
                    self.status.setText("Loading the remaining Insights widgets...")
                except (ValueError, LookupError):
                    pass
        try:
            results = await backend.metrics.query_many(tuple(queries), captured_at=captured_at)
        except (ValueError, LookupError):
            # One unsupported widget/filter must not hide the other saved widgets.
            results = ()
        for index, (widget, query) in enumerate(zip(saved.layout.widgets, queries, strict=True)):
            extra = ""
            try:
                result = (
                    results[index]
                    if results
                    else await backend.metrics.query(
                        query,
                        captured_at=captured_at,
                    )
                )
                if widget.previous_period:
                    comparison = await backend.metrics.compare(query, captured_at=captured_at)
                    result = comparison.current
                    extra = comparison_caption(comparison)
                prepared.append((widget, result, extra))
            except (ValueError, LookupError):
                prepared.append(
                    (
                        widget,
                        None,
                        "Unavailable for these dates or filters. "
                        "Edit this widget or change the dashboard filters.",
                    )
                )
        if generation != self.generation:
            return
        self._publish(saved, definitions, prepared)

    def _publish(
        self,
        saved: SavedDashboard,
        definitions: tuple[MetricDefinition, ...],
        prepared: list[tuple[WidgetSpec, MetricQueryResult | None, str]],
    ) -> None:
        self.saved, self.definitions = saved, definitions
        self._rendered_revision = saved.revision
        for card in self.cards:
            take_widget(card)
            card.hide()
            card.deleteLater()
        self.cards = []
        self.rendered_widgets = saved.layout.widgets
        for widget, card_result, extra in prepared:
            card = named(QWidget(), "Widget " + widget.public_id)
            box = QVBoxLayout(card)
            box.setContentsMargins(0, 0, 0, 0)
            if card_result is not None:
                chart = MetricChartCard(display_result(card_result, widget, extra), card)
                # Details revalidate from the query/fingerprint; do not retain hundreds
                # of thousands of contributor models behind the chart's callback.
                detail_result = card_result.model_copy(update={"contributions": ()})
                chart.activated.connect(lambda bucket, r=detail_result: self._details(r, bucket))
                box.addWidget(chart)
                inspect = outline_button(
                    "Contributing records", "insights-contributors-" + widget.public_id
                )
                inspect.clicked.connect(
                    lambda _checked=False, r=detail_result: self._details(r, None)
                )
                box.addWidget(inspect)
            else:
                box.addWidget(wrap_label(widget.title + ": " + extra))
            if self.edit_layout.isChecked():
                controls = QHBoxLayout()
                for label, operation in (
                    ("Edit", "edit"),
                    ("Earlier", "earlier"),
                    ("Later", "later"),
                    ("Size", "size"),
                    ("Remove", "remove"),
                ):
                    button = outline_button(
                        label, f"insights-{operation}-{widget.public_id}", compact=True
                    )

                    def change(
                        _checked: bool = False,
                        op: str = operation,
                        identity: str = widget.public_id,
                        revision: int = saved.revision,
                    ) -> None:
                        self._change(identity, op, expected_revision=revision)

                    button.clicked.connect(change)
                    controls.addWidget(button)
                box.addLayout(controls)
            self.cards.append(card)
        self._pack()
        self.add.setEnabled(len(saved.layout.widgets) < 12)
        if any(result is not None for _, result, _ in prepared):
            self.data_ready.emit()
        self.status.setText(
            "Local records only. Dashboard filters apply where a widget has no override. "
            "Edit a widget to inspect its saved range and filters."
        )

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._pack()

    def _pack(self) -> None:
        if self.saved is None:
            return
        columns = 2 if self.width() >= 880 else 1
        for card in self.cards:
            take_widget(card)
        row = column = 0
        for card, widget in zip(self.cards, self.rendered_widgets, strict=True):
            span = columns if widget.size is not WidgetSize.SMALL else 1
            if column + span > columns:
                row, column = row + 1, 0
            self.grid.addWidget(card, row, column, 1, span)
            card.show()
            column += span
            if column >= columns:
                row, column = row + 1, 0
        for index in range(2):
            self.grid.setColumnStretch(index, 1 if index < columns else 0)

    def _dialog(self, dialog: InsightsDialog) -> None:
        self.dialogs.append(dialog)
        dialog.finished.connect(lambda _result: self.run(self._release_dialog(dialog)))
        self.dialog_requested.emit(dialog)

    async def _release_dialog(self, dialog: InsightsDialog) -> None:
        await dialog.drain()
        if dialog in self.dialogs:
            self.dialogs.remove(dialog)
        dialog.deleteLater()

    def _edit_widget(self, widget: WidgetSpec | None = None) -> None:
        if self.backend is None or self.saved is None:
            return
        dialog = WidgetEditor(
            self.backend,
            self.dates(),
            self.definitions,
            self.projects,
            self,
            widget,
            areas=self.areas,
            global_project=self.project.currentData() or None,
            global_area=self.area.currentData() or None,
        )
        revision = self.saved.revision
        layout = self.saved.layout

        def save(value: object) -> None:
            if not isinstance(value, WidgetSpec):
                return
            widgets = list(layout.widgets)
            index = next(
                (i for i, old in enumerate(widgets) if old.public_id == value.public_id), None
            )
            if index is None:
                widgets.append(value)
            else:
                widgets[index] = value
            try:
                updated = DashboardLayout(widgets=tuple(widgets))
            except ValueError:
                dialog.message.setText("A dashboard can hold at most twelve widgets.")
                return

            async def write() -> None:
                await self._save(updated, revision)
                dialog.accept()

            dialog.write(write())

        dialog.saved.connect(save)
        self._dialog(dialog)

    async def _save(self, layout: DashboardLayout, revision: int) -> None:
        assert self.backend is not None
        payload = f"{revision}:" + layout.model_dump_json()
        command = self._commands.setdefault(payload, new_public_id())
        saved = await self.backend.save(layout, revision, command)
        self.saved = saved
        self.refresh()

    def _change(
        self, identity: str | int, operation: str, *, expected_revision: int | None = None
    ) -> None:
        if self.saved is None or self._write_task is not None and not self._write_task.done():
            return
        saved = self.saved
        expected = self._rendered_revision if expected_revision is None else expected_revision
        if expected != saved.revision or expected != self._rendered_revision:
            self.status.setText("Layout changed; refreshing before another edit.")
            self.refresh()
            return
        if isinstance(identity, int):
            if not 0 <= identity < len(self.rendered_widgets):
                return
            identity = self.rendered_widgets[identity].public_id
        widgets = list(saved.layout.widgets)
        index = next((i for i, item in enumerate(widgets) if item.public_id == identity), None)
        if index is None:
            return
        if operation == "edit":
            self._edit_widget(widgets[index])
            return
        identity = widgets[index].public_id
        if operation == "remove":
            widgets.pop(index)
        elif operation == "size":
            sizes = list(WidgetSize)
            widgets[index] = widgets[index].model_copy(
                update={"size": sizes[(sizes.index(widgets[index].size) + 1) % len(sizes)]}
            )
        else:
            other = index + (-1 if operation == "earlier" else 1)
            if not 0 <= other < len(widgets):
                return
            widgets[index], widgets[other] = widgets[other], widgets[index]

        async def write() -> None:
            await self._save(DashboardLayout(widgets=tuple(widgets)), saved.revision)
            if operation == "remove" and self.saved is not None:
                self._removed = (saved.layout, saved.revision + 1)
            self.undo.setVisible(self._removed is not None)
            if self._refresh_task:
                await self._refresh_task
            name = f"insights-{operation}-{identity}"
            target = next(
                (
                    found
                    for card in self.cards
                    if (found := card.findChild(QWidget, name)) is not None
                ),
                None,
            )
            (target or self.add).setFocus(Qt.FocusReason.OtherFocusReason)

        self._write_task = self.run(write())

    def _reset(self) -> None:
        if self._write_task is not None and not self._write_task.done():
            return
        revision = self.saved.revision if self.saved else self.repair_revision
        if revision is None:
            return
        before = self.saved

        async def reset() -> None:
            await self._save(starter_layout(self.dates()), revision)
            if before is not None:
                self._removed = (before.layout, revision + 1)
            self.undo.setVisible(self._removed is not None)

        self._write_task = self.run(reset())

    def _undo(self) -> None:
        if self._write_task is not None and not self._write_task.done():
            return
        if self._removed is not None and self.saved is not None:
            layout, revision = self._removed

            async def undo() -> None:
                await self._save(layout, revision)
                self._removed = None
                self.undo.hide()

            self._write_task = self.run(undo())

    def _manage(self) -> None:
        if self.backend is None:
            return
        dialog = MetricManager(self.backend, self.zone, self.projects, self)
        dialog.changed.connect(self.refresh)
        self._dialog(dialog)
        dialog.run(dialog.reload())

    def _details(self, result: MetricQueryResult, bucket: str | None) -> None:
        assert self.backend is not None
        dialog = MetricDetails(self.backend, result, bucket, self)
        dialog.open_requested.connect(self.open_requested)
        dialog.stale.connect(self._stale_detail)
        self._dialog(dialog)
        dialog.run(dialog.load())

    def _stale_detail(self, result: object) -> None:
        if isinstance(result, MetricQueryResult):
            self._next_capture = result.captured_at_utc
            self.refresh()

    def has_unsaved_input(self) -> bool:
        return any(not d.isHidden() and d.has_unsaved_input() for d in self.dialogs)

    async def drain(self) -> None:
        self._draining = True
        self.timer.stop()
        if self._refresh_task:
            self._refresh_task.cancel()
        await asyncio.gather(*(d.drain() for d in self.dialogs), return_exceptions=True)
        while pending := [job for job in self.jobs if not job.done()]:
            await asyncio.gather(*pending, return_exceptions=True)
