"""Подготовка DTO и чистые проверки; запросов к БД и внешним сервисам здесь нет."""

import hashlib
import json
from dataclasses import replace

from pydantic import TypeAdapter

from src.common.db.models import GitHubReportJob, GitHubReportState, ImportedProject
from src.projects import repo
from src.projects.dto import (
    JobRepository,
    LibraryProject,
    ProjectContext,
    ProjectSnapshot,
    ReportJob,
)
from src.projects.exception import InvalidCoreResult
from src.projects.schemas import CoreRequest, CoreResult, ProjectInput

TERMINAL_STATUSES = frozenset(("completed", "failed", "timed_out", "superseded"))
RESULT_ACCEPTING_STATUSES = frozenset(("queued", "preparing", "waiting"))


def context_values(project: ImportedProject | ProjectInput) -> ProjectContext:
    return ProjectContext(
        github_username=project.github_username,
        declared_role=project.declared_role,
        started_at=project.started_at,
        ended_at=project.ended_at,
    )


def context_hash(context: ProjectContext) -> str:
    """Стабильный хеш входа: сохраняем прежний формат, чтобы не сбросить существующий кеш."""
    normalized = replace(
        context, github_username=(context.github_username or "").lower()
    )
    # Сериализация нужна только для детерминированного хеша, наружу словарь не возвращается.
    encoded = json.dumps(
        TypeAdapter(ProjectContext).dump_python(normalized, mode="json"),
        sort_keys=True,
        ensure_ascii=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def library_view(project: ImportedProject) -> LibraryProject:
    metadata = repo.project_metadata(project)
    context = context_values(project)
    return LibraryProject(
        id=metadata.id,
        name=metadata.name,
        description=metadata.description,
        tags=metadata.tags,
        full_name=metadata.full_name,
        repository_url=metadata.repository_url,
        repo_id=project.repo_id,
        github_username=context.github_username,
        declared_role=context.declared_role,
        started_at=context.started_at,
        ended_at=context.ended_at,
        ready_for_analysis=bool(
            project.github_username and project.declared_role and project.started_at
        ),
        report_text=project.report_text,
        summary_ru=project.summary_ru,
        report_sha=project.report_sha,
        cache_matches_inputs=project.report_text is not None
        and project.report_context_hash == context_hash(context),
        report_updated_at=project.report_updated_at,
    )


def job_view(job: GitHubReportJob) -> ReportJob:
    request = job.request_payload
    repositories = (
        []
        if request is None
        else [
            JobRepository(
                repo_id=item.repo_id, commit_sha=item.commit_sha, source=item.source
            )
            for item in request.repositories
        ]
    )
    return ReportJob(
        id=job.id,
        status=job.status,
        repo_ids=[item.repo_id for item in job.input_snapshot],
        attempts=job.attempts,
        error=job.error,
        created_at=job.created_at,
        repositories=repositories,
    )


def project_snapshot(project: ImportedProject) -> ProjectSnapshot:
    """Фиксирует входные данные и кеш без дальнейших обращений к изменяемой ORM-записи."""
    if (
        not project.github_username
        or not project.declared_role
        or project.started_at is None
    ):
        raise ValueError("Не заполнены аккаунт, роль или дата начала")
    return ProjectSnapshot(
        repo_id=project.repo_id,
        github_username=project.github_username,
        declared_role=project.declared_role,
        started_at=project.started_at,
        ended_at=project.ended_at,
        context_hash=context_hash(context_values(project)),
        cached_sha=project.report_sha,
        cached_context_hash=project.report_context_hash,
        cached_report_text=project.report_text,
        cached_summary_ru=project.summary_ru,
    )


def prepared_request(job: GitHubReportJob) -> CoreRequest:
    """Queued допустим: ответ мог прийти после потери confirm, но до повторной отправки."""
    if job.request_payload is None or job.status not in RESULT_ACCEPTING_STATUSES:
        raise InvalidCoreResult("Для задания ещё не подготовлен запрос")
    return job.request_payload


def validate_core_result(request: CoreRequest, result: CoreResult) -> None:
    """Сопоставляет полный набор проектов, SHA и контекст с исходным запросом."""
    if result.type == "github.report.failed":
        return
    if {item.repo_id for item in request.repositories} != {
        item.repo_id for item in result.repositories
    }:
        raise InvalidCoreResult("Результат содержит другой набор репозиториев")
    for repository in request.repositories:
        analysis = next(
            item for item in result.repositories if item.repo_id == repository.repo_id
        )
        if (
            analysis.commit_sha != repository.commit_sha
            or analysis.context_hash != repository.context_hash
        ):
            raise InvalidCoreResult(
                "SHA или контекст результата не соответствует запросу"
            )


def is_latest_job(job: GitHubReportJob, state: GitHubReportState) -> bool:
    return (
        state.latest_job_id == job.id and state.library_revision == job.input_revision
    )


def projects_match_request(
    projects: list[ImportedProject], request: CoreRequest
) -> bool:
    """Локальные правки пользователя во время работы ML делают старый ответ неактуальным."""
    if {project.repo_id for project in projects} != {
        item.repo_id for item in request.repositories
    }:
        return False
    for project in projects:
        requested = next(
            item for item in request.repositories if item.repo_id == project.repo_id
        )
        if context_hash(context_values(project)) != requested.context_hash:
            return False
    return True
