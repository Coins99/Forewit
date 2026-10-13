"""The owner's colour scheme, saved on this device.

It lives in its own file rather than desktop-settings.json, whose model forbids unknown
fields: an older build would refuse its whole settings file over a field it doesn't know.
Colours are cosmetic, so a missing, unreadable or unknown choice gives the default and
never stops MailBrief.
"""

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict, ValidationError

from mailbrief.infra.files import write_text_atomically
from mailbrief.ui.diagnostics import log_failure
from mailbrief.ui.theme import ColorScheme

APPEARANCE_FILE: Final = "appearance.json"


class Appearance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", hide_input_in_errors=True)

    color_scheme: ColorScheme = ColorScheme.SLATE


class AppearanceStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Appearance:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return Appearance()
        try:
            return Appearance.model_validate_json(raw)
        except ValidationError:
            return Appearance()

    def save(self, appearance: Appearance) -> None:
        """Atomic, so a crash never leaves half a file. Raises OSError."""
        write_text_atomically(self.path, appearance.model_dump_json(indent=2), overwrite=True)


class AppearanceWriter:
    """Saves the latest choice off the Qt thread, one write at a time: choices made while a
    write runs are coalesced into one more write of the last of them."""

    def __init__(self, store: AppearanceStore, failed: Callable[[], None]) -> None:
        self._store = store
        self._failed = failed
        self._latest: Appearance | None = None
        self._task: asyncio.Task[None] | None = None

    def save(self, appearance: Appearance) -> None:
        self._latest = appearance
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._write())

    async def _write(self) -> None:
        while self._latest is not None:
            appearance, self._latest = self._latest, None
            try:
                await asyncio.to_thread(self._store.save, appearance)
            except OSError as exc:
                log_failure(exc)
                self._failed()

    async def drain(self) -> None:
        """Wait for the writes in progress, for shutdown."""
        if self._task is not None:
            await self._task
