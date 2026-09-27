import asyncio

from argon2 import PasswordHasher

password_hasher = PasswordHasher()

password_semaphore = asyncio.Semaphore(10)


def encode_password(password: str) -> str:
    return password_hasher.hash(
        password,
    )


def check_password(password: str, hashed_password: str) -> bool:
    return password_hasher.verify(
        hashed_password,
        password,
    )
