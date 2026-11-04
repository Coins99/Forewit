"""Destinations validate identity without retaining searched or owner-written text."""

from datetime import date

import pytest
from pydantic import TypeAdapter, ValidationError

from mailbrief.domain.routes import CalendarRoute, MessageRoute, Route, TaskRoute

ID = "00000000-0000-4000-8000-000000000001"


def test_route_serialization_preserves_canonical_identity() -> None:
    adapter = TypeAdapter[Route](Route)
    route = CalendarRoute(day=date(2026, 11, 2), entry=("event", ID))
    assert adapter.validate_json(adapter.dump_json(route)) == route
    assert adapter.validate_python({"kind": "task", "public_id": ID}) == TaskRoute(public_id=ID)


@pytest.mark.parametrize(
    "value",
    [
        {"kind": "task", "public_id": "not-an-id"},
        {"kind": "task", "public_id": ID, "body": "copied task text"},
        {"kind": "brief", "day": "2026-11-02"},
        {"kind": "message", "account_email": "", "message_id": "message"},
        {"kind": "calendar", "day": "2026-11-02", "entry": ["google", "calendar"]},
        {"kind": "calendar", "day": "2026-11-02", "entry": ["unknown", ID]},
        {"kind": "page", "page": "unknown"},
    ],
)
def test_invalid_destinations_are_rejected(value: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter[Route](Route).validate_python(value)


def test_mail_identity_is_not_in_diagnostic_repr() -> None:
    route = MessageRoute(account_email="owner@example.com", message_id="private-message")
    assert "owner@example.com" not in repr(route)
    assert "private-message" not in repr(route)
