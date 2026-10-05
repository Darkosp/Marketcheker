"""Async engine и сесии."""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def create_engine(url: str | None = None, *, echo: bool = False) -> AsyncEngine:
    settings = get_settings()
    return create_async_engine(
        url or settings.database_url,
        echo=echo,
        pool_pre_ping=True,  # мрежата кон базата може да падне меѓу дневните читања
        pool_size=5,
        max_overflow=10,
    )


engine: AsyncEngine = create_engine()

SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: една сесија по барање, со rollback при грешка."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    await engine.dispose()
