"""Composing Google Calendar: its own scopes and vault entry, owned HTTP, calendar wording."""

import json
from pathlib import Path

import pytest

from mailbrief.config import Settings
from mailbrief.providers.gcal import factory
from mailbrief.providers.gcal.auth import CALENDAR_SCOPES, CalendarCredentialStore
from mailbrief.providers.gcal.errors import CalendarSetupError
from mailbrief.providers.gmail.oauth import LoopbackAuthorization
from tests.unit.providers.gcal.test_client import MemoryVault


def client_file(tmp_path: Path) -> Path:
    path = tmp_path / "client.json"
    path.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "fake.apps.googleusercontent.com",
                    "client_secret": "fake-secret",
                }
            }
        )
    )
    return path


async def test_factory_asks_for_the_calendar_scopes_and_closes_http(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    asked: list[tuple[str, ...]] = []

    def loopback(*, scopes: tuple[str, ...]) -> LoopbackAuthorization:
        asked.append(scopes)
        return LoopbackAuthorization(scopes=scopes)

    vault = MemoryVault()
    monkeypatch.setattr(factory, "CalendarCredentialStore", lambda: CalendarCredentialStore(vault))
    monkeypatch.setattr(factory, "LoopbackAuthorization", loopback)
    settings = Settings(gmail_oauth_client_path=client_file(tmp_path))
    async with factory.google_calendar(settings) as reader:
        http = reader.auth._http
        assert not http.is_closed and not http.follow_redirects
        assert await reader.auth.saved_account() is None
        assert asked == [CALENDAR_SCOPES]
    assert http.is_closed


async def test_factory_needs_a_usable_client_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MAILBRIEF_GMAIL_OAUTH_CLIENT_PATH", raising=False)
    with pytest.raises(CalendarSetupError, match="Settings first"):
        async with factory.google_calendar(Settings()):
            pass
    broken = tmp_path / "web.json"
    broken.write_text(json.dumps({"web": {"client_id": "x", "client_secret": "y"}}))
    with pytest.raises(CalendarSetupError, match="can't be used") as caught:
        async with factory.google_calendar(Settings(gmail_oauth_client_path=broken)):
            pass
    assert "Gmail" not in str(caught.value)
