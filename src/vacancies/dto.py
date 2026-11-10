"""Внутренний снимок; содержимое резюме непрозрачно за пределами JSON-границы."""

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class ResumeSnapshot:
    resume_id: str
    title: str
    content_json: str
