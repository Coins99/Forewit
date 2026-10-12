"""Google Calendar's side of the one Google sign-in (ADR 0019), and what is left of the
separate calendar sign-in that ADR replaced.

Calendar requests use the Gmail sign-in's tokens: its grant includes the two read-only
calendar permissions. Before ADR 0019, Google Calendar had its own grant in its own vault
entry; nothing reads that entry any more, and forget_legacy_sign_in() deletes it.
"""

from typing import Final

from mailbrief.infra import vault
from mailbrief.infra.vault import VaultBackend, VaultUnavailableError
from mailbrief.providers.gcal.errors import CalendarSetupError
from mailbrief.providers.gmail.oauth import CALENDAR_EVENTS_SCOPE as CALENDAR_EVENTS_SCOPE
from mailbrief.providers.gmail.oauth import CALENDAR_LIST_SCOPE as CALENDAR_LIST_SCOPE
from mailbrief.providers.gmail.oauth import CALENDAR_SCOPES as CALENDAR_SCOPES
from mailbrief.providers.google.credentials import VaultCredentialStore, VaultMessages

CALENDAR_LIST_URL: Final = "https://www.googleapis.com/calendar/v3/users/me/calendarList"
# The separate calendar sign-in's vault entry (ADR 0018, phase 2), replaced by ADR 0019.
SERVICE: Final = "MailBrief.GoogleCalendar"
ENTRY: Final = "calendar-account-v1"
_VAULT_MESSAGES = VaultMessages(
    unreadable="Unable to read the old calendar sign-in from the OS vault.",
    invalid="The old calendar sign-in in the OS vault is unusable.",
    unsaved="Unable to save the old calendar sign-in in the OS vault.",
    unremoved="Unable to remove the old calendar sign-in from the OS vault.",
)


def _os_vault() -> VaultBackend:
    try:
        return vault.os_vault()
    except VaultUnavailableError as exc:
        raise CalendarSetupError(str(exc)) from None


class CalendarCredentialStore(VaultCredentialStore):
    """The old, separate calendar sign-in's vault entry; kept only to be deleted."""

    def __init__(self, backend: VaultBackend | None = None) -> None:
        super().__init__(
            backend if backend is not None else _os_vault(),
            service=SERVICE,
            entry=ENTRY,
            messages=_VAULT_MESSAGES,
            error=CalendarSetupError,
        )


def forget_legacy_sign_in(backend: VaultBackend | None = None) -> bool:
    """Delete the old calendar sign-in's vault entry; returns whether there was one."""
    store = CalendarCredentialStore(backend)
    try:
        if store.load() is None:
            return False
    except CalendarSetupError:
        pass  # An unusable leftover is deleted all the same.
    store.clear()
    return True
