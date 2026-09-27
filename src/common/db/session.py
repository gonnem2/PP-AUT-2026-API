from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.settings import settings

async_engine = create_async_engine(
    url=settings.db_settings.dsn,
    pool_size=20,
    max_overflow=1,
)

Session = async_sessionmaker(
    async_engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def get_async_session():
    async with _get_async_session() as session:
        yield session


@asynccontextmanager
async def _get_async_session() -> AsyncGenerator[AsyncSession, Any]:
    try:
        async with Session() as session:
            yield session

            await session.commit()
    except:
        await session.rollback()
        raise
    finally:
        await session.close()
