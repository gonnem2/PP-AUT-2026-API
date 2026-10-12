from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.db.models import Profile, User
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


async def fetch_profile(db_session: AsyncSession, user_id: int) -> Profile | None:
    return await db_session.scalar(select(Profile).where(Profile.user_id == user_id))


async def save_profile(db_session: AsyncSession, user_id: int, values: dict) -> Profile:
    stmt = insert(Profile).values(user_id=user_id, **values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[Profile.user_id],
        set_={**values, "updated_at": func.now()},
    ).returning(Profile)
    return (await db_session.execute(stmt)).scalar_one()


async def update_profile(
    db_session: AsyncSession, user_id: int, values: dict
) -> Profile | None:
    if not values:
        return await fetch_profile(db_session, user_id)
    stmt = (
        update(Profile)
        .where(Profile.user_id == user_id)
        .values(**values)
        .returning(Profile)
    )
    return (await db_session.execute(stmt)).scalar_one_or_none()


async def replace_password(
    db_session: AsyncSession, user: UserDTO, hashed_password: str
) -> bool:
    stmt = (
        update(User)
        .where(
            User.id == user.id,
            User.hashed_password == user.hashed_password,
            User.is_active.is_(True),
        )
        .values(hashed_password=hashed_password)
        .returning(User.id)
    )
    return (await db_session.execute(stmt)).scalar_one_or_none() is not None
