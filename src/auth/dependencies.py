from typing import Annotated

import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.exception import InvalidToken
from src.common.db.session import get_async_session
from src.common.security import decode_access_token
from src.user import repo as user_repo
from src.user.dto import UserDTO

bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db_session: Annotated[AsyncSession, Depends(get_async_session)],
) -> UserDTO:
    if credentials is None:
        raise InvalidToken()

    # Проверяем подпись и время действия access токена
    try:
        user_id = decode_access_token(credentials.credentials)
    except (jwt.InvalidTokenError, ValueError, TypeError, OverflowError):
        raise InvalidToken() from None

    # Дополнительно проверяем что пользователь еще есть в БД и активен
    user_exists = await user_repo.fetch_user_by_id(
        db_sesion=db_session,
        user_id=user_id,
    )
    if not user_exists or not user_exists.is_active:
        raise InvalidToken()

    return user_exists
