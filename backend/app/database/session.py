"""Async SQLAlchemy session lifecycle."""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

_engine = create_async_engine(str(get_settings().database_url), pool_pre_ping=True)
session_factory = async_sessionmaker(_engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield one transaction-capable session for a request."""

    async with session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
