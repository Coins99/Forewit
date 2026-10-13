"""The owner's colour scheme and glass choices, saved on this device.

It lives in its own file rather than desktop-settings.json, whose model forbids unknown
fields: an older build would refuse its whole settings file over a field it doesn't know.
Colours are cosmetic, so a missing, unreadable or unknown choice gives the default and
never stops MailBrief.
"""

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Final

from pydantic import (
    BaseModel,
    ConfigDict,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    field_validator,
)
from PySide6.QtCore import QObject, Signal

from mailbrief.infra.files import write_text_atomically
from mailbrief.ui.diagnostics import log_failure
from mailbrief.ui.glass.preferences import Motion, Transparency
from mailbrief.ui.theme import ColorScheme

APPEARANCE_FILE: Final = "appearance.json"


class Appearance(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore", hide_input_in_errors=True)

    color_scheme: ColorScheme = ColorScheme.SLATE
    transparency: Transparency = Transparency.SYSTEM
    motion: Motion = Motion.SYSTEM

    @field_validator("color_scheme", "transparency", "motion", mode="wrap")
    @classmethod
    def _default_when_unknown(
        cls, value: object, handler: ValidatorFunctionWrapHandler, info: ValidationInfo
    ) -> object:
        """A value this build doesn't know (from a newer one, say) gives that field's
        default, and keeps the others."""
        try:
            return handler(value)
        except ValidationError:
            if info.field_name is None:
                raise
            return cls.model_fields[info.field_name].default


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


# One worker for appearance saves: each is one small atomic write.
_WORKER: Final = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mailbrief-appearance")


class AppearanceWriter(QObject):
    """Saves the latest choice on a worker thread, one write at a time: choices made while a
    write runs become one more write of the last of them.

    It uses a thread, not an asyncio task, so no write depends on qasync running a task
    (one created like this once never ran, and shutdown waited for it). A failed write is
    reported through ``failed``, a queued signal, on the Qt thread. ``drain`` waits for the
    writes at shutdown.
    """

    failed = Signal()

    def __init__(self, store: AppearanceStore, failed: Callable[[], None]) -> None:
        super().__init__()
        self._store = store
        self.failed.connect(failed)
        self._lock = threading.Lock()
        self._latest: Appearance | None = None
        self._writing: Future[None] | None = None

    def save(self, appearance: Appearance) -> None:
        with self._lock:
            self._latest = appearance
            if self._writing is None:
                self._writing = _WORKER.submit(self._write)

    def _write(self) -> None:
        """On the worker: write the latest choice until none is left."""
        while True:
            with self._lock:
                appearance, self._latest = self._latest, None
                if appearance is None:
                    self._writing = None  # A later save starts a new write.
                    return
            try:
                self._store.save(appearance)
            except Exception as exc:  # Any failure: the loop must go on to clear _writing.
                log_failure(exc)
                self.failed.emit()

    async def drain(self) -> None:
        """Wait for the writes in progress, for shutdown; each takes milliseconds."""
        with self._lock:
            writing = self._writing
        if writing is not None:
            wait([writing])
