from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from src.errors import ceh, AppError, app_error_handler, unhandled_exception_handler
from src.user.exception import UserAlreadyExists

app = FastAPI(
    title="Супер-приложение",
    version="0.1",
)

origins = ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    return {
        "status": "ok",
    }


@app.get("/ping")
async def ready():
    return {
        "status": "ok",
    }


# Регистрируем обработчики ошибок
app.add_exception_handler(AppError, app_error_handler)

app.add_exception_handler(Exception, unhandled_exception_handler)