"""Подключение к базе данных (async SQLAlchemy 2.0).

Здесь живут движок, фабрика сессий и зависимость `get_session` для FastAPI.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings

settings = get_settings()

# echo=False — не засоряем логи SQL-запросами. Включите True для отладки.
engine = create_async_engine(settings.database_url, echo=False, pool_pre_ping=True)

SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    """Базовый класс для всех ORM-моделей."""


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI-зависимость: одна сессия на запрос, с автозакрытием."""
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Создаём таблицы при старте.

    Для воркшопа этого достаточно. В проде используйте миграции (Alembic).
    """
    # Импорт моделей нужен, чтобы они зарегистрировались в метаданных Base.
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
