"""Типизированные dataclass DTO для библиотеки, очереди и ответов API."""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Literal, Protocol

from pydantic import ConfigDict
from pydantic.dataclasses import dataclass as validated_dataclass

from src.projects.schemas import CoreRequest

type ResultDisposition = Literal[
    "completed", "failed", "superseded", "duplicate_or_terminal"
]


@validated_dataclass(config=ConfigDict(extra="ignore"), kw_only=True)
class RemoteRepository:
    """Нужная нам часть ответа GitHub; прочие поля API игнорируются на границе."""

    id: int
    name: str
    full_name: str
    html_url: str
    private: bool
    description: str | None = None
    visibility: str = "public"
    language: str | None = None
    topics: list[str] = field(default_factory=list)
    size: int | None = None
    default_branch: str | None = None


@validated_dataclass(config=ConfigDict(extra="ignore"), kw_only=True)
class RepositoryMetadata:
    id: str
    name: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    full_name: str | None = None
    repository_url: str | None = None


@validated_dataclass(kw_only=True)
class RepositoryHead:
    repository_url: str
    commit_sha: str
    default_branch: str


@dataclass(kw_only=True)
class ProjectContext:
    github_username: str | None
    declared_role: str | None
    started_at: date | None
    ended_at: date | None


@dataclass(kw_only=True)
class ProjectSnapshot:
    repo_id: str
    github_username: str
    declared_role: str
    started_at: date
    ended_at: date | None
    context_hash: str
    cached_sha: str | None
    cached_context_hash: str | None
    cached_report_text: str | None
    cached_summary_ru: str | None


@dataclass(kw_only=True)
class PreparationJob:
    id: str
    message_id: str
    input_revision: int
    input_snapshot: list[ProjectSnapshot]


@dataclass(kw_only=True)
class ClaimedJob(PreparationJob):
    request_payload: CoreRequest | None
    lease_token: str
    attempts: int


@validated_dataclass(kw_only=True)
class LibraryProject(RepositoryMetadata, ProjectContext):
    repo_id: str
    ready_for_analysis: bool
    report_text: str | None
    summary_ru: str | None
    report_sha: str | None
    cache_matches_inputs: bool
    report_updated_at: datetime | None


@dataclass(kw_only=True)
class JobRepository:
    repo_id: str
    commit_sha: str
    source: Literal["github", "cache"]


@dataclass(kw_only=True)
class ReportJob:
    id: str
    status: str
    repo_ids: list[str]
    attempts: int
    error: str | None
    created_at: datetime
    repositories: list[JobRepository]


@dataclass(kw_only=True)
class CurrentReport:
    report_text: str | None = None
    summary_ru: str | None = None
    is_current: bool = False
    selected_repo_ids: list[str] = field(default_factory=list)
    report_repo_ids: list[str] = field(default_factory=list)
    report_job_id: str | None = None
    latest_job: ReportJob | None = None


class Unset(Enum):
    """Отличает отсутствие обновления поля от явного присваивания NULL."""

    VALUE = "unset"


@dataclass(kw_only=True)
class ClaimUpdate:
    request_payload: CoreRequest | Unset = Unset.VALUE
    status: str | Unset = Unset.VALUE
    lease_until: datetime | None | Unset = Unset.VALUE
    lease_token: str | None | Unset = Unset.VALUE
    deadline_at: datetime | Unset = Unset.VALUE
    available_at: datetime | Unset = Unset.VALUE
    error: str | None | Unset = Unset.VALUE


class GitHubGateway(Protocol):
    async def repositories(self, username: str) -> list[RepositoryMetadata]: ...
    async def repository(self, repo_id: str) -> RemoteRepository: ...
    async def head(self, repo_id: str) -> RepositoryHead: ...


class ReportPublisher(Protocol):
    async def publish(self, payload: CoreRequest) -> None: ...


@validated_dataclass(config=ConfigDict(extra="ignore"))
class RemoteCommit:
    sha: str
