"""Strict bounded metric-record validation for untrusted current-schema backups."""

import json
import sqlite3
from datetime import UTC, datetime
from uuid import UUID

from mailbrief.domain.metric_entries import (
    DefinedMetricEntry,
    MetricDefinition,
    MetricEntryChange,
    MetricEntryState,
    validate_entry_history,
)


def _time(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Invalid metric timestamp.")
    parsed = datetime.fromisoformat(value)
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def validate_metric_records(connection: sqlite3.Connection) -> None:
    """Schema/FK validation runs first. Do not expose any record text in exceptions."""
    definitions: dict[str, MetricDefinition] = {}
    for values in connection.execute(
        "SELECT public_id,name,unit,kind,scale,allow_negative,version,revision,archived,"
        "created_at_utc,updated_at_utc FROM metric_definitions"
    ):
        (
            identity,
            name,
            unit,
            kind,
            scale,
            negative,
            version,
            revision,
            archived,
            created,
            updated,
        ) = values
        UUID(identity)
        definition = MetricDefinition(
            public_id=identity,
            name=name,
            unit=unit,
            kind=kind,
            scale=scale,
            allow_negative=bool(negative),
            version=version,
            revision=revision,
            archived=bool(archived),
            created_at_utc=_time(created),
            updated_at_utc=_time(updated),
        )
        if definition.name != name or definition.unit != unit:
            raise ValueError("Metric definition is not canonical.")
        definitions[identity] = definition
    for (
        identity,
        definition_id,
        value,
        day,
        zone,
        project,
        revision,
        created,
        updated,
        deleted,
    ) in connection.execute(
        "SELECT public_id,definition_id,value_scaled,local_date,zone,project_public_id,revision,"
        "created_at_utc,updated_at_utc,deleted_at_utc FROM metric_entries"
    ):
        UUID(identity)
        definition = definitions[definition_id]
        state = MetricEntryState(
            value_scaled=value,
            local_date=day,
            zone=zone,
            project_public_id=project,
            deleted=deleted is not None,
        )
        DefinedMetricEntry(definition=definition, entry=state)
        _time(created)
        _time(updated)
        if deleted is not None:
            _time(deleted)
        changes: list[MetricEntryChange] = []
        for rev, operation, payload, corrects, recorded in connection.execute(
            "SELECT revision,operation,state_json,corrects_revision,recorded_at_utc "
            "FROM metric_entry_changes WHERE entry_id = ? ORDER BY revision",
            (identity,),
        ):
            change = MetricEntryChange(
                revision=rev,
                operation=operation,
                state=MetricEntryState.model_validate(json.loads(payload)),
                corrects_revision=corrects,
                recorded_at_utc=_time(recorded),
            )
            DefinedMetricEntry(definition=definition, entry=change.state)
            changes.append(change)
        validate_entry_history(tuple(changes), state, revision)
    for command, operation, kind, result_id, result_revision, created in connection.execute(
        "SELECT command_id,operation,result_kind,result_public_id,result_revision,created_at_utc "
        "FROM metric_commands"
    ):
        UUID(command)
        UUID(result_id)
        _time(created)
        if (operation in {"define", "configure"}) != (kind == "definition"):
            raise ValueError("Metric receipt has an invalid result kind.")
        table = "metric_definitions" if kind == "definition" else "metric_entries"
        # Table names are fixed application constants, never record-provided SQL.
        saved = connection.execute(
            f"SELECT revision FROM {table} WHERE public_id = ?", (result_id,)
        ).fetchone()
        if saved is not None and saved[0] < result_revision:
            raise ValueError("Metric receipt extends beyond its result.")
