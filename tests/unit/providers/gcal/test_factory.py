"""Google Calendar is composed over the one Google sign-in (ADR 0019)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

import pytest

from mailbrief.config import Settings
from mailbrief.providers.gcal import factory


class Session:
    async def access_token(self) -> str:
        return "token"

    async def invalidate_access_token(self, rejected_token: str) -> None:
        return None


async def test_calendar_reads_use_the_gmail_sign_in(monkeypatch: pytest.MonkeyPatch) -> None:
    session = Session()
    seen: list[Settings] = []

    @asynccontextmanager
    async def gmail_auth(settings: Settings) -> AsyncIterator[Session]:
        seen.append(settings)
        yield session

    monkeypatch.setattr(factory, "gmail_auth", gmail_auth)
    settings = Settings()
    async with factory.google_calendar(settings) as reader:
        assert reader.auth is cast(object, session)
    assert seen == [settings]
