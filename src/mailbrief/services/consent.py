"""The automatic-analysis permission on the active consent (ADR 0017).

The desktop and the CLI read and change it here, through the same two functions. It belongs
to one account's active consent for one AI provider and disclosure version, so revoking the
consent, or a new disclosure version, ends it without anything else happening. Setting it
needs an active consent: the owner has then seen the disclosure and analyzed once with Sync
and review. The account is always the connected Gmail account, the one a brief reads its
consent for, and at most one account holds a permission: giving it to one clears every
other's, and turning it off clears them all. Nothing here logs an address.
"""

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.domain.briefs import AUTO_SEND_LIMIT_MAX, AutoSendPermission
from mailbrief.domain.common import utc_now
from mailbrief.domain.messages import ProviderKind
from mailbrief.domain.preferences import LOCAL_MESSAGE_LIMIT_MAX
from mailbrief.errors import ConfigurationError
from mailbrief.services.writes import commit_once
from mailbrief.storage.repositories import ConsentRepository
from mailbrief.storage.tables import AccountTable, AIConsentTable

logger = logging.getLogger(__name__)

NO_CONSENT: Final = "Analyze once with Sync and review to give consent first."
LOCAL_CONSENT_PROVIDER: Final = "local"
LOCAL_DISCLOSURE_VERSION: Final = "local-1"
LOCAL_CONNECT_FIRST: Final = "Connect Gmail and sync once before enabling local email analysis."


def local_disclosure_lines(limit: int) -> tuple[str, ...]:
    """The local-processing disclosure, shared by the CLI and future desktop setup."""
    if not 1 <= limit <= LOCAL_MESSAGE_LIMIT_MAX:
        raise ValueError(f"The local message limit must be 1 to {LOCAL_MESSAGE_LIMIT_MAX}.")
    return (
        f"Allow Forewit to read up to {limit} selected messages per brief from this Gmail account "
        "and summarize them on this computer, in manual and automatic runs.",
        "Reading Gmail still contacts Google. In local mode, email analysis sends nothing "
        "to Groq or another AI service and never falls back to cloud analysis.",
        "Incoming bodies are prepared in memory, not saved. Bounded summaries, short quotes "
        "and suggestions are saved, alongside Gmail's existing preview snippets.",
        "The local runtime and model download is about 2.6 GB. Processing can take several "
        "minutes for 20 messages; speed and memory use depend on this computer.",
        "This permission is separate from cloud and drafting consent. It does not switch "
        "analysis mode, start a run, download a model or change the refresh schedule.",
        "Revoke to stop future local body reads and analysis. Saved results remain until "
        "you clear them; restoring a backup clears this permission.",
    )


async def _gmail_account(session: AsyncSession, email: str | None) -> AccountTable | None:
    if email is None:
        return None
    result = await session.scalars(
        select(AccountTable)
        .where(
            AccountTable.provider == ProviderKind.GMAIL.value,
            AccountTable.email_address == email,
        )
        .execution_options(populate_existing=True)
    )
    return result.first()


async def has_local_consent(session: AsyncSession, account_email: str | None) -> bool:
    """Whether this Gmail account has current, unrevoked local-processing consent."""
    return (
        await _active(session, account_email, LOCAL_CONSENT_PROVIDER, LOCAL_DISCLOSURE_VERSION)
        is not None
    )


async def grant_local_consent(
    session: AsyncSession,
    account_email: str | None,
    *,
    clock: Callable[[], datetime] = utc_now,
) -> None:
    """Record an explicitly approved local grant; no model, mail or cloud I/O.

    Callers must show the disclosure and receive approval first. Repeating a current
    grant preserves its original timestamp. An unknown account cannot acquire a grant.
    """
    account = await _gmail_account(session, account_email)
    if account is None:
        raise ConfigurationError(LOCAL_CONNECT_FIRST)

    async def change(now: datetime) -> None:
        repository = ConsentRepository(session)
        if await repository.get_active(
            account.id, LOCAL_CONSENT_PROVIDER, LOCAL_DISCLOSURE_VERSION
        ):
            return
        await repository.grant(account.id, LOCAL_CONSENT_PROVIDER, LOCAL_DISCLOSURE_VERSION, now)

    await commit_once(session, clock, change)
    logger.info("Local email analysis consent granted")


async def revoke_local_consent(
    session: AsyncSession, *, clock: Callable[[], datetime] = utc_now
) -> int:
    """Revoke all local grants, including older disclosures, without credentials.

    Other providers' consents and all saved analyses remain unchanged.
    """
    count = 0

    async def change(now: datetime) -> None:
        nonlocal count
        repository = ConsentRepository(session)
        for account in await session.scalars(select(AccountTable)):
            count += await repository.revoke_all(account.id, LOCAL_CONSENT_PROVIDER, now)

    await commit_once(session, clock, change)
    logger.info("Local email analysis consent revoked: %d", count)
    return count


def _permission(consent: AIConsentTable, account: AccountTable) -> AutoSendPermission:
    return AutoSendPermission(
        account_email=account.email_address,
        limit=consent.auto_send_limit,
        granted_at_utc=consent.auto_send_granted_at_utc,
    )


async def _active(
    session: AsyncSession, account_email: str | None, provider: str, version: str
) -> tuple[AIConsentTable, AccountTable] | None:
    """The Gmail account with this address and its active consent; None without either."""
    account = await _gmail_account(session, account_email)
    if account is None:
        return None
    consent = await ConsentRepository(session).get_active(account.id, provider, version)
    return None if consent is None else (consent, account)


async def auto_send_permission(
    session: AsyncSession, account_email: str | None, *, provider: str, version: str
) -> AutoSendPermission | None:
    """The permission on the connected Gmail account's active consent, 0 when none was
    given; None when that account has no active consent for this provider and disclosure
    version, or when no account is connected (``account_email`` None)."""
    found = await _active(session, account_email, provider, version)
    return None if found is None else _permission(*found)


async def set_auto_send(
    session: AsyncSession,
    limit: int,
    account_email: str | None,
    *,
    provider: str,
    version: str,
    clock: Callable[[], datetime] = utc_now,
) -> AutoSendPermission | None:
    """Allow automatic runs to send up to ``limit`` of the connected account's messages
    without asking; 0 turns it off for every account.

    A limit above 0 needs the connected account's active consent (ConfigurationError, with a
    static message, without one). It records when it was given and clears every other
    account's permission, so at most one account holds one. 0 clears every account's and
    needs neither an account nor a consent. Returns the connected account's permission, or
    None when it has no active consent. Raises ValueError for a limit outside 0 to 10.
    Commits once and rolls back on failure.
    """
    if not 0 <= limit <= AUTO_SEND_LIMIT_MAX:
        raise ValueError(f"The limit must be 0 to {AUTO_SEND_LIMIT_MAX}.")
    repository = ConsentRepository(session)
    found = await _active(session, account_email, provider, version)
    if limit and found is None:
        raise ConfigurationError(NO_CONSENT)

    async def change(now: datetime) -> None:
        for other in await repository.with_auto_send(provider):
            if found is None or other.id != found[0].id:
                other.auto_send_limit = 0
                other.auto_send_granted_at_utc = None
        if found is not None:
            await repository.set_auto_send(found[1].id, provider, version, limit, now)

    await commit_once(session, clock, change)
    logger.info("Automatic-analysis permission set to %d messages", limit)
    return None if found is None else _permission(*found)
