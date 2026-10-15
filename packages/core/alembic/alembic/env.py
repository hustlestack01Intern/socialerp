"""
Alembic Environment Configuration for SocialERP Core.
"""

import concurrent.futures
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from socialerp.database import Base
from socialerp.settings import get_settings

# This is the Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set the SQLAlchemy URL from settings (only if not already set)
if not config.get_main_option("sqlalchemy.url"):
    settings = get_settings()
    config.set_main_option("sqlalchemy.url", str(settings.DATABASE_URL))

# Target metadata for autogenerate
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations with a connection."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_async_migrations() -> None:
    """Entry point for running async migrations."""
    import asyncio

    try:
        asyncio.get_running_loop()
        # Running in an existing event loop - run migrations in a new thread
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(asyncio.run, run_migrations_online())
            future.result()
    except RuntimeError:
        # No running loop
        import asyncio

        asyncio.run(run_migrations_online())


# Only run migrations when explicitly called (not at module import)
# Alembic will call run_async_migrations() via the command line
# or when running tests programmatically
if __name__ == "__main__":
    run_async_migrations()

# For alembic command line, we don't run at import time
# The command line will call the appropriate function
