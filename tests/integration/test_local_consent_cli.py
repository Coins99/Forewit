"""Terminal local permission: real persistence, synthetic accounts, no providers or vault."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from mailbrief.config import Settings
from mailbrief.diagnostics import gmail
from mailbrief.domain.messages import AccountIdentity, ProviderKind
from mailbrief.domain.preferences import PreferencesEdit
from mailbrief.ports.errors import AuthenticationRequiredError
from mailbrief.services.consent import has_local_consent
from mailbrief.storage.database import Database
from mailbrief.storage.repositories import AccountRepository
from tests.integration.test_preferences_cli import save_preferences

EMAIL = "local-owner@example.com"


def command(path: Path, *args: str) -> int:
    return gmail.main(["ai-consent", *args, "--database", str(path)])


async def read_grant(path: Path) -> bool:
    database = Database.from_path(path)
    try:
        async with database.session() as session:
            return await has_local_consent(session, EMAIL)
    finally:
        await database.dispose()


@pytest.fixture
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "mailbrief.sqlite3"
    save_preferences(path, PreferencesEdit(local_message_limit=7))

    async def seed() -> None:
        database = Database.from_path(path)
        try:
            async with database.session() as session:
                await AccountRepository(session).upsert(
                    AccountIdentity(
                        provider=ProviderKind.GMAIL,
                        provider_account_id="synthetic-local",
                        email_address=EMAIL,
                    )
                )
                await session.commit()
        finally:
            await database.dispose()

    asyncio.run(seed())
    monkeypatch.setattr(gmail, "_stored_account_email", AsyncMock(return_value=EMAIL))
    return path


def test_grant_discloses_account_and_limit_and_needs_typed_yes(
    profile: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(gmail, "_ask", AsyncMock(return_value="yes"))
    assert command(profile, "local", "grant") == 0
    text = capsys.readouterr().out
    assert f"Gmail account: {EMAIL}" in text
    assert "7 selected messages" in text
    assert asyncio.run(read_grant(profile))
    assert command(profile, "status") == 0
    text = capsys.readouterr().out
    assert "Local email analysis consent granted" in text
    assert "Groq consent not granted" in text


@pytest.mark.parametrize("answer", ["", "no", "YES"])
def test_declining_changes_no_permission(
    profile: Path, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    monkeypatch.setattr(gmail, "_ask", AsyncMock(return_value=answer))
    assert command(profile, "local", "grant") == 6
    assert not asyncio.run(read_grant(profile))


def test_revoke_needs_no_vault_or_prompt(profile: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gmail, "_ask", AsyncMock(return_value="yes"))
    assert command(profile, "local", "grant") == 0
    vault = AsyncMock(side_effect=AssertionError("Revoke must not need credentials"))
    prompt = AsyncMock(side_effect=AssertionError("Revoke must not prompt"))
    monkeypatch.setattr(gmail, "_stored_account_email", vault)
    monkeypatch.setattr(gmail, "_ask", prompt)
    assert command(profile, "local", "revoke") == 0
    assert not asyncio.run(read_grant(profile))
    vault.assert_not_called()
    prompt.assert_not_called()


@pytest.mark.parametrize("email", [None, "unknown@example.com"])
def test_missing_connection_or_sync_is_refused_before_prompt(
    profile: Path, monkeypatch: pytest.MonkeyPatch, email: str | None
) -> None:
    monkeypatch.setattr(gmail, "_stored_account_email", AsyncMock(return_value=email))
    prompt = AsyncMock(side_effect=AssertionError("No eligible account"))
    monkeypatch.setattr(gmail, "_ask", prompt)
    assert command(profile, "local", "grant") == 3
    assert not asyncio.run(read_grant(profile))
    prompt.assert_not_called()


@pytest.mark.parametrize("args", [("local",), ("local", "invalid"), ("local", "grant", "--yes")])
def test_invalid_local_command_is_refused(tmp_path: Path, args: tuple[str, ...]) -> None:
    path = tmp_path / "not-created.sqlite3"
    with pytest.raises(SystemExit) as caught:
        command(path, *args)
    assert caught.value.code == 2
    assert not path.exists()


@pytest.mark.parametrize("automatic", [False, True])
def test_local_mode_never_enters_cloud_brief_path(
    profile: Path, monkeypatch: pytest.MonkeyPatch, automatic: bool
) -> None:
    monkeypatch.setenv("MAILBRIEF_EXPERIMENTAL_LOCAL_AI", "true")
    save_preferences(profile, PreferencesEdit(analysis_mode="local"))
    forbidden = AsyncMock(side_effect=AssertionError("A local brief must not construct cloud"))

    @asynccontextmanager
    async def mailbox(settings: Settings, *, silent_only: bool) -> AsyncIterator[None]:
        if silent_only:
            raise AuthenticationRequiredError("Synthetic sign in required")
        yield

    monkeypatch.setattr(gmail, "gmail_provider", mailbox)
    monkeypatch.setattr(gmail, "groq_provider", forbidden)
    args = ["brief", "--silent-only", "--database", str(profile)]
    if automatic:
        args.append("--automatic")
    assert gmail.main(args) == 2
    forbidden.assert_not_called()
