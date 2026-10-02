"""Заеднички fixtures.

Тестовите што бараат база се означени со @pytest.mark.db и се прескокнуваат
ако PostgreSQL не е достапна - така `pytest` работи и без Docker.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Поставките бараат вистински вредности; за тестовите даваме безопасни.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-dolga-najmalku-16")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
# POSTGRES_HOST/PORT намерно НЕ се поставуваат тука: променливите на околината
# имаат предност над .env во pydantic-settings, па би ја пребришале вистинската
# конфигурација. Внатре во контејнерот (docker compose run --rm api pytest)
# важи db:5432. За pytest директно на хостот:
#   POSTGRES_HOST=localhost POSTGRES_PORT=5434 pytest
# Без тоа, тестовите означени со @pytest.mark.db се прескокнуваат.


@pytest.fixture(scope="session")
def settings():
    from app.core.config import get_settings

    return get_settings()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    """HTTP клиент врз ASGI апликацијата, без мрежа и без база."""
    from app.main import create_app

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(scope="session")
async def db_engine(settings):
    """Engine кон тест базата; прескокнува ако базата не е достапна.

    Шемата се прави со create_all, не со Alembic: тестовите се вртат често и
    миграциите се проверуваат одделно (круг upgrade/downgrade). Затоа
    CHECK ограничувањата, кои живеат само во миграцијата, ги нема тука.
    """
    from sqlalchemy import text

    from app.models import Base

    engine = create_async_engine(settings.test_database_url, poolclass=None)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f"Тест базата не е достапна: {exc}")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(db_engine) -> AsyncIterator[AsyncSession]:
    """Сесија во транзакција што секогаш се враќа назад - тестовите не
    оставаат податоци по себе.
    """
    async with db_engine.connect() as connection:
        transaction = await connection.begin()
        maker = async_sessionmaker(bind=connection, expire_on_commit=False)
        async with maker() as session:
            yield session
        await transaction.rollback()


def load_fixture(name: str) -> str:
    """Чита зачуван примерок (HTML/JSON) од tests/fixtures."""
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def load_fixture_bytes(name: str) -> bytes:
    """Чита зачуван бинарен примерок (пр. PDF од КАМ)."""
    return (FIXTURES_DIR / name).read_bytes()
