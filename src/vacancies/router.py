"""Авторизованный HTTP API вакансий и откликов пользователя."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.common.db.session import get_async_session
from src.resume.schemas import Content
from src.user.dto import UserDTO
from src.vacancies.schemas import (
    ResumeSnapshotView,
    VacancyCreate,
    VacancyPatch,
    VacancyStatus,
    VacancyView,
)
from src.vacancies.service import VacancyService

router = APIRouter(prefix="/api/v1/vacancies", tags=["vacancies"])
User = Annotated[UserDTO, Depends(get_current_user)]


async def get_service(
    db: Annotated[AsyncSession, Depends(get_async_session)],
) -> VacancyService:
    return VacancyService(db)


Service = Annotated[VacancyService, Depends(get_service)]


@router.get("")
async def list_vacancies(
    user: User, service: Service, status: VacancyStatus | None = None
) -> list[VacancyView]:
    return await service.list(user.id, status)


@router.post("", status_code=201)
async def create(data: VacancyCreate, user: User, service: Service) -> VacancyView:
    return await service.create(user.id, data)


@router.get("/{vacancy_id}")
async def get(vacancy_id: UUID, user: User, service: Service) -> VacancyView:
    return await service.get(user.id, vacancy_id)


@router.patch("/{vacancy_id}")
async def update(
    vacancy_id: UUID, data: VacancyPatch, user: User, service: Service
) -> VacancyView:
    return await service.update(user.id, vacancy_id, data)


@router.delete("/{vacancy_id}", status_code=204)
async def delete(vacancy_id: UUID, user: User, service: Service) -> Response:
    await service.delete(user.id, vacancy_id)
    return Response(status_code=204)


@router.get("/{vacancy_id}/resume-snapshot")
async def snapshot(
    vacancy_id: UUID, user: User, service: Service
) -> ResumeSnapshotView:
    saved = await service.snapshot(user.id, vacancy_id)
    # Используем существующий контракт контента резюме только на JSON-границе.
    return ResumeSnapshotView(
        id=saved.resume_id,
        title=saved.title,
        content=Content.model_validate_json(saved.content_json),
    )
