from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.common.db.session import get_async_session
from src.projects.dto import (
    CurrentReport,
    LibraryProject,
    ReportJob,
    RepositoryMetadata,
)
from src.projects.schemas import GenerateReport, LibrarySave, profile_username
from src.projects.service import ProjectService
from src.user.dto import UserDTO

router = APIRouter(prefix="/api/v1/github", tags=["github-library-and-reports"])
User = Annotated[UserDTO, Depends(get_current_user)]


async def get_service(
    db_session: Annotated[AsyncSession, Depends(get_async_session)],
) -> ProjectService:
    return ProjectService(db_session)


Service = Annotated[ProjectService, Depends(get_service)]


@router.get("/repositories")
async def repositories(
    profile_url: Annotated[str, Query(max_length=200)], user: User, service: Service
) -> list[RepositoryMetadata]:
    try:
        username = profile_username(profile_url)
    except ValueError:
        raise HTTPException(422, "Нужна ссылка https://github.com/username") from None
    return await service.discover(username)


@router.get("/library")
async def library(user: User, service: Service) -> list[LibraryProject]:
    return await service.library(user.id)


@router.put("/library")
async def save_library(
    data: LibrarySave, user: User, service: Service
) -> list[LibraryProject]:
    return await service.save_library(user.id, data)


@router.delete("/library/{repo_id}", status_code=204)
async def remove(
    repo_id: Annotated[str, Path(pattern=r"^[0-9]{1,20}$")],
    user: User,
    service: Service,
) -> Response:
    await service.remove(user.id, repo_id)
    return Response(status_code=204)


@router.post("/report-jobs", status_code=202)
async def generate(data: GenerateReport, user: User, service: Service) -> ReportJob:
    return await service.generate(user.id, data)


@router.get("/report-jobs/{job_id}")
async def get_job(job_id: UUID, user: User, service: Service) -> ReportJob:
    return await service.get_job(user.id, job_id)


@router.get("/report")
async def current_report(user: User, service: Service) -> CurrentReport:
    return await service.current_report(user.id)
