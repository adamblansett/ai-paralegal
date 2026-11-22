from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.db.base import Base

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None
_engine_url: str | None = None


def create_async_engine_from_url(
    database_url: str,
    *,
    use_null_pool: bool = False,
    echo: bool = False,
) -> AsyncEngine:
    """Create an async SQLAlchemy engine configured for PostgreSQL or SQLite."""
    kwargs: dict[str, Any] = {"echo": echo, "future": True}
    if use_null_pool:
        kwargs["poolclass"] = NullPool

    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    elif not use_null_pool:
        kwargs["pool_pre_ping"] = True

    engine = create_async_engine(database_url, **kwargs)

    if database_url.startswith("sqlite"):

        @event.listens_for(engine.sync_engine, "connect")
        def _enable_sqlite_fk(dbapi_connection: Any, _connection_record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def get_async_engine(settings: Settings | None = None) -> AsyncEngine:
    """Return the shared async engine (recreating if settings URL changed)."""
    global _engine, _session_factory, _engine_url
    cfg = settings or get_settings()
    if _engine is None or _engine_url != cfg.database_url:
        _engine = create_async_engine_from_url(cfg.database_url)
        _session_factory = async_sessionmaker(
            bind=_engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        _engine_url = cfg.database_url
    return _engine


def get_session_factory(
    settings: Settings | None = None,
) -> async_sessionmaker[AsyncSession]:
    """Return the shared async_sessionmaker instance."""
    get_async_engine(settings)
    assert _session_factory is not None
    return _session_factory


def build_worker_session_factory(
    settings: Settings | None = None,
) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create an isolated NullPool engine and session factory for Celery tasks."""
    cfg = settings or get_settings()
    engine = create_async_engine_from_url(cfg.database_url, use_null_pool=True)
    factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return engine, factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding an async database session."""
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def init_db(settings: Settings | None = None) -> None:
    """Create database tables via SQLAlchemy metadata (primarily for local dev/tests)."""
    import app.models  # noqa: F401

    engine = get_async_engine(settings)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    """Dispose the shared async engine cleanly on shutdown."""
    global _engine, _session_factory, _engine_url
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None
    _engine_url = None
