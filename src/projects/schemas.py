from datetime import date
from typing import Annotated, Literal, Self
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)
from pydantic.dataclasses import dataclass

RepoId = Annotated[str, Field(pattern=r"^[0-9]{1,20}$")]
Username = Annotated[
    str, Field(pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
]
Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]


@dataclass(config=ConfigDict(extra="forbid"), kw_only=True)
class StrictDTO:
    """Базовый dataclass с проверкой входа и запретом неизвестных полей."""


def profile_username(value: str) -> str:
    value = value.strip()
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "github.com"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Нужна ссылка https://github.com/username")
    username = parsed.path.strip("/")
    return TypeAdapter(Username).validate_python(username)


@dataclass(kw_only=True)
class ProjectInput(StrictDTO):
    repo_id: RepoId
    github_username: Username
    declared_role: str = Field(min_length=1, max_length=3000)
    started_at: date
    ended_at: date | None = None

    @field_validator("declared_role")
    @classmethod
    def nonempty_role(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Опишите свою работу в проекте")
        return value.strip()

    @model_validator(mode="after")
    def period(self) -> Self:
        if not 1970 <= self.started_at.year <= 2099 or (
            self.ended_at
            and (self.ended_at < self.started_at or self.ended_at.year > 2099)
        ):
            raise ValueError("Некорректный период работы")
        return self


@dataclass(kw_only=True)
class LibrarySave(StrictDTO):
    projects: list[ProjectInput] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len({p.repo_id for p in self.projects}) != len(self.projects):
            raise ValueError("Проекты не должны повторяться")
        return self


@dataclass(kw_only=True)
class GenerateReport(StrictDTO):
    repo_ids: list[RepoId] = Field(min_length=1, max_length=20)
    request_id: UUID

    @model_validator(mode="after")
    def unique(self) -> Self:
        if len(set(self.repo_ids)) != len(self.repo_ids):
            raise ValueError("Проекты не должны повторяться")
        return self


@dataclass(kw_only=True)
class CachedReport(StrictDTO):
    report_text: str = Field(min_length=1, max_length=500000)
    summary_ru: str | None = Field(default=None, max_length=30000)


@dataclass(kw_only=True)
class CoreRepository(StrictDTO):
    repo_id: RepoId
    repository_url: str
    github_username: Username
    declared_role: str
    started_at: date
    ended_at: date | None
    commit_sha: Sha
    context_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    source: Literal["github", "cache"]
    cached_report: CachedReport | None = None

    @model_validator(mode="after")
    def cache_presence(self) -> Self:
        if (self.source == "cache") != (self.cached_report is not None):
            raise ValueError("source должен соответствовать наличию кеша")
        return self


@dataclass(kw_only=True)
class CoreRequest(StrictDTO):
    schema_version: Literal[1] = 1
    type: Literal["github.report.requested"] = "github.report.requested"
    message_id: UUID
    job_id: UUID
    input_revision: int
    repositories: list[CoreRepository] = Field(min_length=1, max_length=20)


@dataclass(kw_only=True)
class CoreRepositoryResult(CachedReport):
    repo_id: RepoId
    commit_sha: Sha
    context_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


@dataclass(kw_only=True)
class CoreResult(StrictDTO):
    schema_version: Literal[1] = 1
    type: Literal["github.report.completed", "github.report.failed"]
    message_id: UUID
    job_id: UUID
    repositories: list[CoreRepositoryResult] = Field(
        default_factory=list, max_length=20
    )
    report_text: str | None = Field(default=None, min_length=1, max_length=1000000)
    summary_ru: str | None = Field(default=None, max_length=50000)
    error: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def shape(self) -> Self:
        if len({r.repo_id for r in self.repositories}) != len(self.repositories):
            raise ValueError("Повторяющиеся результаты репозитория")
        if self.type == "github.report.completed" and (
            not self.report_text or not self.repositories or self.error
        ):
            raise ValueError("Успешный результат должен содержать отчёты без ошибки")
        if self.type == "github.report.failed" and (
            not self.error or self.repositories or self.report_text is not None
        ):
            raise ValueError("Ошибка должна содержать error без успешного результата")
        return self
