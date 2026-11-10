"""Сценарии откликов: изоляция владельцев и фиксация выбранного резюме."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.db.models import Vacancy
from src.vacancies import repo
from src.vacancies.dto import ResumeSnapshot
from src.vacancies.schemas import (
    VacancyCreate,
    VacancyPatch,
    VacancyStatus,
    VacancyView,
)


class VacancyService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list(
        self, user_id: int, status: VacancyStatus | None = None
    ) -> list[VacancyView]:
        return [
            repo.view(row)
            for row in await repo.list_vacancies(self.db, user_id, status)
        ]

    async def _owned(
        self, user_id: int, vacancy_id: UUID, *, lock: bool = False
    ) -> Vacancy:
        row = await repo.owned(self.db, user_id, vacancy_id, lock=lock)
        if row is None:
            raise HTTPException(404, "Вакансия не найдена")
        return row

    async def _capture(
        self, user_id: int, resume_id: UUID | None
    ) -> ResumeSnapshot | None:
        if resume_id is None:
            return None
        snapshot = await repo.capture_resume(self.db, user_id, resume_id)
        if snapshot is None:
            raise HTTPException(404, "Резюме не найдено")
        return snapshot

    async def get(self, user_id: int, vacancy_id: UUID) -> VacancyView:
        return repo.view(await self._owned(user_id, vacancy_id))

    async def create(self, user_id: int, data: VacancyCreate) -> VacancyView:
        snapshot = await self._capture(user_id, data.resumeId)
        row = await repo.create(self.db, user_id, data, snapshot)
        await self.db.commit()
        await self.db.refresh(row)
        return repo.view(row)

    async def update(
        self, user_id: int, vacancy_id: UUID, data: VacancyPatch
    ) -> VacancyView:
        row = await self._owned(user_id, vacancy_id, lock=True)
        snapshot = None
        if (
            "resumeId" in data.provided_fields
            and data.resumeId is not None
            and str(data.resumeId) != row.resume_id
        ):
            snapshot = await self._capture(user_id, data.resumeId)
        repo.update(row, data, snapshot)
        await self.db.commit()
        await self.db.refresh(row)
        return repo.view(row)

    async def delete(self, user_id: int, vacancy_id: UUID) -> None:
        await repo.remove(self.db, await self._owned(user_id, vacancy_id, lock=True))
        await self.db.commit()

    async def snapshot(self, user_id: int, vacancy_id: UUID) -> ResumeSnapshot:
        snapshot = repo.saved_snapshot(await self._owned(user_id, vacancy_id))
        if snapshot is None:
            raise HTTPException(404, "Снимок резюме не прикреплён")
        return snapshot
