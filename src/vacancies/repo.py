"""SQL откликов и сериализация неизменяемых снимков резюме."""

import json
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.db.models import Resume, Vacancy
from src.vacancies.dto import ResumeSnapshot
from src.vacancies.schemas import (
    VacancyCreate,
    VacancyPatch,
    VacancyStatus,
    VacancyView,
)


async def list_vacancies(
    db: AsyncSession, user_id: int, status: VacancyStatus | None
) -> list[Vacancy]:
    query = select(Vacancy).where(Vacancy.user_id == user_id)
    if status is not None:
        query = query.where(Vacancy.status == status)
    return list(
        (
            await db.scalars(
                query.order_by(Vacancy.updated_at.desc(), Vacancy.id.desc())
            )
        ).all()
    )


async def owned(
    db: AsyncSession, user_id: int, vacancy_id: UUID, *, lock: bool = False
) -> Vacancy | None:
    query = select(Vacancy).where(
        Vacancy.user_id == user_id, Vacancy.id == str(vacancy_id)
    )
    if lock:
        query = query.with_for_update()
    return await db.scalar(query)


async def capture_resume(
    db: AsyncSession, user_id: int, resume_id: UUID
) -> ResumeSnapshot | None:
    row = await db.scalar(
        select(Resume)
        .where(Resume.user_id == user_id, Resume.id == str(resume_id))
        .with_for_update(read=True)
    )
    if row is None:
        return None
    return ResumeSnapshot(
        resume_id=row.id,
        title=row.title,
        content_json=json.dumps(row.content, ensure_ascii=False),
    )


def set_snapshot(row: Vacancy, snapshot: ResumeSnapshot | None) -> None:
    row.resume_id = snapshot.resume_id if snapshot else None
    row.resume_title = snapshot.title if snapshot else None
    row.resume_snapshot = snapshot.content_json if snapshot else None


def saved_snapshot(row: Vacancy) -> ResumeSnapshot | None:
    if row.resume_id is None or row.resume_title is None or row.resume_snapshot is None:
        return None
    return ResumeSnapshot(
        resume_id=row.resume_id,
        title=row.resume_title,
        content_json=row.resume_snapshot,
    )


async def create(
    db: AsyncSession, user_id: int, data: VacancyCreate, snapshot: ResumeSnapshot | None
) -> Vacancy:
    row = Vacancy(
        id=str(uuid4()),
        user_id=user_id,
        company=data.company,
        position=data.position,
        url=data.url,
        description=data.description,
        notes=data.notes,
        status=data.status,
        next_step_date=data.nextStepDate,
    )
    set_snapshot(row, snapshot)
    db.add(row)
    await db.flush()
    return row


def update(row: Vacancy, data: VacancyPatch, snapshot: ResumeSnapshot | None) -> None:
    # Это отображение полей HTTP в SQL, а не словарь между слоями.
    columns = {
        "company": "company",
        "position": "position",
        "url": "url",
        "description": "description",
        "notes": "notes",
        "status": "status",
        "nextStepDate": "next_step_date",
    }
    for name in data.provided_fields:
        if name in columns:
            setattr(row, columns[name], getattr(data, name))
    if (
        "resumeId" in data.provided_fields
        and (str(data.resumeId) if data.resumeId else None) != row.resume_id
    ):
        set_snapshot(row, snapshot)


async def remove(db: AsyncSession, row: Vacancy) -> None:
    await db.delete(row)


def view(row: Vacancy) -> VacancyView:
    return VacancyView(
        id=row.id,
        company=row.company,
        position=row.position,
        url=row.url,
        description=row.description,
        notes=row.notes,
        status=row.status,
        nextStepDate=row.next_step_date,
        resumeId=row.resume_id,
        resumeTitle=row.resume_title,
        createdAt=row.created_at,
        updatedAt=row.updated_at,
    )
