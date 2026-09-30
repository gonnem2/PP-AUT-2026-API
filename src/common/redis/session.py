from collections.abc import AsyncGenerator

import redis.asyncio as redis

from src.settings import settings


class RedisPool:
    def __init__(
        self,
        redis_url: str,
        db: int,
    ):
        self._redis_url = redis_url
        self._db: int = db
        self._connection_pool: redis.ConnectionPool | None = None

    @property
    def connected(self) -> bool:
        return self._connection_pool is not None

    @property
    def pool(self) -> redis.ConnectionPool:
        if self._connection_pool is None:
            self._connection_pool = redis.ConnectionPool.from_url(
                self._redis_url,
                db=self._db,
                decode_responses=True,  # иначе везде будут bytes вместо str
                max_connections=20,
                socket_connect_timeout=3,
                socket_keepalive=True,
                health_check_interval=30,  # пингует простаивающие соединения
            )
        return self._connection_pool

    async def close_pool(self):
        if self._connection_pool is not None:
            await self._connection_pool.aclose()
            self._connection_pool = None


pool_session = RedisPool(redis_url=settings.redis_dsn, db=1)


async def get_redis_session() -> AsyncGenerator[redis.Redis]:
    async with redis.Redis(connection_pool=pool_session.pool) as session:
        yield session
