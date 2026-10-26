from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware

from src.auth.router import router as auth_router
from src.common.db.session import async_engine
from src.common.redis.session import pool_session
from src.errors import AppError, app_error_handler, unhandled_exception_handler
from src.projects.router import router as projects_router
from src.resume.github_router import router as github_evidence_router
from src.resume.router import router as resume_router
from src.settings import settings
from src.user.router import router as user_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        yield
    finally:
        await pool_session.close_pool()
        await async_engine.dispose()


app = FastAPI(
    title="Супер-приложение",
    version="0.1",
    lifespan=lifespan,
)

app.include_router(auth_router)
app.include_router(user_router)
app.include_router(resume_router)
app.include_router(github_evidence_router)
app.include_router(projects_router)

origins = settings.cors_origins

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
