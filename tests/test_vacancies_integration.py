"""Отклики проверяются только на отдельной мигрированной PostgreSQL."""

import os
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from src.common.db.models import User
from src.main import app
from src.settings import settings


@unittest.skipUnless(
    os.getenv("RUN_INTEGRATION") == "1", "Нужны отдельные PostgreSQL и Redis"
)
class VacancyIntegrationTests(unittest.TestCase):
    def test_persistence_ownership_snapshot_and_partial_update(self) -> None:
        user_ids: list[int] = []
        refresh_tokens: list[str] = []
        try:
            with TestClient(app) as client:
                headers = []
                for _ in range(2):
                    email = f"vacancy-{uuid4().hex}@example.com"
                    created = client.post(
                        "/api/v1/users",
                        json={
                            "name": "Тест откликов",
                            "email": email,
                            "password": "test-only-password",
                        },
                    )
                    self.assertEqual(created.status_code, 201, created.text)
                    user_ids.append(created.json()["id"])
                    login = client.post(
                        "/api/v1/login",
                        json={"email": email, "password": "test-only-password"},
                    )
                    self.assertEqual(login.status_code, 200, login.text)
                    tokens = login.json()
                    refresh_tokens.append(tokens["refresh_token"])
                    headers.append(
                        {"Authorization": f"Bearer {tokens['access_token']}"}
                    )
                owner, other = headers
                self.assertEqual(
                    client.get("/api/v1/vacancies", headers=owner).json(), []
                )
                resume = client.post(
                    "/api/v1/resumes",
                    json={"title": "Отправленная версия"},
                    headers=owner,
                ).json()
                foreign_resume = client.post(
                    "/api/v1/resumes", json={"title": "Чужое резюме"}, headers=other
                ).json()
                resume_path = f"/api/v1/resumes/{resume['id']}"
                content = client.get(resume_path + "/content", headers=owner).json()
                content["about"] = "Исходный опыт"
                self.assertEqual(
                    client.put(
                        resume_path + "/content", json=content, headers=owner
                    ).status_code,
                    204,
                )
                payload = {
                    "company": "Компания",
                    "position": "Python-разработчик",
                    "url": "https://example.com/job",
                    "description": "Реальная вакансия",
                    "notes": "Позвонить",
                    "nextStepDate": "2026-11-20",
                    "status": "sent",
                    "resumeId": resume["id"],
                }
                response = client.post("/api/v1/vacancies", json=payload, headers=owner)
                self.assertEqual(response.status_code, 201, response.text)
                vacancy = response.json()
                path = f"/api/v1/vacancies/{vacancy['id']}"
                snapshot_path = path + "/resume-snapshot"
                self.assertEqual(vacancy["resumeTitle"], "Отправленная версия")
                # Новый HTTP-запрос читает запись из другой транзакции.
                self.assertEqual(
                    client.get(path, headers=owner).json()["notes"], "Позвонить"
                )
                self.assertEqual(
                    client.get("/api/v1/vacancies", headers=other).json(), []
                )
                for method, suffix, data in (
                    ("GET", "", None),
                    ("PATCH", "", {"status": "offer"}),
                    ("DELETE", "", None),
                    ("GET", "/resume-snapshot", None),
                ):
                    self.assertEqual(
                        client.request(
                            method, path + suffix, headers=other, json=data
                        ).status_code,
                        404,
                    )
                invalid = client.post(
                    "/api/v1/vacancies",
                    json=payload | {"resumeId": foreign_resume["id"]},
                    headers=owner,
                )
                self.assertEqual(invalid.status_code, 404)
                self.assertEqual(
                    client.patch(
                        path, json={"resumeId": foreign_resume["id"]}, headers=owner
                    ).status_code,
                    404,
                )
                self.assertEqual(
                    client.get(path, headers=owner).json()["resumeId"], resume["id"]
                )
                content["about"] = "Изменённый опыт"
                client.put(resume_path + "/content", json=content, headers=owner)
                client.patch(
                    resume_path, json={"title": "Новое название"}, headers=owner
                )
                snapshot = client.get(snapshot_path, headers=owner).json()
                self.assertEqual(snapshot["title"], "Отправленная версия")
                self.assertEqual(snapshot["content"]["about"], "Исходный опыт")
                self.assertEqual(
                    client.delete(resume_path, headers=owner).status_code, 204
                )
                updated = client.patch(
                    path, json={"status": "interview"}, headers=owner
                )
                self.assertEqual(updated.status_code, 200, updated.text)
                self.assertEqual(updated.json()["notes"], "Позвонить")
                self.assertEqual(updated.json()["nextStepDate"], "2026-11-20")
                self.assertEqual(
                    client.get(snapshot_path, headers=owner).json(), snapshot
                )
                self.assertEqual(
                    client.patch(
                        path,
                        json={"resumeId": resume["id"], "notes": "Снимок сохранён"},
                        headers=owner,
                    ).status_code,
                    200,
                )
                self.assertEqual(
                    client.get(snapshot_path, headers=owner).json(), snapshot
                )
                self.assertEqual(
                    len(
                        client.get(
                            "/api/v1/vacancies?status=interview", headers=owner
                        ).json()
                    ),
                    1,
                )
                self.assertEqual(
                    client.get("/api/v1/vacancies?status=sent", headers=owner).json(),
                    [],
                )
                cleared = client.patch(
                    path, json={"resumeId": None, "nextStepDate": None}, headers=owner
                ).json()
                self.assertIsNone(cleared["resumeId"])
                self.assertIsNone(cleared["resumeTitle"])
                self.assertIsNone(cleared["nextStepDate"])
                self.assertEqual(
                    client.get(snapshot_path, headers=owner).status_code, 404
                )
                self.assertEqual(client.delete(path, headers=owner).status_code, 204)
                self.assertEqual(client.get(path, headers=owner).status_code, 404)
                self.assertEqual(
                    client.get("/api/v1/vacancies", headers=owner).json(), []
                )
                for token in refresh_tokens:
                    client.post("/api/v1/logout", json={"refresh_token": token})
        finally:
            import asyncio

            async def cleanup() -> None:
                engine = create_async_engine(
                    settings.db_settings.dsn, poolclass=NullPool
                )
                try:
                    async with engine.begin() as db:
                        await db.execute(delete(User).where(User.id.in_(user_ids)))
                finally:
                    await engine.dispose()

            asyncio.run(cleanup())
