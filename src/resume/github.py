import httpx
from fastapi import HTTPException

from src.settings import settings


def repository(data: dict) -> dict:
    return {
        "id": str(data["id"]),
        "name": data["name"],
        "description": data.get("description") or "",
        "tags": list(
            dict.fromkeys(
                ([data["language"]] if data.get("language") else [])
                + data.get("topics", [])
            )
        ),
    }


def client() -> httpx.AsyncClient:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ResumeHub"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return httpx.AsyncClient(
        base_url="https://api.github.com", headers=headers, timeout=15
    )


async def get(client: httpx.AsyncClient, path: str, **params):
    try:
        response = await client.get(path, params=params)
    except httpx.RequestError:
        raise HTTPException(502, "GitHub временно недоступен") from None
    if response.status_code == 404:
        raise HTTPException(404, "Пользователь или репозиторий GitHub не найден")
    if response.status_code in (403, 429):
        raise HTTPException(503, "Лимит запросов GitHub исчерпан. Попробуйте позже.")
    if response.is_error:
        raise HTTPException(502, "Не удалось получить данные GitHub")
    return response.json()
