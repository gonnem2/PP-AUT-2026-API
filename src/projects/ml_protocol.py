"""Транспорт ML v2 и представление его результата для существующего редактора."""

from datetime import date, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, model_validator
from pydantic.dataclasses import dataclass

from src.projects.exception import InvalidCoreResult
from src.projects.schemas import (
    CoreRepositoryResult,
    CoreRequest,
    CoreResult,
    RepoId,
    Sha,
    StrictDTO,
    Username,
)

ContextHash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
MLStatus = Literal["completed", "partial", "failed"]


@dataclass(kw_only=True)
class MLCandidate(StrictDTO):
    github_login: Username
    display_name: str | None = None


@dataclass(kw_only=True)
class MLRepository(StrictDTO):
    repo_id: RepoId
    repository_url: str
    commit_sha: Sha
    context_hash: ContextHash
    declared_role: str | None = None
    started_at: date | None = None
    ended_at: date | None = None


@dataclass(kw_only=True)
class CoreRequestV2(StrictDTO):
    schema_version: Literal[2] = 2
    type: Literal["github.report.requested"] = "github.report.requested"
    message_id: UUID
    job_id: UUID
    input_revision: int = Field(ge=0)
    candidate: MLCandidate
    repositories: list[MLRepository] = Field(min_length=1, max_length=20)


@dataclass(kw_only=True)
class MLProblem(StrictDTO):
    stage: Literal["input", "collect", "analyze", "profile", "resume"]
    code: str
    message: str
    repository: str | None = None


@dataclass(kw_only=True)
class MLRepositoryResult(StrictDTO):
    repo_id: RepoId
    repository_url: str
    commit_sha: Sha
    context_hash: ContextHash
    status: MLStatus
    cache_hit: bool = False
    analysis: dict[str, JsonValue] | None = None
    problems: list[MLProblem] = Field(default_factory=list)


@dataclass(kw_only=True)
class MLResumeItem(StrictDTO):
    text: str = Field(min_length=1)
    claim_ids: list[str] = Field(min_length=1)


@dataclass(kw_only=True)
class MLResumeSection(StrictDTO):
    title: str = Field(min_length=1)
    items: list[MLResumeItem] = Field(min_length=1)


@dataclass(kw_only=True)
class MLResume(StrictDTO):
    schema_version: Literal["1.0"] = "1.0"
    candidate_name: str = Field(min_length=1)
    headline: MLResumeItem | None = None
    summary: MLResumeItem | None = None
    sections: list[MLResumeSection] = Field(default_factory=list)
    generated_at: datetime


