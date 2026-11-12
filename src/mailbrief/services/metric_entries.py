"""Revisioned exact owner logs with atomic corrections and text-free retry receipts."""

import hashlib
import json
from collections.abc import Awaitable, Callable
from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.common import new_public_id, utc_now
from mailbrief.domain.metric_entries import (
    DefinedMetricEntry,
    MetricDefinition,
    MetricDefinitionSpec,
    MetricEntry,
    MetricEntryChange,
    MetricEntryInput,
    MetricEntryState,
)
from mailbrief.services.writes import commit_once
from mailbrief.storage.metrics import (
    MetricRepository,
    definition_from_row,
    entry_from_row,
    state_from_row,
)
from mailbrief.storage.tables import (
    MetricCommandTable,
    MetricDefinitionTable,
    MetricEntryChangeTable,
    MetricEntryTable,
    ProjectTable,
)


class MetricConflictError(ValueError):
    def __init__(self) -> None:
        super().__init__("The metric changed or the command was already used. Reload and review.")


class MetricNotFoundError(LookupError):
    def __init__(self) -> None:
        super().__init__("The metric record is no longer available.")


class MetricStorageError(RuntimeError):
    def __init__(self) -> None:
        super().__init__("The metric change could not be saved. Retry the same command or reload.")


def _own_fields(spec: MetricDefinitionSpec) -> MetricDefinitionSpec:
    """``spec`` revalidated from its definition fields alone. A saved ``MetricDefinition``
    is a spec too, and its identity, revision and dates are not part of one."""
    return MetricDefinitionSpec.model_validate(
        spec.model_dump(include=set(MetricDefinitionSpec.model_fields))
    )


