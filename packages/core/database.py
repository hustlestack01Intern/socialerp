"""
Database Session Management

Async SQLAlchemy session management with tenant context propagation.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from packages.core.config import get_settings

# Global engine and session factory
_engine = None
_session_factory = None


def get_engine() -> Any:
    """Get or create async database engine."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            poolclass=NullPool,  # Use NullPool for development; switch to QueuePool in production
            echo=settings.database_echo,
        )
    return cast(Any, _engine)


def get_session_factory() -> Any:
    """Get or create async session factory."""
    global _session_factory
    if _session_factory is None:
        engine = get_engine()
        _session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession]:
    """Get database session for dependency injection.

    Commits on success and rolls back on error, so a request that mutates the
    database persists its work when it returns 2xx. Previously the session was
    only closed, which silently discarded every uncommitted write for handlers
    that relied on the dependency (webhooks, onboarding).

    Before yielding, if a tenant is acting (set by the API middleware or a
    worker/test via the ``socialerp.tenancy`` contextvar) the session-local
    ``app.current_tenant_id`` GUC is set with a *parameterized* call so Row-Level
    Security scopes the request. With no tenant the GUC stays unset and RLS
    denies by default (fail closed).

    Pattern rule: routes must not call ``db.commit()`` mid-request — that ends
    the transaction-local GUC — use ``flush`` instead.
    """
    from socialerp.tenancy import get_current_tenant_id

    session_factory = get_session_factory()
    async with session_factory() as session:
        tenant_id = get_current_tenant_id()
        if tenant_id is not None:
            from sqlalchemy import text as sa_text

            await session.execute(
                sa_text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession]:
    """Context manager for database session (for non-FastAPI usage)."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database (create tables if not exist)."""
    from sqlalchemy.exc import ProgrammingError

    from packages.core.models import Base

    engine = get_engine()

    # First create tables without indexes
    async with engine.begin() as conn:
        # Create tables without indexes by temporarily removing indexes from metadata
        for table in Base.metadata.tables.values():
            table.indexes.clear()
        await conn.run_sync(Base.metadata.create_all, checkfirst=True)

    # Then create indexes separately
    async with engine.begin() as conn:
        for table in Base.metadata.tables.values():
            for index in table.indexes:
                try:
                    await conn.run_sync(
                        lambda sync_conn, index=index: index.create(sync_conn, checkfirst=True),
                    )
                except ProgrammingError as e:
                    if "already exists" not in str(e):
                        raise


async def close_db() -> None:
    """Close database connections."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
