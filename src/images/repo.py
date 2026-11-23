"""Доступ к галерее и отметки для удаления объектов без сетевых вызовов."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.common.db.models import Resume, ResumeImage, ResumeImageCleanup, ResumeImageJob


async def owned_resume(
    db: AsyncSession, user_id: int, resume_id: UUID, *, lock: bool = False
) -> Resume:
    statement = select(Resume).where(
        Resume.id == str(resume_id), Resume.user_id == user_id
    )
    if lock:
        statement = statement.with_for_update().execution_options(
            populate_existing=True
        )
    row = await db.scalar(statement)
    if row is None:
        raise HTTPException(404, "Резюме не найдено")
    return row


async def image(db: AsyncSession, resume_id: str, image_id: UUID | str) -> ResumeImage:
    row = await db.scalar(
        select(ResumeImage).where(
            ResumeImage.id == str(image_id), ResumeImage.resume_id == resume_id
        )
    )
    if row is None:
        raise HTTPException(404, "Изображение не найдено")
    return row


async def gallery(db: AsyncSession, resume_id: str) -> list[ResumeImage]:
    rows = await db.scalars(
        select(ResumeImage)
        .where(ResumeImage.resume_id == resume_id)
        .order_by(ResumeImage.created_at, ResumeImage.id)
    )
    return list(rows)


async def gallery_status(
    db: AsyncSession, user_id: int, resume_id: UUID
) -> tuple[str | None, ResumeImageJob | None, list[ResumeImage]]:
    # Один снимок для аватара, задания и картинок. Промежуточный результат
    # нескольких SELECT мог бы преждевременно остановить polling на фронте.
    rows = (
        await db.execute(
            select(Resume.avatar_image_id, ResumeImageJob, ResumeImage)
            .outerjoin(ResumeImageJob, ResumeImageJob.id == Resume.avatar_request_id)
            .outerjoin(ResumeImage, ResumeImage.resume_id == Resume.id)
            .where(Resume.id == str(resume_id), Resume.user_id == user_id)
            .order_by(ResumeImage.created_at, ResumeImage.id)
        )
    ).all()
    if not rows:
        raise HTTPException(404, "Резюме не найдено")
    avatar_id, pending, _ = rows[0]
    return avatar_id, pending, [row for _, _, row in rows if row is not None]


async def pending_avatar(db: AsyncSession, resume: Resume) -> ResumeImageJob | None:
    if resume.avatar_request_id is None:
        return None
    return await db.get(ResumeImageJob, resume.avatar_request_id)


def schedule_cleanup(
    db: AsyncSession, keys: list[str | None], *, staged: bool = False, delay: int = 0
) -> ResumeImageCleanup | None:
    existing = [key for key in keys if key]
    if not existing:
        return None
    row = ResumeImageCleanup(
        id=str(uuid4()),
        staged=staged,
        keys=existing,
        available_at=datetime.now(UTC) + timedelta(seconds=delay),
    )
    db.add(row)
    return row


def schedule_image_cleanup(db: AsyncSession, row: ResumeImage) -> None:
    schedule_cleanup(db, [row.staging_key], staged=True)
    schedule_cleanup(db, [row.full_key, row.preview_key, row.avatar_key])


async def current_avatar_key(db: AsyncSession, resume: Resume) -> str | None:
    if resume.avatar_image_id is None:
        return None
    return await db.scalar(
        select(ResumeImage.avatar_key).where(
            ResumeImage.id == resume.avatar_image_id,
            ResumeImage.resume_id == resume.id,
            ResumeImage.status == "ready",
        )
    )
