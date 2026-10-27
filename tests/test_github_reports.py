"""Контракты dataclass DTO, кеш и HTTP-граница без внешних сервисов."""

import hashlib
import unittest
from dataclasses import is_dataclass, replace
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from src.auth.dependencies import get_current_user
from src.common.db.models import ImportedProject
from src.common.worker import ReportWorker
from src.main import app
from src.projects.dto import (
    PreparationJob,
    ProjectContext,
    ProjectSnapshot,
    RemoteRepository,
    ReportJob,
    RepositoryHead,
)
from src.projects.exception import InvalidCoreResult, ProjectNotFound, ReportJobNotFound
from src.projects.github_client import GitHubClient
from src.projects.messaging import ReportBroker, encode_payload
from src.projects.router import get_service
from src.projects.schemas import (
    CoreRepository,
    CoreRepositoryResult,
    CoreRequest,
    CoreResult,
    GenerateReport,
    LibrarySave,
    ProjectInput,
    profile_username,
)
from src.projects.service import ProjectService, ReportProcessingService
from src.projects.utils import (
    context_hash,
    context_values,
    library_view,
    validate_core_result,
)
from src.settings import BrokerSettings

SHA = "a" * 40
INPUT = ProjectInput(
    repo_id="123",
    github_username="octocat",
    declared_role="Разрабатывал API",
    started_at=date(2024, 1, 1),
)


def snapshot() -> ProjectSnapshot:
    fingerprint = context_hash(context_values(INPUT))
    return ProjectSnapshot(
        repo_id=INPUT.repo_id,
        github_username=INPUT.github_username,
        declared_role=INPUT.declared_role,
        started_at=INPUT.started_at,
        ended_at=None,
        context_hash=fingerprint,
        cached_sha=SHA,
        cached_context_hash=fingerprint,
        cached_report_text="# Непрозрачный отчёт\n{текст}",
        cached_summary_ru="Разрабатывал API",
    )


def preparation(item: ProjectSnapshot | None = None) -> PreparationJob:
    return PreparationJob(
        id=str(uuid4()),
        message_id=str(uuid4()),
        input_revision=0,
        input_snapshot=[item or snapshot()],
    )


def request() -> CoreRequest:
    return CoreRequest(
        message_id=uuid4(),
        job_id=uuid4(),
        input_revision=0,
        repositories=[
            CoreRepository(
                repo_id=INPUT.repo_id,
                repository_url="https://github.com/team/project",
                github_username=INPUT.github_username,
                declared_role=INPUT.declared_role,
                started_at=INPUT.started_at,
                ended_at=None,
                commit_sha=SHA,
                context_hash=snapshot().context_hash,
                source="github",
            )
        ],
    )


def result_for(payload: CoreRequest, text: str = "Итоговый отчёт") -> CoreResult:
    return CoreResult(
        type="github.report.completed",
        message_id=uuid4(),
        job_id=payload.job_id,
        report_text=text,
        summary_ru="Итог",
        repositories=[
            CoreRepositoryResult(
                repo_id=item.repo_id,
                commit_sha=item.commit_sha,
                context_hash=item.context_hash,
                report_text="# Отчёт\nПроизвольный текст без разбора",
                summary_ru="Работал над API",
            )
            for item in payload.repositories
        ],
    )


