"""Gmail's refresh credential, stored only in the operating-system credential vault."""

from mailbrief.infra import vault
from mailbrief.infra.vault import VaultBackend, VaultUnavailableError
from mailbrief.providers.gmail.errors import GmailSetupError
from mailbrief.providers.google.credentials import CredentialStore as CredentialStore
from mailbrief.providers.google.credentials import RefreshCredential as RefreshCredential
from mailbrief.providers.google.credentials import VaultCredentialStore, VaultMessages

SERVICE = "MailBrief.Gmail"
ENTRY = "personal-account-v1"
_MESSAGES = VaultMessages(
    unreadable="Unable to read Gmail credentials from the OS vault.",
    invalid="Stored Gmail credentials are invalid; disconnect and reconnect.",
    unsaved="Unable to save Gmail credentials in the OS vault.",
    unremoved="Unable to remove Gmail credentials from the OS vault.",
)


def os_vault() -> VaultBackend:
    """The OS credential vault, chosen explicitly by infra.vault. A vault that can't be used
    is a Gmail setup error, so the diagnostic CLI prints its message."""
    try:
        return vault.os_vault()
    except VaultUnavailableError as exc:
        raise GmailSetupError(str(exc)) from None


class GmailCredentialStore(VaultCredentialStore):
    """Fail closed if the OS vault cannot load, save, or delete credentials."""

    def __init__(self, backend: VaultBackend | None = None) -> None:
        super().__init__(
            backend if backend is not None else os_vault(),
            service=SERVICE,
            entry=ENTRY,
            messages=_MESSAGES,
            error=GmailSetupError,
        )
