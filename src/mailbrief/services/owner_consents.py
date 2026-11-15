"""The owner's AI consents that belong to no account: drafting (ADR 0013), calendar planning
(ADR 0018) and task breakdown (ADR 0033). Each use has its own scope and disclosure; one
grants nothing to another, and Revoke AI consent revokes them together."""

from typing import Final

from sqlalchemy.ext.asyncio import AsyncSession

from mailbrief.services.breakdown import BREAKDOWN_DISCLOSURE_VERSION, BREAKDOWN_SCOPE
from mailbrief.services.calendar_planning import CALENDAR_DISCLOSURE_VERSION, CALENDAR_SCOPE
from mailbrief.services.drafting import DRAFTING_DISCLOSURE_VERSION, DRAFTING_SCOPE
from mailbrief.storage.repositories import OwnerConsentRepository

# What each use is called, with its scope and current disclosure version, in display order.
OWNER_USES: Final = (
    ("drafting", DRAFTING_SCOPE, DRAFTING_DISCLOSURE_VERSION),
    ("calendar planning", CALENDAR_SCOPE, CALENDAR_DISCLOSURE_VERSION),
    ("breakdown", BREAKDOWN_SCOPE, BREAKDOWN_DISCLOSURE_VERSION),
)
NO_OWNER_CONSENT: Final = "No consent is active for drafting, calendar planning or breakdown."


async def active_owner_uses(session: AsyncSession, provider: str) -> tuple[str, ...]:
    """The uses the owner consents to now for ``provider``, at their current disclosure
    versions, in display order."""
    consents = OwnerConsentRepository(session)
    return tuple(
        [
            name
            for name, scope, version in OWNER_USES
            if await consents.get_active(provider, scope, version) is not None
        ]
    )


def owner_consents_text(uses: tuple[str, ...]) -> str:
    """One plain line naming the active consents, for Settings and the diagnostic."""
    if not uses:
        return NO_OWNER_CONSENT
    return "Consent is active for: " + ", ".join(uses) + "."
