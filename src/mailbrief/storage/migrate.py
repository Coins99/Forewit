"""Run checked-in Alembic upgrades against the explicit diagnostic database.

Alembic is imported where a migration runs, which the desktop does off the Qt thread, so
importing it (about 0.4 s) never delays the window.
"""

import asyncio
import sqlite3
import sys
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, closing
from pathlib import Path
from typing import TYPE_CHECKING

from mailbrief.errors import ConfigurationError
from mailbrief.storage.database import Database, sqlite_url

if TYPE_CHECKING:
    from alembic.config import Config


def migration_config(path: Path) -> "Config":
    """Resolve bundled revisions and bind them to an explicit database path."""
    from alembic.config import Config

    bundle = getattr(sys, "_MEIPASS", None)
    root = Path(bundle) if isinstance(bundle, str) else Path(__file__).resolve().parents[3]
    if not (root / "migrations" / "env.py").is_file():
        raise ConfigurationError(
            "Migration resources are unavailable; reinstall Forewit or use the project checkout."
        )
    config = Config()
    config.set_main_option("script_location", str(root / "migrations").replace("%", "%%"))
    config.set_main_option("sqlalchemy.url", sqlite_url(path).replace("%", "%%"))
    config.attributes["explicit_database_url"] = sqlite_url(path)
    return config


def upgrade_database(path: Path) -> None:
    """Use read-only bundled revisions; always migrate the explicit user-data database."""
    from alembic import command
    from alembic.script import ScriptDirectory

    config = migration_config(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file() and path.stat().st_size:
        with closing(sqlite3.connect(path)) as source:
            has_version = source.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='alembic_version'"
            ).fetchone()
            versions = (
                {row[0] for row in source.execute("SELECT version_num FROM alembic_version")}
                if has_version
                else set()
            )
            scripts = ScriptDirectory.from_config(config)
            for version in versions:
                # Reject newer/unknown schemas without modifying them.
                scripts.get_revision(version)
            if versions != set(scripts.get_heads()):
                # SQLite's backup API includes committed WAL pages. A raw file copy does not.
                # Unique files preserve the original even after a partially failed upgrade/retry.
                with tempfile.NamedTemporaryFile(
                    dir=path.parent,
                    prefix=f"{path.name}.pre-upgrade-",
                    suffix=".sqlite3",
                    delete=False,
                ) as temporary:
                    backup = Path(temporary.name)
                try:
                    with closing(sqlite3.connect(backup)) as destination:
                        source.backup(destination)
                except BaseException:
                    backup.unlink(missing_ok=True)
                    raise
    command.upgrade(config, "head")


@asynccontextmanager
async def migrated_database(path: Path, *, upgrade: bool = True) -> AsyncIterator[Database]:
    """The database at ``path``, created if missing and upgraded off the event loop's thread,
    disposed when the block ends. ``upgrade=False`` skips the upgrade, for a caller that
    already brought this database up to date."""
    if upgrade:
        await asyncio.to_thread(upgrade_database, path)
    database = Database.from_path(path)
    try:
        yield database
    finally:
        await database.dispose()
