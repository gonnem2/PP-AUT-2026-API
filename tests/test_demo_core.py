"""Заглушка Core: явный тестовый результат и надёжное подтверждение запросов."""

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from pydantic import TypeAdapter
from test_github_reports import preparation, request

from src.common.demo_core import consume_demo, demo_result
from src.common.worker import ReportWorker
from src.projects.dto import ClaimedJob
from src.projects.exception import EmptyGitHubRepository
from src.projects.schemas import CachedReport, CoreRequest, CoreResult
from src.projects.utils import validate_core_result
from src.settings import BrokerSettings


class DemoCoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_github_rate_limit_is_reported_without_exception_details(
        self,
    ) -> None:
        original = preparation()
        job = ClaimedJob(
            id=original.id,
            message_id=original.message_id,
            input_revision=original.input_revision,
            input_snapshot=original.input_snapshot,
            request_payload=None,
            lease_token="lease",
            attempts=1,
        )
        # Контекст транзакции нужен только для проверки переданного обновления.
        session = MagicMock()
        sessions = MagicMock()
        sessions.return_value.__aenter__.return_value = session
        worker = ReportWorker(session_factory=sessions)
        with patch(
            "src.common.worker.repo.update_claim", new_callable=AsyncMock
        ) as update:
            await worker._record_failure(
                job, HTTPException(503, "не выводить произвольный текст")
            )
        changes = update.call_args.kwargs["changes"]
        self.assertEqual(changes.status, "queued")
        self.assertIn("лимит", changes.error)
        self.assertNotIn("произвольный", changes.error)
        with patch(
            "src.common.worker.repo.update_claim", new_callable=AsyncMock
        ) as update:
            await worker._record_failure(job, EmptyGitHubRepository("team/empty"))
        changes = update.call_args.kwargs["changes"]
        self.assertEqual(changes.status, "failed")
        self.assertIn("team/empty", changes.error)

    def test_demo_roundtrip_preserves_cache_and_has_stable_message_id(self) -> None:
        original = request()
        cached = replace(
            original.repositories[0],
            source="cache",
            cached_report=CachedReport(
                report_text="Сохранённый текст", summary_ru="Сводка"
            ),
            declared_role=None,
            started_at=None,
        )
        payload = replace(original, repositories=[cached])
        restored = TypeAdapter(CoreRequest).validate_json(
            TypeAdapter(CoreRequest).dump_json(payload)
        )
        result = demo_result(restored)
        validate_core_result(restored, result)
        self.assertEqual(result.repositories[0].report_text, "Сохранённый текст")
        self.assertEqual(result.repositories[0].summary_ru, "Сводка")
        self.assertIn("заглушка Core", result.report_text)
        self.assertEqual(result.message_id, demo_result(restored).message_id)
        failed = demo_result(restored, fail=True)
        self.assertEqual(failed.type, "github.report.failed")
        self.assertEqual(failed.repositories, [])
        self.assertIsNone(failed.report_text)

    async def test_ack_after_publish_and_nack_after_transport_failure(self) -> None:
        message = SimpleNamespace(
            body=TypeAdapter(CoreRequest).dump_json(request()),
            ack=AsyncMock(),
            nack=AsyncMock(),
            reject=AsyncMock(),
        )

        async def publish(event: object) -> None:
            message.ack.assert_not_awaited()

        transport = SimpleNamespace(
            config=BrokerSettings(), publish=AsyncMock(side_effect=publish)
        )
        await consume_demo(message, transport, 0, False)
        message.ack.assert_awaited_once()
        result = TypeAdapter(CoreResult).validate_json(
            transport.publish.call_args.args[0].body
        )
        self.assertEqual(result.type, "github.report.completed")
        message.ack.reset_mock()
        transport.publish.side_effect = ConnectionError()
        await consume_demo(message, transport, 0, False)
        message.ack.assert_not_awaited()
        message.nack.assert_awaited_once_with(requeue=True)
        message.body = b"invalid"
        await consume_demo(message, transport, 0, False)
        message.reject.assert_awaited_once_with(requeue=False)