@dataclass(kw_only=True)
class CoreResultV2(StrictDTO):
    schema_version: Literal[2] = 2
    type: Literal["github.report.completed", "github.report.failed"]
    message_id: UUID
    request_message_id: UUID
    job_id: UUID
    input_revision: int = Field(ge=0)
    status: MLStatus
    repositories: list[MLRepositoryResult] = Field(min_length=1, max_length=20)
    # Evidence и claims сохраняются целиком. Для рендера используется только типизированный CV.
    profile: dict[str, JsonValue] | None = None
    resume: MLResume | None = None
    problems: list[MLProblem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None

    @model_validator(mode="after")
    def valid_result(self) -> Self:
        if len({item.repo_id for item in self.repositories}) != len(self.repositories):
            raise ValueError("Повторяющиеся результаты репозитория")
        if self.type == "github.report.completed":
            if (
                self.status == "failed"
                or self.resume is None
                or self.profile is None
                or self.error is not None
            ):
                raise ValueError(
                    "Успешный ответ v2 должен содержать профиль и черновик"
                )
            if not any(item.status != "failed" for item in self.repositories):
                raise ValueError("Нет пригодных результатов анализа")
        elif self.status != "failed" or self.resume is not None or not self.error:
            raise ValueError("Ошибка v2 должна содержать причину")
        return self


WireRequest = CoreRequest | CoreRequestV2
WireResult = CoreResult | CoreResultV2


class MixedGitHubAccounts(ValueError):
    """Один запрос v2 описывает одного GitHub-кандидата."""


class PreparedProtocolMismatch(ValueError):
    """В новой очереди нельзя повторить публикацию сохранённого запроса другой версии."""


def request_v2(request: CoreRequest) -> CoreRequestV2:
    login = request.repositories[0].github_username
    if any(
        item.github_username.casefold() != login.casefold()
        for item in request.repositories
    ):
        raise MixedGitHubAccounts(
            "Для одного отчёта выберите проекты одного GitHub-аккаунта."
        )
    return CoreRequestV2(
        message_id=request.message_id,
        job_id=request.job_id,
        input_revision=request.input_revision,
        candidate=MLCandidate(github_login=login),
        repositories=[
            MLRepository(
                repo_id=item.repo_id,
                repository_url=item.repository_url,
                commit_sha=item.commit_sha,
                context_hash=item.context_hash,
                declared_role=item.declared_role,
                started_at=item.started_at,
                ended_at=item.ended_at,
            )
            for item in request.repositories
        ],
    )


def report_from_v2(request: CoreRequestV2, result: CoreResultV2) -> CoreResult:
    """Проверяет связь с outbox и строит текст для существующей библиотеки и фронта."""
    if (result.job_id, result.request_message_id, result.input_revision) != (
        request.job_id,
        request.message_id,
        request.input_revision,
    ):
        raise InvalidCoreResult("Результат v2 относится к другому запросу или ревизии")
    by_id = {item.repo_id: item for item in result.repositories}
    if set(by_id) != {item.repo_id for item in request.repositories}:
        raise InvalidCoreResult("Результат v2 содержит другой набор репозиториев")
    for item in request.repositories:
        actual = by_id[item.repo_id]
        if (
            actual.commit_sha,
            actual.context_hash,
            actual.repository_url.casefold(),
        ) != (item.commit_sha, item.context_hash, item.repository_url.casefold()):
            raise InvalidCoreResult(
                "SHA, адрес или контекст результата v2 не совпадает"
            )
        if (
            actual.analysis is not None
            and actual.analysis.get("commit_sha") != item.commit_sha
        ):
            raise InvalidCoreResult("SHA вложенного анализа v2 не совпадает")
    if result.type == "github.report.failed":
        assert result.error is not None
        return CoreResult(
            type=result.type,
            message_id=result.message_id,
            job_id=result.job_id,
            error=result.error[:2000],
        )

    assert result.resume is not None
    texts: list[CoreRepositoryResult] = []
    skipped: list[str] = []
    for item in result.repositories:
        name = item.repository_url.removeprefix("https://github.com/")
        if item.status == "failed":
            skipped.append(name)
            continue
        summary = (
            item.analysis.get("project_summary") if item.analysis is not None else None
        )
        if not isinstance(summary, str) or not summary.strip():
            raise InvalidCoreResult("В пригодном анализе v2 нет описания проекта")
        text = f"# {name}\n\n{summary}"
        if item.status == "partial":
            text = f"# {name}\n\n> Репозиторий проанализирован частично.\n\n{summary}"
        texts.append(
            CoreRepositoryResult(
                repo_id=item.repo_id,
                commit_sha=item.commit_sha,
                context_hash=item.context_hash,
                report_text=text[:500000],
                summary_ru=summary[:30000],
            )
        )

    resume = result.resume
    summary = resume.summary.text if resume.summary else None
    lines = ["# Черновик резюме"]
    if result.status == "partial":
        lines.append("> ML сформировал черновик по части доступных данных.")
    if skipped:
        lines.append("Проекты без результата: " + ", ".join(skipped))
    if resume.headline:
        lines.append(resume.headline.text)
    if summary:
        lines.extend(("## О себе", summary))
    for section in resume.sections:
        lines.append(f"## {section.title}")
        lines.extend(f"- {item.text}" for item in section.items)
    return CoreResult(
        type=result.type,
        message_id=result.message_id,
        job_id=result.job_id,
        repositories=texts,
        report_text="\n\n".join(lines)[:1000000],
        summary_ru=summary[:50000] if summary else None,
    )
