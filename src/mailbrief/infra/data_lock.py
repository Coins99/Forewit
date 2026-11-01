"""Coordinate offline recovery with the desktop and diagnostic CLI."""

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from PySide6.QtCore import QLockFile

from mailbrief.errors import ConfigurationError


class DataInUseError(ConfigurationError):
    """A data folder cannot be used exclusively by this operation."""


def create_data_lock(directory: Path) -> QLockFile:
    """Use one identity and stale-lock policy for desktop, diagnostics and package checks."""
    lock = QLockFile(str(directory / "desktop.lock"))
    lock.setStaleLockTime(0)
    return lock


@contextmanager
def held_lock(path: Path, busy: str) -> Iterator[None]:
    """Hold the lock file at ``path`` for the block; raise DataInUseError(``busy``) at once
    when another process holds it. A lock its holder left by crashing is taken over."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = QLockFile(str(path))
    lock.setStaleLockTime(0)
    if not lock.tryLock(0):
        raise DataInUseError(busy)
    try:
        yield
    finally:
        lock.unlock()


@contextmanager
def data_directory_lock(database: Path) -> Iterator[None]:
    """Hold the desktop's lock until database operations and shutdown finish."""
    directory = database.resolve().parent
    created: list[Path] = []
    ancestor = directory
    while not ancestor.exists() and ancestor != ancestor.parent:
        created.append(ancestor)
        ancestor = ancestor.parent
    lock = None
    acquired = False
    try:
        directory.mkdir(parents=True, exist_ok=True)
        lock = create_data_lock(directory)
        acquired = lock.tryLock(0)
        if not acquired:
            raise DataInUseError(
                "Forewit's data folder is busy or unavailable. Close the desktop and other "
                "diagnostic commands, check folder access, then retry."
            )
        yield
    finally:
        if acquired and lock is not None:
            lock.unlock()
        # Remove only new, empty directories.
        for path in created:
            with suppress(OSError):
                path.rmdir()