class ContractTests(unittest.TestCase):
    def test_urls_dates_and_unique_inputs(self) -> None:
        self.assertEqual(profile_username("https://github.com/octocat/"), "octocat")
        for url in (
            "http://github.com/user",
            "https://github.com.evil/user",
            "https://github.com/user/repo",
            "https://github.com/user?x=1",
        ):
            with self.assertRaises(ValueError):
                profile_username(url)
        self.assertIsNone(INPUT.ended_at)
        with self.assertRaises(ValidationError):
            replace(INPUT, ended_at=date(2023, 1, 1))
        with self.assertRaises(ValidationError):
            replace(INPUT, declared_role="  ")
        with self.assertRaises(ValidationError):
            TypeAdapter(ProjectInput).validate_json(
                b'{"repo_id":"123","github_username":"octocat","declared_role":"API"}'
            )
        with self.assertRaises(ValidationError):
            LibrarySave(projects=[INPUT, INPUT])
        with self.assertRaises(ValidationError):
            GenerateReport(repo_ids=["123", "123"], request_id=uuid4())
        with self.assertRaises(ValidationError):
            TypeAdapter(GenerateReport).validate_json(
                '{"repo_ids":["123"],"request_id":"' + str(uuid4()) + '","user_id":9}'
            )

    def test_case_normalization_and_message_size(self) -> None:
        context = ProjectContext(
            github_username="OctoCat",
            declared_role=None,
            started_at=None,
            ended_at=None,
        )
        self.assertEqual(
            context_hash(context),
            context_hash(replace(context, github_username="octocat")),
        )
        with self.assertRaises(ValueError):
            encode_payload(request(), 5)

    def test_failed_result_cannot_contain_success(self) -> None:
        with self.assertRaises(ValidationError):
            CoreResult(
                type="github.report.failed",
                message_id=uuid4(),
                job_id=uuid4(),
                error="Ошибка",
                report_text="Успех",
            )

    def test_transport_and_internal_records_are_dataclasses(self) -> None:
        for value in (
            INPUT,
            snapshot(),
            preparation(),
            request(),
            result_for(request()),
        ):
            self.assertTrue(is_dataclass(value))
        original = request()
        restored = TypeAdapter(CoreRequest).validate_json(
            encode_payload(original, 10000)
        )
        self.assertEqual(restored, original)
        self.assertIsInstance(restored.repositories[0], CoreRepository)

    def test_cache_fingerprint_keeps_existing_json_format(self) -> None:
        old_json = '{"declared_role": "Разрабатывал API", "ended_at": null, "github_username": "octocat", "started_at": "2024-01-01"}'
        self.assertEqual(
            context_hash(context_values(INPUT)),
            hashlib.sha256(old_json.encode()).hexdigest(),
        )

    def test_result_validation_checks_repository_set_and_context(self) -> None:
        payload = request()
        valid = result_for(payload)
        validate_core_result(payload, valid)
        mismatched = replace(
            valid, repositories=[replace(valid.repositories[0], repo_id="456")]
        )
        with self.assertRaises(InvalidCoreResult):
            validate_core_result(payload, mismatched)
        mismatched = replace(
            valid, repositories=[replace(valid.repositories[0], context_hash="b" * 64)]
        )
        with self.assertRaises(InvalidCoreResult):
            validate_core_result(payload, mismatched)


class ProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_requires_both_sha_and_context(self) -> None:
        github = SimpleNamespace(
            head=AsyncMock(
                return_value=RepositoryHead(
                    commit_sha=SHA,
                    repository_url="https://github.com/team/project",
                    default_branch="main",
                )
            )
        )
        processor = ReportProcessingService(github)
        original = snapshot()
        for item, expected in (
            (original, "cache"),
            (replace(original, cached_sha="b" * 40), "github"),
            (replace(original, cached_context_hash="c" * 64), "github"),
            (replace(original, cached_report_text=None), "github"),
        ):
            payload = await processor.prepare(preparation(item))
            repository = payload.repositories[0]
            self.assertEqual(repository.source, expected)
            self.assertEqual(repository.commit_sha, SHA)
            if expected == "cache":
                self.assertIsNotNone(repository.cached_report)
                self.assertEqual(
                    repository.cached_report.report_text, original.cached_report_text
                )
            else:
                self.assertIsNone(repository.cached_report)

    async def test_github_private_rejected_before_reading_commit(self) -> None:
        calls: list[str] = []
        remote = RemoteRepository(
            id=123,
            name="project",
            full_name="team/project",
            html_url="https://github.com/team/project",
            private=True,
        )

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(str(request.url))
            return httpx.Response(
                200, content=TypeAdapter(RemoteRepository).dump_json(remote)
            )

        with (
            patch(
                "src.projects.github_client.github.client",
                lambda: httpx.AsyncClient(
                    base_url="https://api.github.com",
                    transport=httpx.MockTransport(handler),
                ),
            ),
            self.assertRaises(HTTPException) as error,
        ):
            await GitHubClient().head("123")
        self.assertEqual(error.exception.status_code, 404)
        self.assertEqual(len(calls), 1)

    async def test_owner_filter_before_job_response_and_generation(self) -> None:
        service = ProjectService(AsyncMock())
        with patch(
            "src.projects.service.repo.job", AsyncMock(return_value=None)
        ) as query:
            with self.assertRaises(ReportJobNotFound):
                await service.get_job(7, "foreign")
            query.assert_awaited_once_with(service.db, "foreign", 7)
        with (
            patch("src.projects.service.repo.lock_state", AsyncMock()),
            patch(
                "src.projects.service.repo.find_request", AsyncMock(return_value=None)
            ),
            patch("src.projects.service.repo.projects", AsyncMock(return_value=[])),
            self.assertRaises(ProjectNotFound),
        ):
            await service.generate(
                7, GenerateReport(repo_ids=["123"], request_id=uuid4())
            )

    async def test_invalid_message_rejected_without_accessing_database(self) -> None:
        sessions = AsyncMock()
        worker = ReportWorker(session_factory=sessions)
        message = SimpleNamespace(
            body=b"invalid JSON", reject=AsyncMock(), ack=AsyncMock()
        )
        await worker.consume(message)
        message.reject.assert_awaited_once_with(requeue=False)
        message.ack.assert_not_awaited()
        sessions.assert_not_called()

    async def test_broker_publishes_persistent_identifiable_message(self) -> None:
        exchange = SimpleNamespace(
            publish=AsyncMock(),
            config=BrokerSettings(_env_file=None, ml_request_key="custom.request"),
        )
        payload = request()
        await ReportBroker(exchange).publish(payload)
        call = exchange.publish.call_args
        self.assertEqual(call.args[0].routing_key, "custom.request")
        self.assertEqual(call.args[0].event_type, "github.report.requested")
        self.assertEqual(call.args[0].correlation_id, str(payload.job_id))
        message = call.args[0]
        self.assertEqual(TypeAdapter(CoreRequest).validate_json(message.body), payload)
        self.assertEqual(message.message_id, str(payload.message_id))


class ReportAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AsyncMock()
        app.dependency_overrides[get_service] = lambda: self.service
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        app.dependency_overrides.clear()
        self.client.close()

    def test_thin_api_and_validation(self) -> None:
        self.service.save_library.return_value = []
        wire_input = TypeAdapter(LibrarySave).dump_json(LibrarySave(projects=[INPUT]))
        response = self.client.put(
            "/api/v1/github/library",
            content=wire_input,
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.service.save_library.call_args.args[0], 7)
        self.assertEqual(
            self.service.save_library.call_args.args[1].projects[0].started_at,
            date(2024, 1, 1),
        )
        self.service.generate.return_value = ReportJob(
            id=str(uuid4()),
            status="queued",
            repo_ids=["123"],
            attempts=0,
            error=None,
            created_at=datetime.now(UTC),
            repositories=[],
        )
        response = self.client.post(
            "/api/v1/github/report-jobs",
            content=TypeAdapter(GenerateReport).dump_json(
                GenerateReport(repo_ids=["123"], request_id=uuid4())
            ),
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 202, response.text)
        self.assertEqual(response.json()["status"], "queued")
        self.assertEqual(
            self.client.get(
                "/api/v1/github/repositories?profile_url=https://example.com/user"
            ).status_code,
            422,
        )
        self.service.discover.assert_not_called()
        self.assertEqual(
            self.client.delete("/api/v1/github/library/not-a-number").status_code, 422
        )
        self.service.remove.assert_not_called()

    def test_library_response_preserves_dataclass_fields(self) -> None:
        project = ImportedProject(
            user_id=7,
            repo_id="123",
            data={"id": "123", "name": "project", "description": "Описание"},
            github_username=INPUT.github_username,
            declared_role=INPUT.declared_role,
            started_at=INPUT.started_at,
        )
        self.service.library.return_value = [library_view(project)]
        response = self.client.get("/api/v1/github/library")
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()[0]
        self.assertEqual(item["declared_role"], INPUT.declared_role)
        self.assertEqual(item["started_at"], "2024-01-01")
        self.assertTrue(item["ready_for_analysis"])
        self.assertEqual(item["name"], "project")

    def test_dependency_error_and_authentication(self) -> None:
        self.service.discover.side_effect = HTTPException(503, "GitHub недоступен")
        self.assertEqual(
            self.client.get(
                "/api/v1/github/repositories?profile_url=https://github.com/octocat"
            ).status_code,
            503,
        )
        app.dependency_overrides.pop(get_current_user)
        for method, path in (
            ("GET", "/library"),
            ("PUT", "/library"),
            ("DELETE", "/library/123"),
            ("POST", "/report-jobs"),
            ("GET", "/report"),
            ("GET", f"/report-jobs/{uuid4()}"),
            ("GET", "/repositories?profile_url=https://github.com/octocat"),
        ):
            self.assertEqual(
                self.client.request(method, "/api/v1/github" + path).status_code, 401
            )