class MetricEntryService:
    """Use a dedicated service-owned session; each mutation reserves and commits once."""

    def __init__(self, session: AsyncSession, *, clock: Callable[[], datetime] = utc_now) -> None:
        self.session = session
        self.clock = clock
        self.repository = MetricRepository(session)

    async def _definition(self, public_id: str) -> MetricDefinitionTable:
        row = await self.repository.definition(public_id)
        if row is None:
            raise MetricNotFoundError()
        return row

    async def _entry(self, public_id: str) -> MetricEntryTable:
        row = await self.repository.entry(public_id)
        if row is None:
            raise MetricNotFoundError()
        return row

    async def get_definition(self, public_id: str) -> MetricDefinition:
        return definition_from_row(await self._definition(public_id))

    async def definitions(self, *, include_archived: bool = False) -> tuple[MetricDefinition, ...]:
        statement = select(MetricDefinitionTable).order_by(MetricDefinitionTable.public_id)
        if not include_archived:
            statement = statement.where(MetricDefinitionTable.archived.is_(False))
        return tuple(definition_from_row(row) for row in await self.session.scalars(statement))

    async def get_entry(self, public_id: str) -> MetricEntry:
        row = await self._entry(public_id)
        return entry_from_row(row, await self._definition(row.definition_id))

    async def history(self, public_id: str) -> tuple[MetricEntryChange, ...]:
        return await self.repository.changes(await self._entry(public_id))

    async def entries(
        self,
        definition_id: str,
        *,
        include_deleted: bool = False,
        after: tuple[str, str] | None = None,
        limit: int = 100,
        newest_first: bool = False,
    ) -> tuple[MetricEntry, ...]:
        """Bounded owner list, oldest date first, or the latest first with ``newest_first``;
        ``after`` is the last row of the page before. Aggregates use their own uncapped
        source reader."""
        from datetime import date

        from sqlalchemy import or_

        if not 1 <= limit <= 200:
            raise ValueError("Choose a page size between 1 and 200.")
        definition = await self._definition(definition_id)
        statement = select(MetricEntryTable).where(MetricEntryTable.definition_id == definition_id)
        if not include_deleted:
            statement = statement.where(MetricEntryTable.deleted_at_utc.is_(None))
        if after is not None:
            try:
                day = date.fromisoformat(after[0])
            except ValueError:
                raise ValueError("The metric list cursor is invalid.") from None
            same_day = MetricEntryTable.local_date == day
            statement = statement.where(
                or_(
                    MetricEntryTable.local_date < day,
                    same_day & (MetricEntryTable.public_id < after[1]),
                )
                if newest_first
                else or_(
                    MetricEntryTable.local_date > day,
                    same_day & (MetricEntryTable.public_id > after[1]),
                )
            )
        order = (
            (MetricEntryTable.local_date.desc(), MetricEntryTable.public_id.desc())
            if newest_first
            else (MetricEntryTable.local_date, MetricEntryTable.public_id)
        )
        rows = await self.session.scalars(statement.order_by(*order).limit(limit))
        return tuple(entry_from_row(row, definition) for row in rows)

    async def _command(
        self,
        command_id: str,
        operation: str,
        payload: object,
        kind: str,
        change: Callable[[datetime], Awaitable[str]],
    ) -> MetricDefinition | MetricEntry:
        try:
            identity = str(UUID(command_id))
        except (ValueError, AttributeError):
            raise ValueError("A metric command needs a UUID.") from None
        digest = hashlib.sha256(
            json.dumps((operation, payload), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

        async def run(now: datetime) -> MetricDefinition | MetricEntry:
            await self.session.execute(text("BEGIN IMMEDIATE"))
            receipt = await self.session.get(MetricCommandTable, identity, populate_existing=True)
            if receipt is not None:
                if (receipt.operation, receipt.input_hash, receipt.result_kind) != (
                    operation,
                    digest,
                    kind,
                ):
                    raise MetricConflictError()
                public_id = receipt.result_public_id
            else:
                public_id = await change(now)
            result = (
                await self.get_definition(public_id)
                if kind == "definition"
                else await self.get_entry(public_id)
            )
            if receipt is not None:
                if result.revision != receipt.result_revision:
                    raise MetricConflictError()
            else:
                self.session.add(
                    MetricCommandTable(
                        command_id=identity,
                        operation=operation,
                        input_hash=digest,
                        result_kind=kind,
                        result_public_id=public_id,
                        result_revision=result.revision,
                        created_at_utc=now,
                    )
                )
            return result

        try:
            return await commit_once(self.session, self.clock, run)
        except SQLAlchemyError:
            # Driver exceptions can contain statement parameters (owner names/values).
            raise MetricStorageError() from None

    async def define(self, spec: MetricDefinitionSpec, *, command_id: str) -> MetricDefinition:
        spec = _own_fields(spec)

        async def change(now: datetime) -> str:
            row = MetricDefinitionTable(
                **spec.model_dump(mode="json"),
                public_id=new_public_id(),
                version=1,
                revision=1,
                archived=False,
                created_at_utc=now,
                updated_at_utc=now,
            )
            self.session.add(row)
            await self.session.flush()
            return row.public_id

        result = await self._command(
            command_id, "define", spec.model_dump(mode="json"), "definition", change
        )
        assert isinstance(result, MetricDefinition)
        return result

    async def configure(
        self,
        public_id: str,
        expected_revision: int,
        spec: MetricDefinitionSpec,
        *,
        archived: bool,
        command_id: str,
    ) -> MetricDefinition:
        spec = _own_fields(spec)

        async def change(now: datetime) -> str:
            row = await self._definition(public_id)
            if row.revision != expected_revision:
                raise MetricConflictError()
            old = definition_from_row(row)
            if (old.unit, old.kind, old.scale, old.allow_negative) != (
                spec.unit,
                spec.kind,
                spec.scale,
                spec.allow_negative,
            ):
                raise ValueError(
                    "Create a new metric to change its unit, precision or measurement kind."
                )
            row.name, row.archived = spec.name, archived
            row.revision += 1
            row.updated_at_utc = now
            return public_id

        result = await self._command(
            command_id,
            "configure",
            (public_id, expected_revision, spec.model_dump(mode="json"), archived),
            "definition",
            change,
        )
        assert isinstance(result, MetricDefinition)
        return result

    async def _apply(
        self,
        row: MetricEntryTable,
        state: MetricEntryState,
        operation: str,
        now: datetime,
        *,
        corrects: int | None = None,
    ) -> None:
        definition = await self._definition(row.definition_id)
        DefinedMetricEntry(definition=definition_from_row(definition), entry=state)
        if state.project_public_id is not None:
            project = await self.session.scalar(
                select(ProjectTable).where(ProjectTable.public_id == state.project_public_id)
            )
            if project is None:
                raise ValueError("Choose a saved project for this observation.")
        row.value_scaled, row.local_date, row.zone = (
            state.value_scaled,
            state.local_date,
            state.zone,
        )
        row.project_public_id = state.project_public_id
        row.deleted_at_utc = now if state.deleted else None
        row.revision += 1
        row.updated_at_utc = now
        await self.session.flush([row])
        self.session.add(
            MetricEntryChangeTable(
                entry_id=row.public_id,
                revision=row.revision,
                operation=operation,
                state_json=state.model_dump(mode="json"),
                corrects_revision=corrects,
                recorded_at_utc=now,
            )
        )
        await self.session.flush()

    async def create(
        self,
        definition_id: str,
        entry: MetricEntryInput,
        *,
        command_id: str,
    ) -> MetricEntry:
        async def change(now: datetime) -> str:
            definition = await self._definition(definition_id)
            if definition.archived:
                raise ValueError("Restore the metric before adding observations.")
            row = MetricEntryTable(
                public_id=new_public_id(),
                definition_id=definition_id,
                definition_version=definition.version,
                revision=0,
                created_at_utc=now,
            )
            self.session.add(row)
            # Assign required fields before any query can autoflush the new row.
            row.value_scaled, row.local_date, row.zone = (
                entry.value_scaled,
                entry.local_date,
                entry.zone,
            )
            row.project_public_id, row.updated_at_utc = entry.project_public_id, now
            with self.session.no_autoflush:
                await self._apply(row, MetricEntryState(**entry.model_dump()), "create", now)
            return row.public_id

        result = await self._command(
            command_id, "create", (definition_id, entry.model_dump(mode="json")), "entry", change
        )
        assert isinstance(result, MetricEntry)
        return result

    async def mutate(
        self,
        public_id: str,
        expected_revision: int,
        operation: str,
        *,
        entry: MetricEntryInput | None = None,
        command_id: str,
    ) -> MetricEntry:
        """Edit, delete, restore or undo exactly the revision the owner reviewed."""
        if operation not in {"edit", "delete", "restore", "undo"} or (entry is not None) != (
            operation == "edit"
        ):
            raise ValueError("Choose a valid metric entry operation.")

        async def change(now: datetime) -> str:
            row = await self._entry(public_id)
            if row.revision != expected_revision:
                raise MetricConflictError()
            changes = await self.repository.changes(row)
            state = state_from_row(row)
            if operation == "edit":
                if state.deleted:
                    raise MetricConflictError()
                assert entry is not None
                state = MetricEntryState(**entry.model_dump())
            elif operation == "undo":
                state = (
                    changes[-2].state
                    if len(changes) > 1
                    else state.model_copy(update={"deleted": True})
                )
            else:
                deleted = operation == "delete"
                if state.deleted == deleted:
                    raise MetricConflictError()
                state = state.model_copy(update={"deleted": deleted})
            await self._apply(
                row,
                state,
                operation,
                now,
                corrects=expected_revision if operation == "undo" else None,
            )
            return public_id

        result = await self._command(
            command_id,
            operation,
            (
                public_id,
                expected_revision,
                None if entry is None else entry.model_dump(mode="json"),
            ),
            "entry",
            change,
        )
        assert isinstance(result, MetricEntry)
        return result
