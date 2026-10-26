import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from src.auth.dependencies import get_current_user
from src.common.db.session import get_async_session
from src.common.security import check_password, encode_password
from src.main import app
from src.user.dto import UserDTO


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.user = UserDTO(
            7, "Test", "test@example.com", encode_password("old-password"), True, True
        )
        self.db = AsyncMock()

        async def db():
            yield self.db

        app.dependency_overrides[get_async_session] = db
        app.dependency_overrides[get_current_user] = lambda: self.user
        self.client = TestClient(app)
        self.url = "/api/v1/users/me/profile"
        self.values = {
            "full_name": "Анна Иванова",
            "specialization": "Разработчик",
            "about": "О себе",
        }
        self.profile = SimpleNamespace(id=1, user_id=7, **self.values)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.client.close()

    def test_get_and_missing_profile(self):
        with patch(
            "src.user.repo.fetch_profile", AsyncMock(return_value=self.profile)
        ) as fetch:
            response = self.client.get(self.url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["full_name"], "Анна Иванова")
            fetch.assert_awaited_once_with(self.db, 7)
            fetch.return_value = None
            self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_fill_profile_and_partial_edit(self):
        with patch(
            "src.user.repo.save_profile", AsyncMock(return_value=self.profile)
        ) as save:
            self.assertEqual(
                self.client.put(self.url, json=self.values).status_code, 200
            )
            save.assert_awaited_once_with(self.db, 7, self.values)
        with patch(
            "src.user.repo.update_profile", AsyncMock(return_value=self.profile)
        ) as update:
            self.assertEqual(
                self.client.patch(self.url, json={"about": ""}).status_code, 200
            )
            update.assert_awaited_once_with(self.db, 7, {"about": ""})
            update.return_value = None
            self.assertEqual(
                self.client.patch(self.url, json={"about": "new"}).status_code, 404
            )

    def test_validation_and_ownership(self):
        for values in (
            {"about": None},
            {"full_name": "   "},
            {"about": "x" * 2001},
            {"user_id": 9},
        ):
            self.assertEqual(self.client.patch(self.url, json=values).status_code, 422)
        self.assertEqual(
            self.client.put(self.url, json={"about": "text"}).status_code, 422
        )
        app.dependency_overrides.pop(get_current_user)
        for method in (self.client.get, self.client.put, self.client.patch):
            self.assertEqual(method(self.url).status_code, 401)
        self.assertEqual(self.client.post("/api/v1/users/me/password").status_code, 401)

    def test_password_change(self):
        with patch(
            "src.user.repo.replace_password", AsyncMock(return_value=True)
        ) as replace:
            response = self.client.post(
                "/api/v1/users/me/password",
                json={
                    "current_password": "old-password",
                    "new_password": "new-password",
                },
            )
            self.assertEqual(response.status_code, 204)
            stored = replace.call_args.args[2]
            self.assertTrue(check_password("new-password", stored))
            self.assertFalse(check_password("old-password", stored))
            self.db.commit.assert_awaited_once()

    def test_rejected_password_changes(self):
        with patch("src.user.repo.replace_password", AsyncMock()) as replace:
            for current, new, status in (
                ("wrong", "new-password", 400),
                ("old-password", "old-password", 400),
                ("old-password", "short", 422),
            ):
                response = self.client.post(
                    "/api/v1/users/me/password",
                    json={"current_password": current, "new_password": new},
                )
                self.assertEqual(response.status_code, status)
            replace.assert_not_awaited()
            self.db.commit.assert_not_awaited()
