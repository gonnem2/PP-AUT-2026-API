"""Адаптация внешнего GitHub JSON в типизированные метаданные."""

from urllib.parse import quote

from fastapi import HTTPException
from pydantic import TypeAdapter

from src.projects.dto import (
    RemoteCommit,
    RemoteRepository,
    RepositoryHead,
    RepositoryMetadata,
)
from src.resume import github


class GitHubClient:
    async def repositories(self, username: str) -> list[RepositoryMetadata]:
        result: list[RepositoryMetadata] = []
        async with github.client() as client:
            for page in range(1, 11):
                rows = TypeAdapter(list[RemoteRepository]).validate_python(
                    await github.get(
                        client,
                        f"/users/{username}/repos",
                        per_page=100,
                        page=page,
                        sort="updated",
                    )
                )
                result.extend(
                    self.metadata(row)
                    for row in rows
                    if not row.private and row.visibility == "public"
                )
                if len(rows) < 100:
                    break
        return result

    async def repository(self, repo_id: str) -> RemoteRepository:
        async with github.client() as client:
            remote = TypeAdapter(RemoteRepository).validate_python(
                await github.get(client, f"/repositories/{repo_id}")
            )
        self.public(remote)
        return remote

    @staticmethod
    def public(value: RemoteRepository) -> None:
        if value.private or value.visibility != "public":
            raise HTTPException(404, "Публичный репозиторий не найден")

    @staticmethod
    def metadata(value: RemoteRepository) -> RepositoryMetadata:
        tags = ([value.language] if value.language else []) + value.topics
        unique_tags: list[str] = []
        for tag in tags:
            if tag not in unique_tags:
                unique_tags.append(tag)
        return RepositoryMetadata(
            id=str(value.id),
            name=value.name,
            description=value.description or "",
            tags=unique_tags,
            full_name=value.full_name,
            repository_url=value.html_url,
        )

    async def head(self, repo_id: str) -> RepositoryHead:
        # Сначала проверяем публичность, даже если серверный токен имеет расширенный доступ.
        async with github.client() as client:
            value = TypeAdapter(RemoteRepository).validate_python(
                await github.get(client, f"/repositories/{repo_id}")
            )
            self.public(value)
            if not value.default_branch or value.size == 0:
                raise HTTPException(422, "В репозитории нет коммитов")
            path = "/repos/" + "/".join(
                quote(part, safe="") for part in value.full_name.split("/")
            )
            commit = TypeAdapter(RemoteCommit).validate_python(
                await github.get(
                    client, path + "/commits/" + quote(value.default_branch, safe="")
                )
            )
        return RepositoryHead(
            repository_url=value.html_url,
            commit_sha=commit.sha,
            default_branch=value.default_branch,
        )
