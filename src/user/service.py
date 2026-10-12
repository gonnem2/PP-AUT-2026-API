import asyncio
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from src.common.security import check_password, encode_password, password_semaphore
from src.user import repo as user_repo
from src.user.dto import UserDTO
from src.user.exception import (
    InvalidCurrentPassword,
    PasswordUnchanged,
    ProfileNotFound,
    UserAlreadyExists,
)
from src.user.schemas import ProfileOut

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

    async def get_profile(self, user_id: int) -> ProfileOut:
        profile = await user_repo.fetch_profile(self.db_session, user_id)
        if profile is None:
            raise ProfileNotFound()
        return ProfileOut.model_validate(profile)

    async def save_profile(
        self, user_id: int, values: dict, partial: bool = False
    ) -> ProfileOut:
        if partial:
            profile = await user_repo.update_profile(self.db_session, user_id, values)
        else:
            profile = await user_repo.save_profile(self.db_session, user_id, values)
        if profile is None:
            raise ProfileNotFound()
        result = ProfileOut.model_validate(profile)
        await self.db_session.commit()
        return result

    async def change_password(
        self, user: UserDTO, current_password: str, new_password: str
    ) -> None:
        async with password_semaphore:
            if not await asyncio.to_thread(
                check_password, current_password, user.hashed_password
            ):
                raise InvalidCurrentPassword()
            if current_password == new_password:
                raise PasswordUnchanged()
            hashed_password = await asyncio.to_thread(encode_password, new_password)
        if not await user_repo.replace_password(self.db_session, user, hashed_password):
            raise InvalidCurrentPassword()
        await self.db_session.commit()
