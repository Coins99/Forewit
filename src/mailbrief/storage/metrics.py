"""Metric ORM adapters. Services own transactions and optimistic revision checks."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.metric_entries import (
    DefinedMetricEntry,
    MetricDefinition,
    MetricEntry,
    MetricEntryChange,
    MetricEntryState,
    validate_entry_history,
)
from mailbrief.storage.tables import MetricDefinitionTable, MetricEntryChangeTable, MetricEntryTable


def definition_from_row(row: MetricDefinitionTable) -> MetricDefinition:
    return MetricDefinition.model_validate(
        {name: getattr(row, name) for name in MetricDefinition.model_fields}
    )


def state_from_row(row: MetricEntryTable) -> MetricEntryState:
    return MetricEntryState(
        value_scaled=row.value_scaled,
        local_date=row.local_date,
        zone=row.zone,
        project_public_id=row.project_public_id,
        deleted=row.deleted_at_utc is not None,
    )


def entry_from_row(row: MetricEntryTable, definition: MetricDefinitionTable) -> MetricEntry:
    state = state_from_row(row)
    DefinedMetricEntry(definition=definition_from_row(definition), entry=state)
    return MetricEntry(
        **state.model_dump(),
        public_id=row.public_id,
        definition_id=row.definition_id,
        definition_version=row.definition_version,
        revision=row.revision,
        created_at_utc=row.created_at_utc,
        updated_at_utc=row.updated_at_utc,
    )


def change_from_row(row: MetricEntryChangeTable) -> MetricEntryChange:
    return MetricEntryChange(
        revision=row.revision,
        operation=row.operation,
        state=MetricEntryState.model_validate(row.state_json),
        corrects_revision=row.corrects_revision,
        recorded_at_utc=row.recorded_at_utc,
    )


class MetricRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def definition(self, public_id: str) -> MetricDefinitionTable | None:
        result = await self.session.scalars(
            select(MetricDefinitionTable)
            .where(MetricDefinitionTable.public_id == public_id)
            .execution_options(populate_existing=True)
        )
        return result.one_or_none()

    async def entry(self, public_id: str) -> MetricEntryTable | None:
        result = await self.session.scalars(
            select(MetricEntryTable)
            .where(MetricEntryTable.public_id == public_id)
            .execution_options(populate_existing=True)
        )
        return result.one_or_none()

    async def changes(self, row: MetricEntryTable) -> tuple[MetricEntryChange, ...]:
        rows = await self.session.scalars(
            select(MetricEntryChangeTable)
            .where(MetricEntryChangeTable.entry_id == row.public_id)
            .order_by(MetricEntryChangeTable.revision)
        )
        changes = tuple(change_from_row(record) for record in rows)
        validate_entry_history(changes, state_from_row(row), row.revision)
        return changes
