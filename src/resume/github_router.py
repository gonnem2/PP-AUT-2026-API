from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.dependencies import get_current_user
from src.common.db.models import ImportedProject
from src.common.db.session import get_async_session
from src.resume import github
from src.resume.github_evidence import Collector
from src.resume.schemas import (
    GitHubCollectionOptions,
    GitHubCollectionRequest,
    GitHubEvidence,
)
from src.user.dto import UserDTO

router = APIRouter(prefix="/api/v1", tags=["github-evidence"])
DB = Annotated[AsyncSession, Depends(get_async_session)]
User = Annotated[UserDTO, Depends(get_current_user)]
RepoId = Annotated[str, Path(pattern=r"^[0-9]{1,20}$")]


@router.post("/github/evidence", response_model=GitHubEvidence)
async def collect_evidence(data: GitHubCollectionRequest, user: User):
    """Preview public evidence for any owner/repo; does not claim account ownership."""
    async with github.client() as client:
        collector = Collector(client, data)
        remote = await collector.fetch(f"/repos/{data.repository}", required=True)
        return await collector.collect(remote)


async def owned_project(db: AsyncSession, user_id: int, repo_id: str):
    row = await db.scalar(
        select(ImportedProject).where(
            ImportedProject.user_id == user_id, ImportedProject.repo_id == repo_id
        )
    )
    if row is None:
        raise HTTPException(404, "Импортированный проект не найден")
    return row


@router.post("/projects/{repo_id}/github-evidence", response_model=GitHubEvidence)
async def save_evidence(
    repo_id: RepoId, data: GitHubCollectionOptions, db: DB, user: User
):
    # Authorize before making any external request. Do not hold a transaction during collection.
    await owned_project(db, user.id, repo_id)
    await db.rollback()
    async with github.client() as client:
        collector = Collector(client, data)
        remote = await collector.fetch(f"/repositories/{repo_id}", required=True)
        report = await collector.collect(remote)
    # Re-read and lock after collection to avoid overwriting concurrent project edits.
    row = await db.scalar(
        select(ImportedProject)
        .where(ImportedProject.user_id == user.id, ImportedProject.repo_id == repo_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise HTTPException(404, "Импортированный проект не найден")
    row.data = {**row.data, "githubEvidence": report.model_dump()}
    await db.commit()
    return report


@router.get("/projects/{repo_id}/github-evidence", response_model=GitHubEvidence)
async def get_evidence(repo_id: RepoId, db: DB, user: User):
    row = await owned_project(db, user.id, repo_id)
    report = row.data.get("githubEvidence")
    if report is None:
        raise HTTPException(404, "Данные о вкладе ещё не собраны")
    return report
