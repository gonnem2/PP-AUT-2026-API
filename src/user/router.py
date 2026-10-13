from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from src.auth.dependencies import get_current_user
from src.common.db.session import get_async_session
from src.user.dto import UserDTO
from src.user.schemas import (
    PasswordChange,
    ProfileIn,
    ProfileOut,
    ProfilePatch,
    UserIn,
    UserOut,
)
from src.user.service import UserService

router = APIRouter(tags=["users"])


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
        username=" ".join(filter(None, [user_in_data.name, user_in_data.surname])),
        email=user_in_data.email,
        password=user_in_data.password,
    )
    return UserOut.from_dto(res)


@router.get("/api/v1/users/me/profile", summary="Получить свой профиль")
async def get_profile(
    current_user: Annotated[UserDTO, Depends(get_current_user)],
    user_service: Annotated[UserService, Depends(_get_user_service)],
) -> ProfileOut:
    return await user_service.get_profile(current_user.id)


@router.put("/api/v1/users/me/profile", summary="Заполнить или заменить свой профиль")
async def save_profile(
    data: ProfileIn,
    current_user: Annotated[UserDTO, Depends(get_current_user)],
    user_service: Annotated[UserService, Depends(_get_user_service)],
) -> ProfileOut:
    return await user_service.save_profile(current_user.id, data.model_dump())


@router.patch("/api/v1/users/me/profile", summary="Изменить поля своего профиля")
async def edit_profile(
    data: ProfilePatch,
    current_user: Annotated[UserDTO, Depends(get_current_user)],
    user_service: Annotated[UserService, Depends(_get_user_service)],
) -> ProfileOut:
    return await user_service.save_profile(
        current_user.id, data.model_dump(exclude_unset=True), partial=True
    )


@router.post("/api/v1/users/me/password", status_code=204, summary="Сменить пароль")
async def change_password(
    data: PasswordChange,
    current_user: Annotated[UserDTO, Depends(get_current_user)],
    user_service: Annotated[UserService, Depends(_get_user_service)],
) -> Response:
    await user_service.change_password(
        current_user, data.current_password, data.new_password
    )
    return Response(status_code=204)
