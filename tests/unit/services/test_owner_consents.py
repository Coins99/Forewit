"""The owner's own AI consents: each use has its scope, and they are revoked together."""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.services.breakdown import BREAKDOWN_DISCLOSURE_VERSION, BREAKDOWN_SCOPE
from mailbrief.services.calendar_planning import CALENDAR_DISCLOSURE_VERSION, CALENDAR_SCOPE
from mailbrief.services.drafting import DRAFTING_DISCLOSURE_VERSION, DRAFTING_SCOPE
from mailbrief.services.owner_consents import (
    NO_OWNER_CONSENT,
    OWNER_USES,
    active_owner_uses,
    owner_consents_text,
)
from mailbrief.storage.repositories import OwnerConsentRepository

NOW = datetime(2026, 11, 16, 15, 0, tzinfo=UTC)


def test_each_use_has_its_own_scope() -> None:
    assert OWNER_USES == (
        ("drafting", DRAFTING_SCOPE, DRAFTING_DISCLOSURE_VERSION),
        ("calendar planning", CALENDAR_SCOPE, CALENDAR_DISCLOSURE_VERSION),
        ("breakdown", BREAKDOWN_SCOPE, BREAKDOWN_DISCLOSURE_VERSION),
    )
    assert len({scope for _, scope, _ in OWNER_USES}) == 3


async def test_one_consent_grants_nothing_to_another_use(session: AsyncSession) -> None:
    consents = OwnerConsentRepository(session)
    assert await active_owner_uses(session, "groq") == ()
    await consents.grant("groq", BREAKDOWN_SCOPE, BREAKDOWN_DISCLOSURE_VERSION, NOW)
    assert await active_owner_uses(session, "groq") == ("breakdown",)
    assert await active_owner_uses(session, "local") == ()
    await consents.grant("groq", DRAFTING_SCOPE, DRAFTING_DISCLOSURE_VERSION, NOW)
    await consents.grant("groq", BREAKDOWN_SCOPE, "0", NOW)  # An older disclosure: not this one.
    assert await active_owner_uses(session, "groq") == ("drafting", "breakdown")


async def test_revoking_ai_consent_revokes_every_use(session: AsyncSession) -> None:
    consents = OwnerConsentRepository(session)
    for _, scope, version in OWNER_USES:
        await consents.grant("groq", scope, version, NOW)
    assert await active_owner_uses(session, "groq") == (
        "drafting",
        "calendar planning",
        "breakdown",
    )
    assert await consents.revoke_all("groq", NOW) == 3
    assert await active_owner_uses(session, "groq") == ()


def test_the_line_names_the_active_uses() -> None:
    assert owner_consents_text(()) == NO_OWNER_CONSENT
    assert owner_consents_text(("drafting", "breakdown")) == (
        "Consent is active for: drafting, breakdown."
    )
