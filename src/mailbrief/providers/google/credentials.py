"""A Google refresh credential, kept only in the operating-system credential vault.

Gmail and Google Calendar each keep their own entry; the vault handling is shared.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from mailbrief.infra.vault import VaultBackend


class RefreshCredential(BaseModel):
    """Versioned single-account record; never contains access tokens or mail."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    version: int = Field(default=1, ge=1, le=1)
    client_id: str = Field(min_length=1)
    email_address: str = Field(min_length=3)
    refresh_token: SecretStr = Field(min_length=1)


class CredentialStore(Protocol):
    """Synchronous secret-store boundary, called off the application thread."""

    def load(self) -> RefreshCredential | None: ...

    def save(self, credential: RefreshCredential) -> None: ...

    def clear(self) -> None: ...


@dataclass(frozen=True, slots=True)
class VaultMessages:
    """One sign-in's fixed texts for a vault that fails or holds an unusable entry."""

    unreadable: str
    invalid: str
    unsaved: str
    unremoved: str


class VaultCredentialStore:
    """One vault entry; fails closed with static messages if the vault can't load, save or
    delete it."""

    def __init__(
        self,
        backend: VaultBackend,
        *,
        service: str,
        entry: str,
        messages: VaultMessages,
        error: Callable[[str], Exception],
    ) -> None:
        self._backend = backend
        self._service = service
        self._entry = entry
        self._messages = messages
        self._error = error

    def load(self) -> RefreshCredential | None:
        try:
            value = self._backend.get_password(self._service, self._entry)
        except Exception:
            raise self._error(self._messages.unreadable) from None
        if value is None:
            return None
        try:
            return RefreshCredential.model_validate_json(value)
        except ValidationError:
            raise self._error(self._messages.invalid) from None

    def save(self, credential: RefreshCredential) -> None:
        # SecretStr's default JSON output is redacted; unwrap only at the vault boundary.
        payload = credential.model_dump(mode="json")
        payload["refresh_token"] = credential.refresh_token.get_secret_value()
        try:
            self._backend.set_password(self._service, self._entry, json.dumps(payload))
        except Exception:
            raise self._error(self._messages.unsaved) from None

    def clear(self) -> None:
        try:
            if self._backend.get_password(self._service, self._entry) is not None:
                self._backend.delete_password(self._service, self._entry)
        except Exception:
            raise self._error(self._messages.unremoved) from None
