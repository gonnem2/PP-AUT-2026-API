from typing import Annotated

from fastapi import APIRouter, Request, Response
from fastapi.params import Depends

from src.auth.schemas import AuthData, RefreshData, TokensOut
from src.auth.service import AuthService, _get_auth_service

router = APIRouter(tags=["auth"])


@router.post(
    "/api/v1/login",
    summary="Получаем логин и пароль и возвращаем токены",
)
async def login(
    request: Request,
    login_data: AuthData,
    auth_service: Annotated[AuthService, Depends(_get_auth_service)],
) -> TokensOut:
    """Сервис аутентификации - получаем email и password, возвращаем токены"""

    return await auth_service.authenticate(login_data.email, login_data.password)


@router.post(
    "/api/v1/logout",
    summary="Берем refresh токен и делаем логаут",
    status_code=204,
)
async def logout(
    request: Request,
    refresh_data: RefreshData,
    auth_service: Annotated[AuthService, Depends(_get_auth_service)],
) -> Response:
    await auth_service.logout(refresh_data.refresh_token)
    return Response(status_code=204)


@router.post(
    "/api/v1/refresh",
    summary="Берем refresh токен и возвращаем новые токены",
)
async def refresh(
    request: Request,
    refresh_data: RefreshData,
    auth_service: Annotated[AuthService, Depends(_get_auth_service)],
) -> TokensOut:
    return await auth_service.refresh(refresh_data.refresh_token)
