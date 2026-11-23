"""Проверка живого API: пользователь, вход и сохранение резюме, без GitHub/ML."""

import argparse
import json
import secrets
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4


class SmokeFailure(RuntimeError):
    pass


def request(
    base: str,
    method: str,
    path: str,
    expected: int,
    payload: dict | None = None,
    token: str | None = None,
) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(
        base + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers=headers,
        method=method,
    )
    try:
        with urlopen(req, timeout=10) as response:
            if response.status != expected:
                raise SmokeFailure(
                    f"{method} {path}: неожиданный статус {response.status}"
                )
            body = response.read()
            return json.loads(body) if body else {}
    except HTTPError as exc:
        # Тела ответов и токены не выводим в журнал.
        raise SmokeFailure(
            f"{method} {path}: HTTP {exc.code}, ожидался {expected}"
        ) from None


def run(base: str) -> None:
    for attempt in range(30):
        try:
            if request(base, "GET", "/health", 200) != {"status": "ok"}:
                raise SmokeFailure("Некорректный ответ /health")
            break
        except (URLError, TimeoutError):
            if attempt == 29:
                raise SmokeFailure("API не стал доступен") from None
            time.sleep(1)
    print("1/3: API отвечает; проверяем PostgreSQL и Redis через основной сценарий.")
    email = f"lab1-{uuid4().hex}@example.com"
    password = secrets.token_urlsafe(24)
    request(
        base,
        "POST",
        "/api/v1/users",
        201,
        {"name": "ЛР1", "email": email, "password": password},
    )
    tokens = request(
        base, "POST", "/api/v1/login", 200, {"email": email, "password": password}
    )
    token = tokens["access_token"]
    print("2/3: регистрация и вход выполнены.")
    resume_id: str | None = None
    try:
        resume = request(
            base, "POST", "/api/v1/resumes", 201, {"title": "ЛР1 — проверка"}, token
        )
        resume_id = resume["id"]
        path = f"/api/v1/resumes/{resume_id}/content"
        request(base, "PUT", path, 204, {"about": "Проверка сохранения резюме"}, token)
        stored = request(base, "GET", path, 200, token=token)
        if stored["about"] != "Проверка сохранения резюме":
            raise SmokeFailure("Сохранённый текст не совпал")
        print("3/3: резюме создано, текст сохранён и прочитан. Проверка пройдена.")
    finally:
        if resume_id:
            request(base, "DELETE", f"/api/v1/resumes/{resume_id}", 204, token=token)
        request(
            base,
            "POST",
            "/api/v1/logout",
            204,
            {"refresh_token": tokens["refresh_token"]},
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    try:
        run(args.base_url.rstrip("/"))
    except (SmokeFailure, URLError, TimeoutError, KeyError, ValueError) as exc:
        print(f"Проверка не пройдена: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
