"""Регрессии перехода на ML v2 без зависимости от версии ML-репозитория."""

import unittest
from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

from pydantic import TypeAdapter, ValidationError
from test_github_reports import request

from src.projects.exception import InvalidCoreResult
from src.projects.messaging import encode_payload
from src.projects.ml_protocol import (
    CoreResultV2,
    MixedGitHubAccounts,
    MLRepositoryResult,
    MLResume,
    MLResumeItem,
    WireRequest,
    WireResult,
    report_from_v2,
    request_v2,
)
from src.settings import ReportWorkerSettings


class ReportWorkerSettingsTests(unittest.TestCase):
    def test_protocol_version_is_parsed_from_compose_environment(self) -> None:
        for version in ("1", "2"):
            with (
                self.subTest(version=version),
                patch.dict("os.environ", {"REPORT_PROTOCOL_VERSION": version}),
            ):
                self.assertEqual(
                    ReportWorkerSettings().report_protocol_version, int(version)
                )
        with (
            patch.dict("os.environ", {"REPORT_PROTOCOL_VERSION": "3"}),
            self.assertRaises(ValidationError),
        ):
            ReportWorkerSettings()


class MLProtocolV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        previous = request()
        another = replace(
            previous.repositories[0],
            repo_id="876",
            repository_url="https://github.com/team/other",
        )
        self.original = replace(
            previous, repositories=[*previous.repositories, another]
        )
        self.outgoing = request_v2(self.original)
        self.result = CoreResultV2(
            type="github.report.completed",
            message_id=uuid4(),
            request_message_id=self.outgoing.message_id,
            job_id=self.outgoing.job_id,
            input_revision=self.outgoing.input_revision,
            status="partial",
            repositories=[
                MLRepositoryResult(
                    repo_id=item.repo_id,
                    repository_url=item.repository_url,
                    commit_sha=item.commit_sha,
                    context_hash=item.context_hash,
                    status="completed" if i == 0 else "failed",
                    analysis={
                        "commit_sha": item.commit_sha,
                        "project_summary": "Описание API",
                    }
                    if i == 0
                    else None,
                )
                for i, item in enumerate(self.outgoing.repositories)
            ],
            profile={"claims": [{"id": "claim-1", "evidence_ids": ["source-1"]}]},
            resume=MLResume(
                candidate_name="Test",
                generated_at=datetime.now(UTC),
                summary=MLResumeItem(text="Опыт разработки", claim_ids=["claim-1"]),
            ),
        )

    def test_wire_preserves_both_versions(self) -> None:
        for payload in (self.outgoing, self.original):
            data = encode_payload(payload, 4000000)
            self.assertEqual(TypeAdapter(WireRequest).validate_json(data), payload)
        self.assertNotIn(b'"cached_report"', encode_payload(self.outgoing, 4000000))

    def test_partial_keeps_only_usable_project_reports(self) -> None:
        data = TypeAdapter(WireResult).dump_json(self.result)
        self.assertEqual(TypeAdapter(WireResult).validate_json(data), self.result)
        report = report_from_v2(self.outgoing, self.result)
        self.assertEqual(report.summary_ru, "Опыт разработки")
        self.assertEqual(
            [p.repo_id for p in report.repositories],
            [self.outgoing.repositories[0].repo_id],
        )
        self.assertIn("Проекты без результата: team/other", report.report_text)

    def test_failure_still_requires_matching_request_and_repositories(self) -> None:
        failure = replace(
            self.result,
            type="github.report.failed",
            status="failed",
            resume=None,
            profile=None,
            error="LLM недоступна",
        )
        self.assertEqual(report_from_v2(self.outgoing, failure).error, "LLM недоступна")
        for invalid in (
            replace(failure, request_message_id=uuid4()),
            replace(failure, input_revision=99),
            replace(failure, repositories=failure.repositories[:1]),
            replace(
                failure,
                repositories=[
                    replace(failure.repositories[0], context_hash="f" * 64),
                    failure.repositories[1],
                ],
            ),
        ):
            with self.assertRaises(InvalidCoreResult):
                report_from_v2(self.outgoing, invalid)

    def test_mixed_accounts_are_rejected(self) -> None:
        self.original.repositories[1] = replace(
            self.original.repositories[1], github_username="other"
        )
        with self.assertRaises(MixedGitHubAccounts):
            request_v2(self.original)
