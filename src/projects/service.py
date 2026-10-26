"""Сценарии библиотеки GitHub и обработки отчётов; SQL находится в repo."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.db.models import GitHubReportJob
from src.projects import repo
from src.projects.dto import (
    CurrentReport,
    GitHubGateway,
    LibraryProject,
    PreparationJob,
    ReportJob,
    RepositoryMetadata,
    ResultDisposition,
)
from src.projects.exception import (
    InvalidCoreResult,
    ProjectNotFound,
    ReportConflict,
    ReportJobNotFound,
)
from src.projects.github_client import GitHubClient
from src.projects.schemas import (
    CachedReport,
    CoreRepository,
    CoreRequest,
    CoreResult,
    GenerateReport,
    LibrarySave,
)
from src.projects.utils import (
    TERMINAL_STATUSES,
    context_hash,
    context_values,
    is_latest_job,
    job_view,
    library_view,
    prepared_request,
    project_snapshot,
    projects_match_request,
    validate_core_result,
)


class ProjectService:
    def __init__(
        self, db_session: AsyncSession, github_client: GitHubGateway | None = None
    ) -> None:
        self.db = db_session
        self.github = github_client or GitHubClient()

    async def discover(self, username: str) -> list[RepositoryMetadata]:
        # Авторизация могла открыть транзакцию: закрываем её перед HTTP к GitHub.
        await self.db.rollback()
        return await self.github.repositories(username)

    async def library(self, user_id: int) -> list[LibraryProject]:
        return [
            library_view(project) for project in await repo.projects(self.db, user_id)
        ]

    async def save_library(
        self, user_id: int, data: LibrarySave
    ) -> list[LibraryProject]:
        await self.db.rollback()
        # Публичность проверяем до записи. Метаданные клиента не принимаются.
        remotes = [
            await self.github.repository(project.repo_id) for project in data.projects
        ]
        state = await repo.lock_state(self.db, user_id)
        existing = await repo.projects(
            self.db, user_id, [project.repo_id for project in data.projects]
        )
        changed_selected = False
        for project, remote in zip(data.projects, remotes, strict=True):
            old = next(
                (item for item in existing if item.repo_id == project.repo_id), None
            )
            changed = old is None or context_hash(context_values(old)) != context_hash(
                context_values(project)
            )
            if project.repo_id in state.selected_repo_ids and changed:
                changed_selected = True
            await repo.save_project(
                self.db, user_id, project, GitHubClient.metadata(remote), old
            )
        if changed_selected:
            state.library_revision += 1
        await self.db.commit()
        self.db.expire_all()
        return await self.library(user_id)

    async def remove(self, user_id: int, repo_id: str) -> None:
        state = await repo.lock_state(self.db, user_id)
        if not await repo.delete_project(self.db, user_id, repo_id):
            raise ProjectNotFound()
        if repo_id in state.selected_repo_ids:
            state.library_revision += 1
            state.selected_repo_ids = [
                item for item in state.selected_repo_ids if item != repo_id
            ]
        await self.db.commit()

    async def generate(self, user_id: int, data: GenerateReport) -> ReportJob:
        state = await repo.lock_state(self.db, user_id)
        previous = await repo.find_request(self.db, user_id, data.request_id)
        if previous is not None:
            if sorted(data.repo_ids) != sorted(
                item.repo_id for item in previous.input_snapshot
            ):
                raise ReportConflict(
                    "request_id уже использован для другого набора проектов"
                )
            return job_view(previous)
        projects = await repo.projects(self.db, user_id, data.repo_ids)
        if len(projects) != len(data.repo_ids):
            raise ProjectNotFound()
        try:
            snapshots = [project_snapshot(project) for project in projects]
        except ValueError:
            raise HTTPException(
                422,
                "Заполните аккаунт, описание работы и дату начала для каждого проекта",
            ) from None
        # Снимок и указатель на последнее задание сохраняются одной транзакцией.
        job = GitHubReportJob(
            id=str(uuid4()),
            user_id=user_id,
            request_id=str(data.request_id),
            message_id=str(uuid4()),
            status="queued",
            input_revision=state.library_revision,
            input_snapshot=snapshots,
            attempts=0,
            available_at=datetime.now(UTC),
        )
        self.db.add(job)
        state.latest_job_id = job.id
        state.selected_repo_ids = list(data.repo_ids)
        await self.db.commit()
        await self.db.refresh(job)
        return job_view(job)

    async def get_job(self, user_id: int, job_id: UUID | str) -> ReportJob:
        job = await repo.job(self.db, job_id, user_id)
        if job is None:
            raise ReportJobNotFound()
        return job_view(job)

    async def current_report(self, user_id: int) -> CurrentReport:
        state = await repo.state(self.db, user_id)
        if state is None:
            return CurrentReport()
        latest = (
            await repo.job(self.db, state.latest_job_id, user_id)
            if state.latest_job_id
            else None
        )
        completed = (
            await repo.job(self.db, state.report_job_id, user_id)
            if state.report_job_id
            else None
        )
        return CurrentReport(
            report_text=state.report_text,
            summary_ru=state.summary_ru,
            is_current=state.report_text is not None
            and state.report_job_id == state.latest_job_id
            and state.report_revision == state.library_revision,
            report_job_id=state.report_job_id,
            report_repo_ids=[item.repo_id for item in completed.input_snapshot]
            if completed
            else [],
            selected_repo_ids=state.selected_repo_ids,
            latest_job=job_view(latest) if latest else None,
        )


class ReportProcessingService:
    def __init__(self, github_client: GitHubGateway | None = None) -> None:
        self.github = github_client or GitHubClient()

    async def prepare(self, job: PreparationJob) -> CoreRequest:
        """Выбирает кеш отдельно для каждого проекта и собирает один запрос к ML."""
        repositories: list[CoreRepository] = []
        for item in job.input_snapshot:
            remote = await self.github.head(item.repo_id)
            usable = (
                item.cached_report_text is not None
                and item.cached_sha == remote.commit_sha
                and item.cached_context_hash == item.context_hash
            )
            cached = (
                CachedReport(
                    report_text=item.cached_report_text,
                    summary_ru=item.cached_summary_ru,
                )
                if usable and item.cached_report_text is not None
                else None
            )
            repositories.append(
                CoreRepository(
                    repo_id=item.repo_id,
                    repository_url=remote.repository_url,
                    github_username=item.github_username,
                    declared_role=item.declared_role,
                    started_at=item.started_at,
                    ended_at=item.ended_at,
                    commit_sha=remote.commit_sha,
                    context_hash=item.context_hash,
                    source="cache" if usable else "github",
                    cached_report=cached,
                )
            )
        return CoreRequest(
            message_id=UUID(job.message_id),
            job_id=UUID(job.id),
            input_revision=job.input_revision,
            repositories=repositories,
        )

    async def receive(self, db: AsyncSession, event: CoreResult) -> ResultDisposition:
        """Проверяет ответ и применяет его; транзакцию завершает вызывающий воркер."""
        job = await repo.job(db, event.job_id, lock=True)
        if job is None:
            raise InvalidCoreResult("Неизвестное задание")
        if job.status in TERMINAL_STATUSES:
            return "duplicate_or_terminal"

        request = prepared_request(job)
        validate_core_result(request, event)
        state = await repo.lock_state(db, job.user_id)
        if not is_latest_job(job, state):
            return repo.supersede_job(job)

        if event.type == "github.report.failed":
            return repo.fail_job(job, event)
        projects = await repo.projects(
            db, job.user_id, [item.repo_id for item in request.repositories]
        )
        if not projects_match_request(projects, request):
            return repo.supersede_job(job)
        return repo.complete_report(job, state, projects, event)
