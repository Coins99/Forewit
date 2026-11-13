"""Alembic environment for the async MailBrief database."""

import asyncio
import os
from logging.config import fileConfig
from typing import Any

from alembic import context
from sqlalchemy import Connection, event, pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from mailbrief.storage.migration_forks import compatible_plan
from mailbrief.storage.tables import Base

config = context.config

if config.config_file_name is not None:
    # Keep loggers that already exist: the application's, and those tests capture. The
    # app's own upgrade builds its Config without alembic.ini, so this runs only for the
    # alembic command and tests.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    """Read an explicit URL from the environment or Alembic configuration."""
    url = (
        config.attributes.get("explicit_database_url")
        or os.getenv("MAILBRIEF_DATABASE_URL")
        or config.get_main_option("sqlalchemy.url")
    )
    if not url:
        raise RuntimeError("MAILBRIEF_DATABASE_URL or sqlalchemy.url must be configured")
    return url


def run_migrations_offline() -> None:
    """Generate SQL without creating a database connection."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_sync_migrations(connection: Connection) -> None:
    """Run migrations with the synchronous facade of an async connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=True,
    )
    # Keep the command's planner (upgrade, downgrade or schema check); adapt only
    # the two published revisions that independently added the same note column.
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=True,
        fn=compatible_plan(context.get_context().opts["fn"]),
    )

    with context.begin_transaction():
        context.run_migrations()


def _leave_transactions_to_sqlalchemy(dbapi_connection: Any, _record: Any) -> None:
    dbapi_connection.isolation_level = None


def _begin(connection: Connection) -> None:
    connection.exec_driver_sql("BEGIN")


async def run_async_migrations() -> None:
    """Create the async engine and execute migrations."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = database_url()
    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    # SQLite's driver begins a transaction only before it changes rows, so a revision's
    # schema statements were committed one by one: an upgrade that stopped inside a
    # revision left columns and tables its recorded version did not have, and every retry
    # failed on them. Begin the transaction here, so a revision and its version are
    # committed together or not at all.
    event.listen(connectable.sync_engine, "connect", _leave_transactions_to_sqlalchemy)
    event.listen(connectable.sync_engine, "begin", _begin)

    async with connectable.connect() as connection:
        await connection.run_sync(run_sync_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
