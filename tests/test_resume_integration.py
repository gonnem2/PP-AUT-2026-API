"""Run against an isolated migrated PostgreSQL + Redis with RUN_INTEGRATION=1."""

import os
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from src.main import app


@unittest.skipUnless(
    os.getenv("RUN_INTEGRATION") == "1", "Requires isolated PostgreSQL and Redis"
)
class ResumeIntegrationTests(unittest.TestCase):
    def test_full_user_journey_and_ownership(self):
        with TestClient(app) as client:
            email = f"{uuid4().hex}@example.com"
            user = client.post(
                "/api/v1/users",
                json={
                    "name": "Анна Иванова",
                    "email": email,
                    "password": "test-password",
                },
            )
            self.assertEqual(user.status_code, 201, user.text)
            login = client.post(
                "/api/v1/login", json={"email": email, "password": "test-password"}
            )
            self.assertEqual(login.status_code, 200, login.text)
            tokens = login.json()
            headers = {"Authorization": f"Bearer {tokens['access_token']}"}
            self.assertEqual(
                client.get("/api/v1/users/me", headers=headers).json()["name"],
                "Анна Иванова",
            )
            profile = {
                "full_name": "Анна Иванова",
                "specialization": "",
                "about": "Разрабатываю API",
            }
            self.assertEqual(
                client.put(
                    "/api/v1/users/me/profile", json=profile, headers=headers
                ).status_code,
                200,
            )
            response = client.post(
                "/api/v1/resumes", json={"title": "Моё резюме"}, headers=headers
            )
            self.assertEqual(response.status_code, 201, response.text)
            resume = response.json()
            url = f"/api/v1/resumes/{resume['id']}"
            content = client.get(url + "/content", headers=headers).json()
            self.assertEqual(content["contacts"]["fullName"], profile["full_name"])
            self.assertEqual(content["about"], profile["about"])
            content["contacts"]["position"] = "Python разработчик"
            content["projects"] = [
                {
                    "id": "p1",
                    "name": "API",
                    "technologies": "Python",
                    "contribution": "Разработка",
                },
                {"id": "blank"},
            ]
            content["experience"] = [{"id": "e1", "company": "Компания"}]
            self.assertEqual(
                client.put(url + "/content", json=content, headers=headers).status_code,
                204,
            )
            stored = client.get(url, headers=headers).json()
            self.assertEqual(
                (
                    stored["projectsCount"],
                    stored["workplacesCount"],
                    stored["position"],
                ),
                (1, 1, "Python разработчик"),
            )
            self.assertEqual(
                client.patch(
                    url, json={"title": "Новое название"}, headers=headers
                ).status_code,
                200,
            )
            copy = client.post(url + "/duplicate", headers=headers).json()
            self.assertNotEqual(copy["id"], resume["id"])
            copy_url = f"/api/v1/resumes/{copy['id']}"
            copied = client.get(copy_url + "/content", headers=headers).json()
            self.assertEqual(copied["projects"][0]["name"], "API")
            content["projects"] = []
            client.put(url + "/content", json=content, headers=headers)
            self.assertEqual(
                client.get(copy_url, headers=headers).json()["projectsCount"], 1
            )
            self.assertEqual(client.get(url + "/pdf", headers=headers).status_code, 501)
            self.assertEqual(
                client.post(
                    "/api/v1/ai/improve-about", json={"text": "Опыт"}, headers=headers
                ).status_code,
                503,
            )
            self.assertEqual(
                client.put(
                    url + "/content", json={"user_id": 999}, headers=headers
                ).status_code,
                422,
            )
            self.assertEqual(
                client.post(
                    "/api/v1/resumes", json={"title": "  "}, headers=headers
                ).status_code,
                422,
            )
            # Different authenticated user cannot read, mutate, duplicate, or export another user's resume.
            other = f"{uuid4().hex}@example.com"
            client.post(
                "/api/v1/users",
                json={"name": "Другой", "email": other, "password": "test-password"},
            )
            token = client.post(
                "/api/v1/login", json={"email": other, "password": "test-password"}
            ).json()["access_token"]
            other_headers = {"Authorization": f"Bearer {token}"}
            for method, suffix, body in [
                ("GET", "", None),
                ("GET", "/content", None),
                ("PUT", "/content", content),
                ("PATCH", "", {"title": "Чужое"}),
                ("DELETE", "", None),
                ("POST", "/duplicate", None),
                ("GET", "/pdf", None),
            ]:
                self.assertEqual(
                    client.request(
                        method, url + suffix, json=body, headers=other_headers
                    ).status_code,
                    404,
                )
            self.assertEqual(
                client.get("/api/v1/resumes", headers=other_headers).json(), []
            )
            self.assertEqual(client.get(url).status_code, 401)
            remote = {
                "id": 123,
                "name": "project",
                "description": None,
                "language": "Python",
                "topics": ["api"],
                "private": False,
            }
            with patch("src.resume.github.get", AsyncMock(return_value=[remote])):
                repos = client.get(
                    "/api/v1/github/repos?username=octocat", headers=headers
                )
                self.assertEqual(repos.json()[0]["tags"], ["Python", "api"])
            with patch("src.resume.github.get", AsyncMock(return_value=remote)):
                for _ in range(2):
                    self.assertEqual(
                        client.post(
                            "/api/v1/projects/import",
                            json={"repoIds": ["123"]},
                            headers=headers,
                        ).status_code,
                        204,
                    )
            self.assertEqual(
                len(client.get("/api/v1/projects", headers=headers).json()), 1
            )
            self.assertEqual(
                client.get("/api/v1/projects", headers=other_headers).json(), []
            )
            refreshed = client.post(
                "/api/v1/refresh", json={"refresh_token": tokens["refresh_token"]}
            )
            self.assertEqual(refreshed.status_code, 200)
            self.assertEqual(
                client.post(
                    "/api/v1/refresh", json={"refresh_token": tokens["refresh_token"]}
                ).status_code,
                401,
            )
            refresh_token = refreshed.json()["refresh_token"]
            self.assertEqual(
                client.post(
                    "/api/v1/logout", json={"refresh_token": refresh_token}
                ).status_code,
                204,
            )
            self.assertEqual(
                client.post(
                    "/api/v1/refresh", json={"refresh_token": refresh_token}
                ).status_code,
                401,
            )
            self.assertEqual(client.delete(url, headers=headers).status_code, 204)
            self.assertEqual(client.get(url, headers=headers).status_code, 404)
            self.assertEqual(client.delete(copy_url, headers=headers).status_code, 204)
