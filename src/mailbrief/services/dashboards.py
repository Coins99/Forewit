"""Offline singleton layout, atomic revisions and retry-safe whole-layout writes."""

import hashlib
import json
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.common import new_public_id
from mailbrief.domain.dashboards import (
    ChartKind,
    DashboardLayout,
    SavedDashboard,
    ValidatedWidget,
    WidgetSpec,
)
from mailbrief.domain.metrics import (
    MetricDateRange,
    MetricDimension,
    MetricQuery,
    builtin_metric,
    custom_metric,
)
from mailbrief.services.metric_entries import (
    MetricConflictError,
    MetricEntryService,
    MetricStorageError,
)
from mailbrief.services.writes import committed
from mailbrief.storage.tables import DashboardCommandTable, DashboardTable


class DashboardRepairError(ValueError):
    def __init__(self, revision: int) -> None:
        self.revision = revision
        super().__init__("This layout is unsupported or damaged. Reset it to the starter layout.")


def starter_layout(dates: MetricDateRange) -> DashboardLayout:
    widgets = []
    for key, chart, group in (
        ("tasks.completions", ChartKind.NUMBER, None),
        ("tasks.current_overdue", ChartKind.NUMBER, None),
        ("tasks.completions", ChartKind.BAR, None),
        ("tasks.completions", ChartKind.BAR, MetricDimension.PROJECT),
    ):
        descriptor = builtin_metric(key)
        widgets.append(
            WidgetSpec(
                public_id=new_public_id(),
                title=descriptor.label,
                chart=chart,
                query=MetricQuery(
                    metric_key=key, reducer=descriptor.reducer, dates=dates, group_by=group
                ),
            )
        )
    return DashboardLayout(widgets=tuple(widgets))


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _saved(row: DashboardTable) -> SavedDashboard:
        try:
            return SavedDashboard(
                revision=row.revision, layout=DashboardLayout.model_validate(row.layout_json)
            )
        except ValueError:
            raise DashboardRepairError(row.revision) from None

    async def load(self, dates: MetricDateRange) -> SavedDashboard:
        async def run() -> SavedDashboard:
            await self.session.execute(text("BEGIN IMMEDIATE"))
            row = await self.session.get(DashboardTable, 1, populate_existing=True)
            if row is None:
                row = DashboardTable(
                    id=1, revision=1, layout_json=starter_layout(dates).model_dump(mode="json")
                )
                self.session.add(row)
            return self._saved(row)

        return await committed(self.session, run())

    async def save(
        self, layout: DashboardLayout, expected_revision: int, *, command_id: str
    ) -> SavedDashboard:
        layout = DashboardLayout.model_validate(layout.model_dump())
        try:
            command_id = str(UUID(command_id))
        except ValueError:
            raise ValueError("A dashboard command needs a UUID.") from None
        digest = hashlib.sha256(
            json.dumps(
                (expected_revision, layout.model_dump(mode="json")),
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

        async def run() -> SavedDashboard:
            await self.session.execute(text("BEGIN IMMEDIATE"))
            row = await self.session.get(DashboardTable, 1, populate_existing=True)
            receipt = await self.session.get(DashboardCommandTable, command_id)
            if row is None:
                raise MetricConflictError()
            if receipt is not None:
                if receipt.input_hash != digest or receipt.result_revision != row.revision:
                    raise MetricConflictError()
                return self._saved(row)
            if row.revision != expected_revision:
                raise MetricConflictError()
            for widget in layout.widgets:
                if widget.query.metric_key.startswith("custom."):
                    definition = await MetricEntryService(self.session).get_definition(
                        widget.query.metric_key.split(".")[1]
                    )
                    suffix = "amount" if definition.kind.value == "amount" else "point"
                    if widget.query.metric_key != f"custom.{definition.public_id}.{suffix}":
                        raise ValueError("The custom metric reference is invalid.")
                    descriptor = custom_metric(
                        widget.query.metric_key, definition.name, definition.unit, definition.kind
                    )
                else:
                    descriptor = builtin_metric(widget.query.metric_key)
                ValidatedWidget(widget=widget, descriptor=descriptor)
            row.layout_json = layout.model_dump(mode="json")
            row.revision += 1
            self.session.add(
                DashboardCommandTable(
                    command_id=command_id, input_hash=digest, result_revision=row.revision
                )
            )
            return self._saved(row)

        try:
            return await committed(self.session, run())
        except SQLAlchemyError:
            raise MetricStorageError() from None
