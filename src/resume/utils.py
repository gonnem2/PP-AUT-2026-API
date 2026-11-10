"""Чистые проверки версии контента резюме на JSON-границе."""

import hashlib
import json

from src.resume.schemas import Content


def content_etag(content: Content) -> str:
    serialized = json.dumps(
        content.model_dump(mode="json"),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return '"' + hashlib.sha256(serialized.encode()).hexdigest() + '"'
