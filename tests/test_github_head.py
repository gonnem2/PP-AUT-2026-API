"""Нулевой размер репозитория не доказывает отсутствия коммитов."""

import unittest
from dataclasses import replace
from unittest.mock import patch

import httpx
from fastapi import HTTPException
from pydantic import TypeAdapter

from src.projects.dto import RemoteRepository
from src.projects.exception import EmptyGitHubRepository
from src.projects.github_client import GitHubClient

SHA = "a" * 40


class GitHubHeadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.remote = RemoteRepository(
            id=123,
            name="project",
            full_name="team/project",
            html_url="https://github.com/team/project",
            private=False,
            default_branch="main",
            size=0,
        )
        self.calls: list[str] = []
        self.commit_status = 200

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        if request.url.path == "/repositories/123":
            return httpx.Response(
                200, content=TypeAdapter(RemoteRepository).dump_json(self.remote)
            )
        if self.commit_status == 200:
            return httpx.Response(200, json={"sha": SHA})
        return httpx.Response(
            self.commit_status, json={"message": "Git Repository is empty."}
        )

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url="https://api.github.com",
            transport=httpx.MockTransport(self.handler),
        )

    async def test_zero_size_with_commit_is_analyzable(self) -> None:
        with patch("src.projects.github_client.github.client", self.client):
            result = await GitHubClient().head("123")
        self.assertEqual(result.commit_sha, SHA)
        self.assertEqual(result.default_branch, "main")
        self.assertEqual(
            self.calls, ["/repositories/123", "/repos/team/project/commits/main"]
        )

    async def test_missing_branch_uses_head_instead_of_guessing_empty(self) -> None:
        self.remote = replace(self.remote, default_branch=None)
        with patch("src.projects.github_client.github.client", self.client):
            result = await GitHubClient().head("123")
        self.assertEqual(result.commit_sha, SHA)
        self.assertEqual(self.calls[-1], "/repos/team/project/commits/HEAD")

    async def test_true_empty_repository_reports_name(self) -> None:
        self.commit_status = 409
        with (
            patch("src.projects.github_client.github.client", self.client),
            self.assertRaises(EmptyGitHubRepository) as caught,
        ):
            await GitHubClient().head("123")
        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("team/project", caught.exception.detail)
        self.assertEqual(len(self.calls), 2)

    async def test_other_http_errors_are_not_reported_as_empty(self) -> None:
        for upstream, expected in ((403, 503), (429, 503), (404, 404), (500, 502)):
            with self.subTest(status=upstream):
                self.commit_status = upstream
                with (
                    patch("src.projects.github_client.github.client", self.client),
                    self.assertRaises(HTTPException) as caught,
                ):
                    await GitHubClient().head("123")
                self.assertEqual(caught.exception.status_code, expected)
                self.assertNotIsInstance(caught.exception, EmptyGitHubRepository)

    async def test_private_repository_is_rejected_before_reading_commit(self) -> None:
        self.remote = replace(self.remote, private=True)
        with (
            patch("src.projects.github_client.github.client", self.client),
            self.assertRaises(HTTPException) as caught,
        ):
            await GitHubClient().head("123")
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(self.calls, ["/repositories/123"])
