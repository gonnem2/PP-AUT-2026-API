"""HTTP откликов, частичное обновление и запрет доступа к чужим данным без сети."""

import unittest
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import TypeAdapter, ValidationError

from src.auth.dependencies import get_current_user
from src.main import app
from src.vacancies.router import get_service
from src.vacancies.schemas import VacancyCreate, VacancyPatch, VacancyView
from src.vacancies.service import VacancyService


def vacancy_view() -> VacancyView:
    return VacancyView(
        id=str(uuid4()),
        company="Компания",
        position="Разработчик",
        url="",
        description="Описание",
        notes="Заметка",
        status="saved",
        nextStepDate=date(2026, 11, 20),
        resumeId=None,
        resumeTitle=None,
        createdAt=datetime.now(UTC),
        updatedAt=datetime.now(UTC),
    )


class VacancyContractTests(unittest.TestCase):
    def test_patch_distinguishes_missing_clear_and_explicit_null(self) -> None:
        patch = TypeAdapter(VacancyPatch).validate_json('{"status":"sent"}')
        self.assertEqual(patch.provided_fields, {"status"})
        cleared = TypeAdapter(VacancyPatch).validate_json(
            '{"resumeId":null,"nextStepDate":null}'
        )
        self.assertEqual(cleared.provided_fields, {"resumeId", "nextStepDate"})
        self.assertIsNone(cleared.resumeId)
        for data in (
            {"company": None},
            {"position": "  "},
            {"notes": None},
            {"status": None},
            {"_provided_fields": ["resumeId"]},
        ):
            with self.assertRaises(ValidationError):
                TypeAdapter(VacancyPatch).validate_python(data)
        self.assertEqual(VacancyPatch(status="offer").provided_fields, {"status"})

    def test_input_validation_blocks_unsafe_urls_and_forged_owner_or_title(
        self,
    ) -> None:
        adapter = TypeAdapter(VacancyCreate)
        minimal = adapter.validate_python(
            {"company": " Компания ", "position": " Разработчик "}
        )
        self.assertEqual(minimal.company, "Компания")
        self.assertIsNone(minimal.resumeId)
        for values in (
            {"company": ""},
            {"position": " "},
            {"status": "unknown"},
            {"url": "javascript:alert(1)"},
            {"url": "file:///etc/passwd"},
            {"resumeId": "demo-1"},
            {"resumeTitle": "Чужое название"},
            {"user_id": 9},
            {"nextStepDate": "2026-02-30"},
            {"nextStepDate": "2100-01-01"},
            {"notes": "x" * 20001},
        ):
            with self.assertRaises(ValidationError):
                adapter.validate_python(
                    {"company": "Компания", "position": "Разработчик"} | values
                )


class VacancyServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_other_owner_is_checked_before_patch_or_delete(self) -> None:
        service = VacancyService(AsyncMock())
        vacancy_id = uuid4()
        with patch(
            "src.vacancies.service.repo.owned",
            new_callable=AsyncMock,
            return_value=None,
        ) as owned:
            with self.assertRaises(HTTPException) as caught:
                await service.update(7, vacancy_id, VacancyPatch(status="sent"))
            self.assertEqual(caught.exception.status_code, 404)
            owned.assert_awaited_once_with(service.db, 7, vacancy_id, lock=True)
            with self.assertRaises(HTTPException):
                await service.delete(7, vacancy_id)
            service.db.commit.assert_not_awaited()

    async def test_foreign_resume_is_checked_before_creating_record(self) -> None:
        service = VacancyService(AsyncMock())
        data = VacancyCreate(
            company="Компания", position="Разработчик", resumeId=uuid4()
        )
        with patch(
            "src.vacancies.service.repo.capture_resume",
            new_callable=AsyncMock,
            return_value=None,
        ) as capture:
            with self.assertRaises(HTTPException) as caught:
                await service.create(7, data)
            self.assertEqual(caught.exception.status_code, 404)
            capture.assert_awaited_once_with(service.db, 7, data.resumeId)
            service.db.commit.assert_not_awaited()


class VacancyAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = AsyncMock()
        app.dependency_overrides[get_service] = lambda: self.service
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=7)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        app.dependency_overrides.clear()

    def test_real_contract_and_partial_update(self) -> None:
        item = vacancy_view()
        self.service.list.return_value = []
        response = self.client.get("/api/v1/vacancies?status=sent")
        self.assertEqual(response.json(), [])
        self.service.list.assert_awaited_once_with(7, "sent")
        self.service.create.return_value = item
        response = self.client.post(
            "/api/v1/vacancies", json={"company": "Компания", "position": "Разработчик"}
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["resumeTitle"], None)
        self.assertEqual(response.json()["nextStepDate"], "2026-11-20")
        self.assertIn("updatedAt", response.json())
        self.service.update.return_value = item
        response = self.client.patch(
            f"/api/v1/vacancies/{item.id}", json={"status": "offer"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        args = self.service.update.call_args.args
        self.assertEqual(args[:2], (7, UUID(item.id)))
        self.assertEqual(args[2].provided_fields, {"status"})
        response = self.client.delete(f"/api/v1/vacancies/{item.id}")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")

    def test_validation_errors_and_dependency_failure(self) -> None:
        self.assertEqual(
            self.client.get("/api/v1/vacancies/not-a-uuid").status_code, 422
        )
        self.assertEqual(
            self.client.get("/api/v1/vacancies?status=unknown").status_code, 422
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/vacancies", json={"company": "", "position": "X"}
            ).status_code,
            422,
        )
        self.service.create.assert_not_awaited()
        self.service.list.side_effect = HTTPException(503, "Хранилище недоступно")
        response = self.client.get("/api/v1/vacancies")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "Хранилище недоступно")
        self.service.get.side_effect = HTTPException(404, "Вакансия не найдена")
        self.assertEqual(
            self.client.get(f"/api/v1/vacancies/{uuid4()}").status_code, 404
        )

    def test_every_route_requires_authentication(self) -> None:
        app.dependency_overrides.pop(get_current_user)
        vacancy_id = uuid4()
        for method, path in (
            ("GET", ""),
            ("POST", ""),
            ("GET", f"/{vacancy_id}"),
            ("PATCH", f"/{vacancy_id}"),
            ("DELETE", f"/{vacancy_id}"),
            ("GET", f"/{vacancy_id}/resume-snapshot"),
        ):
            self.assertEqual(
                self.client.request(method, "/api/v1/vacancies" + path).status_code, 401
            )
