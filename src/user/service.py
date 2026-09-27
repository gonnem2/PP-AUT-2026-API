import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.common.security import encode_password, password_semaphore
from src.user import repo as user_repo
from src.user.dto import UserDTO
from src.user.exception import UserAlreadyExists

logger = logging.getLogger(__name__)


class UserService:
    def __init__(self, db_session: AsyncSession):
        self.db_session = db_session

    async def create_user(
        self,
        username: str,
        password: str,
        email: str,
    ) -> UserDTO:
        """Создание нового пользователя"""

        # Проверяем, есть ли пользователь в БД
        users_exits: UserDTO | None = await user_repo.fetch_user_by_email(
            db_sesion=self.db_session,
            email=email,
        )

        if users_exits is not None:
            logger.info(
                "Пользователь с таким email уже создан", extra={"username": username}
            )
            raise UserAlreadyExists()

        # Хешируем пароль в отдельном потоке, чтобы ничего здесь не заблочить нам
        async with password_semaphore:
            hashed_password = await asyncio.to_thread(encode_password, password)

        logger.info(f"Создание пользователя - {username}")
        # Если пользователя с таким email нету - то создаем пользователя
        created_user: UserDTO | None = await user_repo.create_user(
            db_session=self.db_session,
            username=username,
            hashed_password=hashed_password,
            email=email,
        )

        if created_user is None:
            logger.warning(f"Пользователь не был создан - конфликт по email: {email}")
            raise UserAlreadyExists()

        await self.db_session.commit()

        # Если все гуд - то пользователь создан
        return created_user
