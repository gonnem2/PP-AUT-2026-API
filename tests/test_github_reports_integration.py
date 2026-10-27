"""Только изолированная мигрированная PostgreSQL: RUN_REPORT_INTEGRATION=1."""

import os
import unittest
from dataclasses import is_dataclass, replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import aio_pika
from pydantic import TypeAdapter
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from test_github_reports import INPUT, SHA, result_for

from src.common.broker import RabbitMQService
from src.common.db.models import GitHubReportJob, ImportedProject, User
from src.common.worker import ReportWorker
from src.projects import repo
from src.projects.dto import (
    ClaimUpdate,
    CurrentReport,
    LibraryProject,
    RemoteRepository,
    ReportJob,
    RepositoryHead,
    RepositoryMetadata,
)
from src.projects.exception import ReportConflict, ReportJobNotFound
from src.projects.messaging import ReportBroker
from src.projects.schemas import (
    CoreRequest,
    CoreResult,
    GenerateReport,
    LibrarySave,
    ProjectInput,
)
from src.projects.service import ProjectService, ReportProcessingService
from src.settings import settings


class FakeGitHub:
    def __init__(self) -> None:
        self.sha = SHA

    async def repository(self, repo_id: str) -> RemoteRepository:
        return RemoteRepository(
            id=int(repo_id),
            name="project",
            full_name="team/project",
            html_url="https://github.com/team/project",
            private=False,
            description="Описание GitHub",
        )

    async def repositories(self, username: str) -> list[RepositoryMetadata]:
        return []

    async def head(self, repo_id: str) -> RepositoryHead:
        return RepositoryHead(
            commit_sha=self.sha,
            repository_url="https://github.com/team/project",
            default_branch="main",
        )


