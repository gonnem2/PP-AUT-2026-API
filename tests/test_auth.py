import unittest
from unittest.mock import AsyncMock, patch

import jwt
from fastapi.testclient import TestClient

from src.common.db.session import get_async_session
from src.common.redis.session import get_redis_session
from src.common.security import check_password, create_tokens, encode_password
from src.main import app
from src.settings import settings
from src.user.dto import UserDTO


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.user = UserDTO(
            1, "Test User", "test@example.com", encode_password("password"), True, True
        )
        self.sessions = {}
        self.redis = AsyncMock()

        async def save(key, value, ex):
            self.assertEqual(ex, settings.security.refresh_token_expire_minutes * 60)
            self.sessions[key] = value

        async def consume(key):
            return self.sessions.pop(key, None)

        self.redis.set.side_effect = save
        self.redis.getdel.side_effect = consume
        self.redis.delete.side_effect = consume
        self.db = AsyncMock()

        async def db_dependency():
            yield self.db

        async def redis_dependency():
            yield self.redis

        app.dependency_overrides[get_async_session] = db_dependency
        app.dependency_overrides[get_redis_session] = redis_dependency
        self.client = TestClient(app)
        self.email_patch = patch(
            "src.auth.service.user_repo.fetch_user_by_email",
            AsyncMock(return_value=self.user),
        )
        self.id_patch = patch(
            "src.auth.dependencies.user_repo.fetch_user_by_id",
            AsyncMock(return_value=self.user),
        )
        self.email_lookup = self.email_patch.start()
        self.id_lookup = self.id_patch.start()

    def tearDown(self):
        self.email_patch.stop()
        self.id_patch.stop()
        app.dependency_overrides.clear()
        self.client.close()

    def login(self):
        return self.client.post(
            "/api/v1/login", json={"email": self.user.email, "password": "password"}
        )

    def test_login_me_refresh_logout(self):
        response = self.login()
        self.assertEqual(response.status_code, 200)
        tokens = response.json()
        self.assertEqual(len(tokens["refresh_token"]), 32)
        response = self.client.get(
            "/api/v1/users/me",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("hashed_password", response.json())
        self.id_lookup.assert_awaited_once()
        response = self.client.post(
            "/api/v1/refresh", json={"refresh_token": tokens["refresh_token"]}
        )
        self.assertEqual(response.status_code, 200)
        refreshed = response.json()
        self.assertNotEqual(tokens["refresh_token"], refreshed["refresh_token"])
        self.assertEqual(
            self.client.post(
                "/api/v1/refresh", json={"refresh_token": tokens["refresh_token"]}
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/logout", json={"refresh_token": refreshed["refresh_token"]}
            ).status_code,
            204,
        )
        self.assertEqual(
            self.client.post(
                "/api/v1/refresh", json={"refresh_token": refreshed["refresh_token"]}
            ).status_code,
            401,
        )

    def test_bad_password_and_missing_user(self):
        self.assertFalse(check_password("wrong", self.user.hashed_password))
        response = self.client.post(
            "/api/v1/login", json={"email": self.user.email, "password": "wrong"}
        )
        self.assertEqual(response.status_code, 401)
        self.email_lookup.return_value = None
        self.assertEqual(self.login().status_code, 401)
        self.redis.set.assert_not_awaited()

    def test_invalid_access(self):
        self.assertEqual(self.client.get("/api/v1/users/me").status_code, 401)
        for payload in (
            {"sub": "1", "exp": 1},
            {"sub": "1"},
            {"sub": "bad", "exp": 9999999999},
        ):
            token = jwt.encode(
                payload,
                settings.security.secret_key,
                algorithm=settings.security.algorithm,
            )
            self.assertEqual(
                self.client.get(
                    "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}
                ).status_code,
                401,
            )
        self.assertEqual(
            self.client.get(
                "/api/v1/users/me", headers={"Authorization": "Bearer broken"}
            ).status_code,
            401,
        )
        self.id_lookup.assert_not_awaited()

    def test_deleted_or_inactive_user(self):
        token, _ = create_tokens(self.user.id)
        for user in (None, UserDTO(1, "Test", "test@example.com", "", False, True)):
            self.id_lookup.return_value = user
            self.assertEqual(
                self.client.get(
                    "/api/v1/users/me", headers={"Authorization": f"Bearer {token}"}
                ).status_code,
                401,
            )
            self.sessions["session:refresh"] = "1"
            self.assertEqual(
                self.client.post(
                    "/api/v1/refresh", json={"refresh_token": "refresh"}
                ).status_code,
                401,
            )
