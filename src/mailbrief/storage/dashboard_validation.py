"""Typed layout and receipt validation for untrusted recovery profiles."""

import json
import sqlite3
from uuid import UUID

from mailbrief.domain.dashboards import DashboardLayout, ValidatedWidget
from mailbrief.domain.metric_entries import MeasurementKind
from mailbrief.domain.metrics import builtin_metric, custom_metric


def validate_dashboard_records(connection: sqlite3.Connection) -> None:
    revision = 0
    for identity, revision, payload in connection.execute(
        "SELECT id,revision,layout_json FROM dashboards"
    ):
        if identity != 1 or revision < 1:
            raise ValueError("Invalid dashboard.")
        layout = DashboardLayout.model_validate(json.loads(payload))
        for widget in layout.widgets:
            key = widget.query.metric_key
            if key.startswith("custom."):
                parts = key.split(".")
                if len(parts) != 3:
                    raise ValueError("Invalid metric reference.")
                definition = connection.execute(
                    "SELECT name,unit,kind,version FROM metric_definitions WHERE public_id=?",
                    (parts[1],),
                ).fetchone()
                if definition is None or parts[2] != (
                    "amount" if definition[2] == "amount" else "point"
                ):
                    raise ValueError("Invalid metric reference.")
                descriptor = custom_metric(
                    key, definition[0], definition[1], MeasurementKind(definition[2])
                )
            else:
                descriptor = builtin_metric(key)
            ValidatedWidget(widget=widget, descriptor=descriptor)
    for command, digest, result_revision in connection.execute(
        "SELECT command_id,input_hash,result_revision FROM dashboard_commands"
    ):
        if (
            str(UUID(command)) != command
            or len(digest) != 64
            or not (1 <= result_revision <= revision)
        ):
            raise ValueError("Invalid dashboard receipt.")
