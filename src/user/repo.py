from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.db.models import User
from src.user.dto import UserDTO


async def fetch_user_by_id(db_sesion: AsyncSession, user_id: int) -> UserDTO | None:
    stmt = select(User).where(User.id == user_id)
    res = (await db_sesion.execute(stmt)).scalar_one_or_none()

    if not res:
        return None
    return UserDTO(
        id=res.id,
        email=res.email,
        hashed_password=res.hashed_password,
        name=res.name,
        is_active=res.is_active,
        is_verified=res.is_verified,
    )


async def fetch_user_by_email(
    db_sesion: AsyncSession, email: str, is_active: bool = True
) -> UserDTO | None:
    stmt = select(User).where(User.email == email, User.is_active == is_active)
    res = (await db_sesion.execute(stmt)).scalar_one_or_none()

    if not res:
        return None
    return UserDTO(
        id=res.id,
        email=res.email,
        hashed_password=res.hashed_password,
        name=res.name,
        is_active=res.is_active,
        is_verified=res.is_verified,
    )


async def create_user(
    db_session: AsyncSession,
    username: str,
    hashed_password: str,
    email: str,
):
    """Создание пользователя"""
    stmt = (
        insert(User)
        .values(name=username, hashed_password=hashed_password, email=email)
        .on_conflict_do_nothing(
            index_elements=["email"],
        )
        .returning(
            User.id,
            User.name,
            User.hashed_password,
            User.email,
            User.is_active,
            User.is_verified,
        )
    )

    res = await db_session.execute(stmt)
    user_row = res.one_or_none()
    if not user_row:
        return None
    return UserDTO(
        id=user_row.id,
        name=user_row.name,
        hashed_password=user_row.hashed_password,
        email=user_row.email,
        is_active=user_row.is_active,
        is_verified=user_row.is_verified,
    )
