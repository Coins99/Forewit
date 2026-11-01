"""Local body-processing permission is separate, account-scoped and revocable."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.messages import ProviderKind
from mailbrief.errors import ConfigurationError
from mailbrief.services.consent import (
    LOCAL_CONSENT_PROVIDER,
    LOCAL_DISCLOSURE_VERSION,
    grant_local_consent,
    has_local_consent,
    local_disclosure_lines,
    revoke_local_consent,
)
from mailbrief.storage.database import Database
from mailbrief.storage.repositories import ConsentRepository
from tests.unit.services.test_consent import ME, consent

AT = datetime(2026, 11, 1, 12, tzinfo=UTC)
LATER = AT + timedelta(hours=1)


async def test_a_grant_is_account_scoped_and_independent_of_cloud(
    database: Database, session: AsyncSession
) -> None:
    account = await consent(session)  # Existing Groq grant is not a local grant.
    await consent(session, "other")
    assert not await has_local_consent(session, ME)
    assert not await has_local_consent(session, None)
    await grant_local_consent(session, ME, clock=lambda: AT)

    async with database.session() as other:
        assert await has_local_consent(other, ME)
        assert not await has_local_consent(other, "other@example.com")
        assert await ConsentRepository(other).get_active(account.id, "groq", "2") is not None
        grant = await ConsentRepository(other).get_active(
            account.id, LOCAL_CONSENT_PROVIDER, LOCAL_DISCLOSURE_VERSION
        )
        assert grant is not None
        assert grant.auto_send_limit == 0  # Never grants any cloud automatic permission.
        assert grant.granted_at_utc == AT


@pytest.mark.parametrize("email", [None, "missing@example.com", ME])
async def test_unknown_or_microsoft_accounts_cannot_grant_local_permission(
    session: AsyncSession, email: str | None
) -> None:
    await consent(session, kind=ProviderKind.MICROSOFT)
    with pytest.raises(ConfigurationError, match="Connect Gmail and sync once"):
        await grant_local_consent(session, email)
    assert not await has_local_consent(session, email)


async def test_repeat_grant_is_idempotent_but_regrant_after_revoke_has_a_new_time(
    session: AsyncSession,
) -> None:
    account = await consent(session)
    await grant_local_consent(session, ME, clock=lambda: AT)
    await grant_local_consent(session, ME, clock=lambda: LATER)
    repository = ConsentRepository(session)
    grant = await repository.get_active(
        account.id, LOCAL_CONSENT_PROVIDER, LOCAL_DISCLOSURE_VERSION
    )
    assert grant is not None and grant.granted_at_utc == AT
    assert await revoke_local_consent(session, clock=lambda: LATER) == 1
    assert not await has_local_consent(session, ME)
    await grant_local_consent(session, ME, clock=lambda: LATER)
    grant = await repository.get_active(
        account.id, LOCAL_CONSENT_PROVIDER, LOCAL_DISCLOSURE_VERSION
    )
    assert grant is not None and grant.granted_at_utc == LATER


async def test_revoke_covers_all_accounts_and_old_versions_without_touching_cloud(
    session: AsyncSession,
) -> None:
    accounts = [await consent(session, name) for name in ("me", "other")]
    repository = ConsentRepository(session)
    for account in accounts:
        await repository.grant(account.id, LOCAL_CONSENT_PROVIDER, "old-local", AT)
        await repository.grant(account.id, LOCAL_CONSENT_PROVIDER, LOCAL_DISCLOSURE_VERSION, AT)
    await session.commit()
    assert await revoke_local_consent(session, clock=lambda: LATER) == 4
    assert await revoke_local_consent(session, clock=lambda: LATER) == 0
    for account in accounts:
        assert not await has_local_consent(session, account.email_address)
        assert await repository.get_active(account.id, LOCAL_CONSENT_PROVIDER, "old-local") is None
        assert await repository.get_active(account.id, "groq", "2") is not None


async def test_old_disclosure_does_not_authorize_current_processing(session: AsyncSession) -> None:
    await consent(session, provider=LOCAL_CONSENT_PROVIDER, version="old-local")
    assert not await has_local_consent(session, ME)


@pytest.mark.parametrize("revoke", [False, True])
async def test_failed_commit_rolls_back_permission_changes(
    database: Database, session: AsyncSession, monkeypatch: pytest.MonkeyPatch, revoke: bool
) -> None:
    await consent(session)
    if revoke:
        await grant_local_consent(session, ME)
    monkeypatch.setattr(session, "commit", AsyncMock(side_effect=RuntimeError("synthetic failure")))
    with pytest.raises(RuntimeError, match="synthetic failure"):
        if revoke:
            await revoke_local_consent(session)
        else:
            await grant_local_consent(session, ME)
    async with database.session() as other:
        assert await has_local_consent(other, ME) is revoke


@pytest.mark.parametrize("limit", [0, 21])
def test_disclosure_rejects_an_unbounded_limit(limit: int) -> None:
    with pytest.raises(ValueError, match="1 to 20"):
        local_disclosure_lines(limit)


def test_disclosure_explains_local_scope_and_its_limits() -> None:
    text = "\n".join(local_disclosure_lines(7))
    assert "7 selected messages" in text
    assert "manual and automatic" in text
    assert "Reading Gmail still contacts Google" in text
    assert "never falls back" in text
    assert "restoring a backup clears" in text
