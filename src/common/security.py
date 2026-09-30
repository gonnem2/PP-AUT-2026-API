import asyncio
import datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from src.settings import settings

password_hasher = PasswordHasher()

password_semaphore = asyncio.Semaphore(20)


######################### КОДИРОВКА ПАРОЛЯ ##############################
def encode_password(password: str) -> str:
    return password_hasher.hash(
        password,
    )


def check_password(password: str, hashed_password: str) -> bool:
    try:
        return password_hasher.verify(
            hashed_password,
            password,
        )
    except (VerificationError, InvalidHashError):
        return False


######################## JWT ########################################


# Создание токена
def create_tokens(user_id: int):
    # payload access токена
    access_payload = {
        "sub": str(user_id),
        "exp": datetime.datetime.now(tz=ZoneInfo(settings.timezone))
        + datetime.timedelta(minutes=settings.security.access_token_expire_minutes),
    }

    # Получаем токен
    access_token = jwt.encode(
        payload=access_payload,
        key=settings.security.secret_key,
        algorithm=settings.security.algorithm,
    )
    refresh_token = uuid4().hex

    return access_token, refresh_token


# Проверка токена
def decode_access_token(access_token: str) -> int:
    payload = jwt.decode(
        access_token,
        key=settings.security.secret_key,
        algorithms=[settings.security.algorithm],
        options={"require": ["sub", "exp"]},
    )
    user_id = payload["sub"]
    if not isinstance(user_id, str) or not user_id.isdecimal() or int(user_id) <= 0:
        raise jwt.InvalidTokenError()
    return int(user_id)
