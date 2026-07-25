from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import ensure_data_dirs, get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine
    if _engine is None:
        ensure_data_dirs()
        settings = get_settings()
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            connect_args={"check_same_thread": False},
        )
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    factory = get_session_factory()
    async with factory() as session:
        yield session


async def init_db() -> None:
    from app import models  # noqa: F401
    from sqlalchemy import text

    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # SQLite: adiciona colunas novas em bancos já existentes
        result = await conn.execute(text("PRAGMA table_info(runtime_state)"))
        existing = {row[1] for row in result.fetchall()}
        alterations = {
            "jitter_seconds": "ALTER TABLE runtime_state ADD COLUMN jitter_seconds FLOAT NOT NULL DEFAULT 8.0",
            "action_settle_ms": "ALTER TABLE runtime_state ADD COLUMN action_settle_ms INTEGER NOT NULL DEFAULT 2500",
            "click_delay_ms_min": "ALTER TABLE runtime_state ADD COLUMN click_delay_ms_min INTEGER NOT NULL DEFAULT 600",
            "click_delay_ms_max": "ALTER TABLE runtime_state ADD COLUMN click_delay_ms_max INTEGER NOT NULL DEFAULT 1800",
            "max_actions_per_hour": "ALTER TABLE runtime_state ADD COLUMN max_actions_per_hour INTEGER NOT NULL DEFAULT 25",
            "cooldown_after_rate_limit_seconds": (
                "ALTER TABLE runtime_state ADD COLUMN cooldown_after_rate_limit_seconds "
                "FLOAT NOT NULL DEFAULT 300.0"
            ),
            "auth_recheck_every_n_tasks": (
                "ALTER TABLE runtime_state ADD COLUMN auth_recheck_every_n_tasks "
                "INTEGER NOT NULL DEFAULT 5"
            ),
            "rate_limit_cooldown_until": (
                "ALTER TABLE runtime_state ADD COLUMN rate_limit_cooldown_until DATETIME"
            ),
        }
        for column, sql in alterations.items():
            if column not in existing:
                await conn.execute(text(sql))


async def close_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
