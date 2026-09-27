import logging

from fastapi import Request
from starlette.responses import JSONResponse

from src.user.exception import UserAlreadyExists

logger = logging.getLogger(__name__)


class AppError(Exception):
    code = "APP_ERROR"
    message = "Application error"

    def __init__(self, message: str | None = None):
        self.message = message or self.message
        super().__init__(self.message)


ERROR_STATUS_CODES: dict[type[AppError], int] = {
    UserAlreadyExists: 409,
}


async def unhandled_exception_handler(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    logger.exception(
        "Unhandled exception",
        extra={
            "method": request.method,
            "path": request.url.path,
        },
    )

    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "Internal server error",
            }
        },
    )


async def app_error_handler(
    request: Request,
    exc: AppError,
) -> JSONResponse:
    status_code = ERROR_STATUS_CODES.get(
        type(exc),
        500,
    )

    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
            }
        },
    )
