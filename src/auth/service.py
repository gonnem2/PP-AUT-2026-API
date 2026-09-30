import asyncio
import logging
from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.exception import InvalidToken
from src.auth.schemas import TokensOut
from src.common.db.session import get_async_session
from src.common.redis.session import get_redis_session
from src.common.security import check_password, create_tokens, password_semaphore
from src.settings import settings
from src.user import repo as user_repo
from src.user.dto import UserDTO
from src.user.exception import UserNotFound

logger = logging.getLogger(__name__)


class AuthService:
    def __init__(self, db_session: AsyncSession, redis_session: Redis):
        self.db_session = db_session
        self.redis_session = redis_session

    async def authenticate(self, email: str, password: str) -> TokensOut:
        # Проверяем есть ли пользователь с таким email
        user_exists: UserDTO | None = await user_repo.fetch_user_by_email(
            db_sesion=self.db_session,
            email=email,
            is_active=True,
        )

        if not user_exists:
            logger.warning("Пользователь с таким EMAIL не найден")
            raise UserNotFound()

        # Проверяем что пароли совпадают
        async with password_semaphore:
            is_relevant = await asyncio.to_thread(
                check_password, password, user_exists.hashed_password
            )

        if not is_relevant:
            logger.warning(f"Пароли не сопадают - кидаем NOT FOUND: {email}")
            raise UserNotFound()
        # На данном этапе проверили пароли и почту и is_active => следовательно пользователь аутентифицирован
        # Будем генерировать токены

        return await self.create_session(user_exists.id)

    async def create_session(self, user_id: int) -> TokensOut:
        access, refresh = create_tokens(user_id)
        # После создания токенов - сохраняем их в REDIS
        await self.redis_session.set(
            f"session:{refresh}",
            str(user_id),
            ex=settings.security.refresh_token_expire_minutes * 60,
        )
        return TokensOut(access_token=access, refresh_token=refresh)

    async def refresh(self, refresh_token: str) -> TokensOut:
        # Забираем сессию один раз - старый refresh больше не используем
        user_id = await self.redis_session.getdel(f"session:{refresh_token}")
        if user_id is None:
            raise InvalidToken()

        user_exists = await user_repo.fetch_user_by_id(
            db_sesion=self.db_session,
            user_id=int(user_id),
        )
        if not user_exists or not user_exists.is_active:
            raise InvalidToken()

        return await self.create_session(user_exists.id)

    async def logout(self, refresh_token: str) -> None:
        # Удаляем только эту сессию пользователя
        await self.redis_session.delete(f"session:{refresh_token}")


async def _get_auth_service(
    db_session: Annotated[AsyncSession, Depends(get_async_session)],
    redis_session: Annotated[Redis, Depends(get_redis_session)],
) -> AuthService:
    return AuthService(db_session, redis_session)
