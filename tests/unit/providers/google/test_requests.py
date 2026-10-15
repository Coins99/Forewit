"""The reasons in a Google error payload, wherever Google puts them."""

import pytest

from mailbrief.providers.google.requests import error_reasons


def test_reasons_come_from_errors_and_details() -> None:
    payload = {
        "error": {
            "message": "private-token",
            "errors": [{"reason": "rateLimitExceeded", "message": "private"}],
            "details": [{"@type": "ErrorInfo", "reason": "SERVICE_DISABLED"}],
        }
    }
    assert error_reasons(payload) == {"rateLimitExceeded", "SERVICE_DISABLED"}


@pytest.mark.parametrize(
    "payload",
    [
        None,
        [],
        "private",
        {"error": "private"},
        {"error": {"errors": "private", "details": {"reason": "not-a-list"}}},
        {"error": {"errors": [None, "x", {"reason": 3}], "details": [{"reason": []}]}},
    ],
)
def test_malformed_payloads_have_no_reasons(payload: object) -> None:
    assert error_reasons(payload) == set()
