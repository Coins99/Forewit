"""What is left of the separate calendar sign-in (ADR 0019), and calendar access guidance."""

import pytest
from pydantic import SecretStr

from mailbrief.providers.gcal.auth import (
    ENTRY,
    SERVICE,
    CalendarCredentialStore,
    forget_legacy_sign_in,
)
from mailbrief.providers.gcal.errors import calendar_permission_guidance
from mailbrief.providers.google.credentials import RefreshCredential


class MemoryVault:
    """Any vault entry, kept in memory."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, entry: str) -> str | None:
        return self.values.get((service, entry))

    def set_password(self, service: str, entry: str, value: str) -> None:
        self.values[(service, entry)] = value

    def delete_password(self, service: str, entry: str) -> None:
        del self.values[(service, entry)]


def test_the_old_calendar_sign_in_is_deleted_once() -> None:
    vault = MemoryVault()
    assert forget_legacy_sign_in(vault) is False
    CalendarCredentialStore(vault).save(
        RefreshCredential(
            client_id="client", email_address="owner@example.com", refresh_token=SecretStr("r")
        )
    )
    assert forget_legacy_sign_in(vault) is True
    assert CalendarCredentialStore(vault).load() is None
    assert forget_legacy_sign_in(vault) is False


def test_an_unusable_old_entry_is_deleted_too() -> None:
    vault = MemoryVault()
    vault.set_password(SERVICE, ENTRY, "{}")
    assert forget_legacy_sign_in(vault) is True
    assert vault.get_password(SERVICE, ENTRY) is None


def payload(*reasons: str, field: str = "errors") -> dict[str, object]:
    return {"error": {field: [{"reason": reason} for reason in reasons]}}


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        (payload("SERVICE_DISABLED", field="details"), "API is disabled"),
        (payload("insufficientPermissions"), "Allow Google Calendar"),
        (payload("domainPolicy"), "administrator"),
        (payload("dailyLimitExceeded"), "quota is used up"),
        (payload("quotaExceeded"), "quota is used up"),
        (payload("forbidden"), "denied access; choose Allow Google Calendar"),
        ({"error": {"errors": "nope"}}, "denied access"),
        ([], "denied access"),
    ],
)
def test_permission_guidance(given: object, expected: str) -> None:
    assert expected in calendar_permission_guidance(given)
