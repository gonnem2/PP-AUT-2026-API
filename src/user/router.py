from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.auth.dependencies import get_current_user
from src.common.db.session import get_async_session
from src.user.dto import UserDTO
from src.user.schemas import UserIn, UserOut
from src.user.service import UserService

router = APIRouter(tags=["auth"])


async def _get_user_service(
    db_session: Annotated[AsyncSession, Depends(get_async_session)],
) -> UserService:
    return UserService(
        db_session=db_session,
    )


@router.get("/api/v1/users/me", summary="Получение текущего пользователя")
async def get_me(
    request: Request, current_user: Annotated[UserDTO, Depends(get_current_user)]
) -> UserOut:
    return UserOut.from_dto(current_user)


@router.post(
    "/api/v1/users",
    summary="Создание пользователя",
    status_code=status.HTTP_201_CREATED,
)
async def create_user(
    request: Request,
    user_in_data: UserIn,
    user_service: Annotated[UserService, Depends(_get_user_service)],
) -> UserOut:
    res: UserDTO = await user_service.create_user(
        username=f"{user_in_data.name} {user_in_data.surname}",
        email=user_in_data.email,
        password=user_in_data.password,
    )
    return UserOut.from_dto(res)