@unittest.skipUnless(
    os.getenv("RUN_REPORT_INTEGRATION") == "1", "Нужна изолированная PostgreSQL"
)
class ReportIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(settings.db_settings.dsn, poolclass=NullPool)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as db, db.begin():
            users = [
                User(
                    name="Тест",
                    email=f"{uuid4()}@example.com",
                    hashed_password="test-only",
                )
                for _ in range(2)
            ]
            db.add_all(users)
            await db.flush()
            self.user_id, self.other_id = [u.id for u in users]
        self.github = FakeGitHub()
        self.worker = ReportWorker(self.sessions, ReportProcessingService(self.github))
        self.broker = SimpleNamespace(publish=AsyncMock())
        await self.save([INPUT])

    async def asyncTearDown(self) -> None:
        async with self.sessions() as db, db.begin():
            await db.execute(
                delete(User).where(User.id.in_([self.user_id, self.other_id]))
            )
        await self.engine.dispose()

    async def save(self, projects: list[ProjectInput]) -> list[LibraryProject]:
        async with self.sessions() as db:
            return await ProjectService(db, self.github).save_library(
                self.user_id, LibrarySave(projects=projects)
            )

    async def generate(
        self, ids: list[str] | None = None, request_id: UUID | None = None
    ) -> ReportJob:
        async with self.sessions() as db:
            return await ProjectService(db, self.github).generate(
                self.user_id,
                GenerateReport(
                    repo_ids=ids or ["123"], request_id=request_id or uuid4()
                ),
            )

    async def current(self) -> CurrentReport:
        async with self.sessions() as db:
            return await ProjectService(db).current_report(self.user_id)

    async def message(self, event: CoreResult) -> SimpleNamespace:
        message = SimpleNamespace(
            body=TypeAdapter(CoreResult).dump_json(event),
            ack=AsyncMock(),
            nack=AsyncMock(),
            reject=AsyncMock(),
        )
        await self.worker.consume(message)
        return message

    async def test_roundtrip_cache_inputs_new_repositories_and_owner_isolation(
        self,
    ) -> None:
        request_id = uuid4()
        first = await self.generate(request_id=request_id)
        same = await self.generate(request_id=request_id)
        self.assertEqual(first.id, same.id)
        async with self.sessions() as db:
            with self.assertRaises(ReportJobNotFound):
                await ProjectService(db).get_job(self.other_id, first.id)
        await self.worker.dispatch_one(self.broker)
        payload = self.broker.publish.call_args.args[0]
        self.assertEqual(payload.repositories[0].source, "github")
        message = await self.message(result_for(payload))
        message.ack.assert_awaited_once()
        duplicate = await self.message(result_for(payload, "Запоздалый дубль"))
        duplicate.ack.assert_awaited_once()
        self.assertEqual((await self.current()).report_text, "Итоговый отчёт")
        self.assertTrue((await self.current()).is_current)
        async with self.sessions() as db:
            other = await ProjectService(db).library(self.other_id)
            self.assertEqual(other, [])
        await self.generate()
        self.assertFalse((await self.current()).is_current)
        self.assertEqual((await self.current()).report_text, "Итоговый отчёт")
        await self.worker.dispatch_one(self.broker)
        cached = self.broker.publish.call_args.args[0]
        self.assertEqual(cached.repositories[0].source, "cache")
        self.assertEqual(
            cached.repositories[0].cached_report.report_text,
            "# Отчёт\nПроизвольный текст без разбора",
        )
        await self.message(result_for(cached))
        await self.save([INPUT, replace(INPUT, repo_id="456")])
        with self.assertRaises(ReportConflict):
            await self.generate(ids=["123", "456"], request_id=request_id)
        await self.generate(ids=["123", "456"])
        await self.worker.dispatch_one(self.broker)
        mixed = self.broker.publish.call_args.args[0]
        self.assertEqual(
            [(r.repo_id, r.source) for r in mixed.repositories],
            [("123", "cache"), ("456", "github")],
        )
        await self.message(result_for(mixed))
        self.github.sha = "b" * 40
        await self.generate()
        await self.worker.dispatch_one(self.broker)
        self.assertEqual(
            self.broker.publish.call_args.args[0].repositories[0].source, "github"
        )
        await self.message(result_for(self.broker.publish.call_args.args[0]))
        await self.save([replace(INPUT, declared_role="Теперь отвечал за CI")])
        self.assertFalse((await self.current()).is_current)
        await self.generate()
        await self.worker.dispatch_one(self.broker)
        self.assertEqual(
            self.broker.publish.call_args.args[0].repositories[0].source, "github"
        )

    async def test_stale_results_bad_sha_and_failed_regeneration_preserve_report(
        self,
    ) -> None:
        await self.generate()
        await self.worker.dispatch_one(self.broker)
        first = self.broker.publish.call_args.args[0]
        await self.message(result_for(first, "Сохранённый отчёт"))
        await self.generate()
        await self.worker.dispatch_one(self.broker)
        stale = self.broker.publish.call_args.args[0]
        await self.save([replace(INPUT, declared_role="Другая роль")])
        await self.message(result_for(stale, "Устаревший отчёт"))
        self.assertEqual((await self.current()).report_text, "Сохранённый отчёт")
        self.assertEqual((await self.current()).latest_job.status, "superseded")
        await self.generate()
        await self.worker.dispatch_one(self.broker)
        payload = self.broker.publish.call_args.args[0]
        invalid = result_for(payload)
        invalid.repositories[0].commit_sha = "b" * 40
        message = await self.message(invalid)
        message.reject.assert_awaited_once_with(requeue=False)
        self.assertEqual((await self.current()).latest_job.status, "waiting")
        failure = CoreResult(
            type="github.report.failed",
            message_id=uuid4(),
            job_id=payload.job_id,
            error="ML недоступен",
        )
        await self.message(failure)
        self.assertEqual((await self.current()).report_text, "Сохранённый отчёт")
        self.assertFalse((await self.current()).is_current)

    async def test_publisher_retry_reuses_snapshot_and_expired_lease_recovers(
        self,
    ) -> None:
        job = await self.generate()
        self.broker.publish.side_effect = RuntimeError("Имитируем потерю подтверждения")
        await self.worker.dispatch_one(self.broker)
        original = self.broker.publish.call_args.args[0]
        self.github.sha = "b" * 40
        async with self.sessions() as db, db.begin():
            await db.execute(
                update(GitHubReportJob)
                .where(GitHubReportJob.id == job.id)
                .values(
                    status="preparing",
                    lease_token=str(uuid4()),
                    lease_until=datetime.now(UTC) - timedelta(seconds=1),
                    available_at=datetime.now(UTC),
                )
            )
        self.broker.publish.side_effect = None
        await self.worker.dispatch_one(self.broker)
        self.assertEqual(original, self.broker.publish.call_args.args[0])
        async with self.sessions() as db, db.begin():
            self.assertFalse(
                await repo.update_claim(
                    db, job.id, "lost-token", changes=ClaimUpdate(status="failed")
                )
            )
            await db.execute(
                update(GitHubReportJob)
                .where(GitHubReportJob.id == job.id)
                .values(deadline_at=datetime.now(UTC) - timedelta(seconds=1))
            )
        await self.worker.dispatch_one(self.broker)
        self.assertEqual((await self.current()).latest_job.status, "timed_out")
        late = await self.message(result_for(original))
        late.ack.assert_awaited_once()
        self.assertIsNone((await self.current()).report_text)

    async def test_reads_existing_json_snapshot_as_dataclass_without_migration(
        self,
    ) -> None:
        job = await self.generate()
        # Имитируем уже существующую JSON-запись, записанную прежней версией бэкенда.
        async with self.sessions() as db, db.begin():
            raw = await db.scalar(
                text(
                    "SELECT input_snapshot::text FROM github_report_jobs WHERE id = :job_id"
                ),
                {"job_id": job.id},
            )
            await db.execute(
                text(
                    "UPDATE github_report_jobs SET input_snapshot = CAST(:payload AS JSON) WHERE id = :job_id"
                ),
                {"payload": raw, "job_id": job.id},
            )
        async with self.sessions() as db:
            restored = await repo.job(db, job.id)
            self.assertTrue(is_dataclass(restored.input_snapshot[0]))
            self.assertEqual(restored.input_snapshot[0].started_at, INPUT.started_at)
        await self.worker.dispatch_one(self.broker)
        async with self.sessions() as db:
            restored = await repo.job(db, job.id)
            self.assertIsInstance(restored.request_payload, CoreRequest)
            self.assertEqual(
                restored.request_payload, self.broker.publish.call_args.args[0]
            )

    async def test_response_after_lost_confirm_and_before_retry_is_accepted(
        self,
    ) -> None:
        await self.generate()
        self.broker.publish.side_effect = RuntimeError("Потеря confirm")
        await self.worker.dispatch_one(self.broker)
        payload = self.broker.publish.call_args.args[0]
        message = await self.message(result_for(payload))
        message.ack.assert_awaited_once()
        self.assertTrue((await self.current()).is_current)
        self.assertEqual((await self.current()).latest_job.status, "completed")

    async def test_fast_result_and_deleting_selected_repository(self) -> None:
        await self.generate()

        async def fast_result(payload: CoreRequest) -> None:
            await self.message(result_for(payload))

        self.broker.publish.side_effect = fast_result
        await self.worker.dispatch_one(self.broker)
        self.assertEqual((await self.current()).latest_job.status, "completed")
        async with self.sessions() as db:
            await ProjectService(db).remove(self.user_id, "123")
        self.assertFalse((await self.current()).is_current)
        self.assertEqual((await self.current()).selected_repo_ids, [])
        async with self.sessions() as db:
            self.assertIsNone(
                await db.scalar(
                    select(ImportedProject).where(
                        ImportedProject.user_id == self.user_id
                    )
                )
            )

    @unittest.skipUnless(
        os.getenv("RUN_RABBIT_INTEGRATION") == "1", "Нужен отдельный RabbitMQ"
    )
    async def test_real_rabbit_roundtrip_with_fake_core(self) -> None:
        # Тест создаёт собственную топологию и удаляет только её.
        prefix = "resumehub.test." + uuid4().hex
        config = settings.reports.model_copy(
            update={
                "ml_exchange": prefix,
                "ml_request_queue": prefix + ".requests",
                "ml_result_queue": prefix + ".results",
                "ml_dead_letter_queue": prefix + ".invalid",
            }
        )
        async with RabbitMQService(config) as transport:
            queues = await transport.setup()
            try:
                await self.generate()
                await self.worker.dispatch_one(ReportBroker(transport))
                request = await queues.requests.get(timeout=10)
                assert request is not None
                payload = TypeAdapter(CoreRequest).validate_json(request.body)
                event = result_for(payload)
                # Имитируем независимый Core, публикующий ответ через собственное соединение.
                connection = await aio_pika.connect_robust(config.rabbitmq_url)
                async with connection:
                    channel = await connection.channel(
                        publisher_confirms=True, on_return_raises=True
                    )
                    exchange = await channel.get_exchange(config.ml_exchange)
                    await exchange.publish(
                        aio_pika.Message(
                            body=TypeAdapter(CoreResult).dump_json(event),
                            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                        ),
                        routing_key=config.ml_completed_key,
                        mandatory=True,
                        timeout=10,
                    )
                    await request.ack()
                    message = await queues.responses.get(timeout=10)
                    assert message is not None
                    await self.worker.consume(message)
                    self.assertTrue((await self.current()).is_current)
                    await exchange.publish(
                        aio_pika.Message(body=b"invalid"),
                        routing_key=config.ml_completed_key,
                        timeout=10,
                    )
                    message = await queues.responses.get(timeout=10)
                    assert message is not None
                    await self.worker.consume(message)
                    rejected = await queues.rejected.get(timeout=10)
                    assert rejected is not None
                    self.assertEqual(rejected.body, b"invalid")
                    await rejected.ack()
                    dead = await channel.get_exchange(config.ml_exchange + ".dlx")
                    await dead.delete()
                    await exchange.delete()
            finally:
                # При аварии удаляем только собственные тестовые очереди.
                for queue in (queues.requests, queues.responses, queues.rejected):
                    await queue.delete(if_unused=False, if_empty=False)
