"""SQL библиотеки и outbox; JSON преобразуется в DTO только на границе БД."""

from collections.abc import Sequence
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import TypeAdapter
from sqlalchemy import delete, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.db.models import GitHubReportJob, GitHubReportState, ImportedProject
from src.projects.dto import ClaimUpdate, RepositoryMetadata, Unset
from src.projects.schemas import CoreResult, ProjectInput


async def lock_state(db: AsyncSession, user_id: int) -> GitHubReportState:
    """Создаёт состояние при первом обращении и сериализует изменения одного пользователя."""
    await db.execute(
        insert(GitHubReportState)
        .values(user_id=user_id, library_revision=0, selected_repo_ids=[])
        .on_conflict_do_nothing(index_elements=["user_id"])
    )
    row = await db.scalar(
        select(GitHubReportState)
        .where(GitHubReportState.user_id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


async def state(db: AsyncSession, user_id: int) -> GitHubReportState | None:
    return await db.scalar(
        select(GitHubReportState).where(GitHubReportState.user_id == user_id)
    )


async def projects(
    db: AsyncSession, user_id: int, repo_ids: Sequence[str] | None = None
) -> list[ImportedProject]:
    query = select(ImportedProject).where(ImportedProject.user_id == user_id)
    if repo_ids is not None:
        query = query.where(ImportedProject.repo_id.in_(repo_ids))
    return list((await db.scalars(query.order_by(ImportedProject.created_at))).all())


def project_metadata(project: ImportedProject) -> RepositoryMetadata:
    """Старые записи могут содержать evidence: в DTO метаданных он не попадает."""
    return TypeAdapter(RepositoryMetadata).validate_python(project.data)


async def save_project(
    db: AsyncSession,
    user_id: int,
    values: ProjectInput,
    metadata: RepositoryMetadata,
    existing: ImportedProject | None,
) -> None:
    # Единственная работа с сырым JSON проекта находится здесь. Старый evidence сохраняется.
    stored = TypeAdapter(RepositoryMetadata).dump_python(metadata, mode="json")
    if existing is not None:
        stored = existing.data | stored
    statement = insert(ImportedProject).values(
        user_id=user_id,
        repo_id=values.repo_id,
        data=stored,
        github_username=values.github_username,
        declared_role=values.declared_role,
        started_at=values.started_at,
        ended_at=values.ended_at,
    )
    # Mapping здесь нужен SQLAlchemy для SET, между слоями передаются dataclass DTO.
    await db.execute(
        statement.on_conflict_do_update(
            index_elements=["user_id", "repo_id"],
            set_={
                ImportedProject.data: statement.excluded.data,
                ImportedProject.github_username: statement.excluded.github_username,
                ImportedProject.declared_role: statement.excluded.declared_role,
                ImportedProject.started_at: statement.excluded.started_at,
                ImportedProject.ended_at: statement.excluded.ended_at,
                ImportedProject.updated_at: datetime.now(UTC),
            },
        )
    )


async def delete_project(db: AsyncSession, user_id: int, repo_id: str) -> int:
    result = await db.execute(
        delete(ImportedProject)
        .where(ImportedProject.user_id == user_id, ImportedProject.repo_id == repo_id)
        .returning(ImportedProject.repo_id)
    )
    return 1 if result.scalar_one_or_none() is not None else 0


async def find_request(
    db: AsyncSession, user_id: int, request_id: UUID
) -> GitHubReportJob | None:
    return await db.scalar(
        select(GitHubReportJob).where(
            GitHubReportJob.user_id == user_id,
            GitHubReportJob.request_id == str(request_id),
        )
    )


async def job(
    db: AsyncSession,
    job_id: UUID | str,
    user_id: int | None = None,
    *,
    lock: bool = False,
) -> GitHubReportJob | None:
    query = select(GitHubReportJob).where(GitHubReportJob.id == str(job_id))
    if user_id is not None:
        query = query.where(GitHubReportJob.user_id == user_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return await db.scalar(query)


async def claim(db: AsyncSession, lease_seconds: int) -> GitHubReportJob | None:
    """Забирает новое задание или восстанавливает захват после падения процесса."""
    now = datetime.now(UTC)
    row = await db.scalar(
        select(GitHubReportJob)
        .where(
            GitHubReportJob.available_at <= now,
            or_(
                GitHubReportJob.status == "queued",
                (GitHubReportJob.status == "preparing")
                & (GitHubReportJob.lease_until < now),
            ),
        )
        .order_by(GitHubReportJob.created_at)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if row:
        row.status = "preparing"
        row.attempts += 1
        row.lease_token = str(uuid4())
        row.lease_until = now + timedelta(seconds=lease_seconds)
    return row


async def update_claim(
    db: AsyncSession, job_id: str, token: str, changes: ClaimUpdate
) -> bool:
    """Обновляет только живой lease; Unset сохраняет поле, None явно очищает его."""
    # Это адаптация DTO в SQL SET, а не словарь с бизнес-данными.
    columns = {
        getattr(GitHubReportJob, item.name): getattr(changes, item.name)
        for item in fields(changes)
        if getattr(changes, item.name) is not Unset.VALUE
    }
    result = await db.execute(
        update(GitHubReportJob)
        .where(
            GitHubReportJob.id == job_id,
            GitHubReportJob.status == "preparing",
            GitHubReportJob.lease_token == token,
            GitHubReportJob.lease_until > datetime.now(UTC),
        )
        .values(columns)
        .returning(GitHubReportJob.id)
    )
    return result.scalar_one_or_none() is not None


async def expire(db: AsyncSession) -> None:
    """Завершает ожидание ML по таймауту, не удаляя предыдущий общий отчёт."""
    now = datetime.now(UTC)
    await db.execute(
        update(GitHubReportJob)
        .where(GitHubReportJob.status == "waiting", GitHubReportJob.deadline_at < now)
        .values(status="timed_out", error="ML-сервис не вернул отчёт вовремя")
    )


def supersede_job(job: GitHubReportJob) -> Literal["superseded"]:
    job.status = "superseded"
    return "superseded"


def fail_job(job: GitHubReportJob, event: CoreResult) -> Literal["failed"]:
    job.result_message_id = str(event.message_id)
    job.status = "failed"
    job.error = event.error
    return "failed"


def complete_report(
    job: GitHubReportJob,
    state: GitHubReportState,
    projects: list[ImportedProject],
    event: CoreResult,
) -> Literal["completed"]:
    """Записывает все отчёты в текущую транзакцию: частичного обновления не будет."""
    now = datetime.now(UTC)
    for project in projects:
        result = next(
            item for item in event.repositories if item.repo_id == project.repo_id
        )
        project.report_text = result.report_text
        project.summary_ru = result.summary_ru
        project.report_sha = result.commit_sha
        project.report_context_hash = result.context_hash
        project.report_updated_at = now
    state.report_text = event.report_text
    state.summary_ru = event.summary_ru
    state.report_job_id = job.id
    state.report_revision = job.input_revision
    job.result_message_id = str(event.message_id)
    job.status = "completed"
    job.error = None
    return "completed"
