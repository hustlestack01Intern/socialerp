"""
Database connection and session management.

Provides async SQLAlchemy engine, session factory, and base model.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from socialerp.settings import get_settings
from sqlalchemy import Pool, event
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import ConnectionPoolEntry

from packages.core.models.base import Base

settings = get_settings()

# Single canonical metadata (ADR 0008): the `socialerp` facade must NOT define its
# own `Base`. All models — gen-1 domain plus gen-2 infra (outbox, idempotency) —
# register on the one metadata so that `init_db` and Alembic agree.
metadata = Base.metadata


# Global engine and session factory (initialized on startup)
_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine() -> AsyncEngine:
    """Create async database engine."""
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            str(settings.DATABASE_URL),
            echo=settings.ENVIRONMENT == "development",
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            pool_recycle=3600,
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create async session factory."""
    global _session_factory
    if _session_factory is None:
        engine = create_engine()
        _session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get a database session for a request/worker task."""
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db() -> None:
    """Initialize database (create tables if they don't exist)."""
    engine = create_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db() -> None:
    """Close database connections."""
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


def reset_db() -> None:
    """Reset global database state (for testing)."""
    global _engine, _session_factory
    if _engine is not None:
        # Note: This is not async, so we can't await dispose()
        # The engine will be garbage collected
        _engine = None
    _session_factory = None


# Event listener for connection validation
@event.listens_for(Pool, "connect")
def set_postgresql_timezone(
    dbapi_connection: DBAPIConnection, connection_record: ConnectionPoolEntry
) -> None:
    """Set timezone to UTC for all connections."""
    cursor = dbapi_connection.cursor()
    cursor.execute("SET TIME ZONE 'UTC'")
    cursor.close()
