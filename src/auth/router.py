from fastapi import APIRouter, Request

router = APIRouter(tags=["auth"])


@router.post(
    "/api/v1/login",
    summary="Получаем логин и пароль и возвращаем токены",
)
async def login(request: Request): ...


@router.post(
    "/api/v1/logout",
    summary="Берем refresh токен и делаем логаут",
)
async def logout(request: Request): ...
