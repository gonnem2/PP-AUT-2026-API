"""Отправка outbox, обработка изображений и удаление объектов S3."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from aio_pika.abc import AbstractIncomingMessage
from pydantic import BaseModel, ValidationError
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.common.broker import BrokerMessage, RabbitMQService
from src.common.db.models import Resume, ResumeImage, ResumeImageCleanup, ResumeImageJob
from src.common.db.session import Session, async_engine
from src.images import repo
from src.images.processing import (
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    InvalidImage,
    crop_avatar,
    process_upload,
)
from src.images.storage import (
    ImageStorage,
    ObjectMissing,
    StorageUnavailable,
    get_image_storage,
)
from src.settings import settings

logger = logging.getLogger(__name__)
LEASE = timedelta(minutes=3)
MAX_ATTEMPTS = 5


class WorkMessage(BaseModel):
    job_id: UUID


@dataclass(frozen=True)
class Work:
    job_id: str
    resume_id: str
    image_id: str
    token: str
    input_key: str
    upload: bool
    make_avatar: bool
    cleanup_id: str
    full_key: str | None
    preview_key: str | None
    avatar_key: str | None


async def lock_resume(db: AsyncSession, resume_id: str) -> Resume | None:
    return await db.scalar(
        select(Resume).where(Resume.id == resume_id).with_for_update()
    )


async def lock_job(
    db: AsyncSession,
    job_id: str,
    *,
    available_as_of: datetime | None = None,
) -> ResumeImageJob | None:
    statement = select(ResumeImageJob).where(ResumeImageJob.id == job_id)
    if available_as_of is not None:
        statement = statement.where(
            or_(
                ResumeImageJob.lease_until.is_(None),
                ResumeImageJob.lease_until <= available_as_of,
            )
        )
    return await db.scalar(statement.with_for_update())


async def record_failure(
    db: AsyncSession, resume: Resume, row: ResumeImage, job: ResumeImageJob, error: str
) -> None:
    if job.kind == "upload":
        row.status = "failed"
        row.error = error
        repo.schedule_cleanup(db, [row.staging_key], staged=True)
        row.staging_key = None
    if resume.avatar_request_id == job.id:
        resume.avatar_request_id = None
        row.avatar_error = error
    await db.delete(job)


class ImageWorker:
    def __init__(
        self,
        storage: ImageStorage,
        sessions: async_sessionmaker[AsyncSession] = Session,
    ) -> None:
        self.storage = storage
        self.sessions = sessions

    async def dispatch_one(self, broker: RabbitMQService) -> bool:
        now = datetime.now(UTC)
        async with self.sessions() as db, db.begin():
            job = await db.scalar(
                select(ResumeImageJob)
                .where(
                    ResumeImageJob.dispatch_at <= now,
                    or_(
                        ResumeImageJob.lease_until.is_(None),
                        ResumeImageJob.lease_until <= now,
                    ),
                )
                .order_by(ResumeImageJob.dispatch_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if job is None:
                return False
            # При потере confirm или перезапуске тот же job_id уйдёт снова.
            job_id = job.id
            reserved_until = now + timedelta(seconds=30)
            job.dispatch_at = reserved_until

        event = WorkMessage(job_id=UUID(job_id))
        next_dispatch: datetime | None = None
        try:
            await broker.publish(
                BrokerMessage(
                    body=event.model_dump_json().encode(),
                    routing_key=settings.images.image_queue,
                    message_id=job_id,
                    correlation_id=job_id,
                    event_type="resume.image.requested",
                )
            )
        except Exception as error:  # noqa: BLE001 — не теряем строку outbox при сбое транспорта
            logger.warning(
                "Ошибка публикации изображения %s (%s)", job_id, type(error).__name__
            )
            next_dispatch = datetime.now(UTC) + timedelta(seconds=5)
        # После confirm очередь отвечает за доставку. Быстрый consumer мог уже
        # переставить dispatch_at на конец своего lease или удалить задание.
        async with self.sessions() as db, db.begin():
            await db.execute(
                update(ResumeImageJob)
                .where(
                    ResumeImageJob.id == job_id,
                    ResumeImageJob.dispatch_at == reserved_until,
                )
                .values(dispatch_at=next_dispatch)
            )
        return True

    async def claim(self, job_id: str) -> Work | None:
        now = datetime.now(UTC)
        async with self.sessions() as db, db.begin():
            location = (
                await db.execute(
                    select(ResumeImage.resume_id, ResumeImage.id)
                    .join(ResumeImageJob, ResumeImageJob.image_id == ResumeImage.id)
                    .where(ResumeImageJob.id == job_id)
                )
            ).one_or_none()
            if location is None:
                return None
            resume = await lock_resume(db, location.resume_id)
            if resume is None:
                return None
            job = await lock_job(db, job_id, available_as_of=now)
            if job is None:
                return None
            row = await db.get(ResumeImage, location.id)
            assert row is not None
            upload = job.kind == "upload"
            if not upload and resume.avatar_request_id != job.id:
                await db.delete(job)
                return None
            input_key = row.staging_key if upload else row.full_key
            if not input_key or job.attempts >= MAX_ATTEMPTS:
                await record_failure(
                    db,
                    resume,
                    row,
                    job,
                    "Не удалось обработать изображение. Попробуйте загрузить его снова.",
                )
                return None

            token = str(uuid4())
            job.lease_token = token
            job.lease_until = now + LEASE
            job.dispatch_at = job.lease_until
            job.attempts += 1
            if upload:
                row.status = "processing"
                row.error = None
            make_avatar = not upload or (
                job.make_avatar and resume.avatar_request_id == job.id
            )
            prefix = f"{row.resume_id}/{row.id}/{token}"
            full_key = f"{prefix}/full.webp" if upload else None
            preview_key = f"{prefix}/preview.webp" if upload else None
            avatar_key = f"{prefix}/avatar.webp" if make_avatar else None

            # До записи резервируем очистку. Опубликованные варианты исключим при commit.
            cleanup = repo.schedule_cleanup(
                db, [full_key, preview_key, avatar_key], delay=3600
            )
            assert cleanup is not None
            return Work(
                job_id,
                resume.id,
                row.id,
                token,
                input_key,
                upload,
                make_avatar,
                cleanup.id,
                full_key,
                preview_key,
                avatar_key,
            )

    async def process(self, work: Work) -> None:
        data = await self.storage.get(
            work.input_key,
            MAX_INPUT_BYTES if work.upload else MAX_OUTPUT_BYTES,
            staged=work.upload,
        )
        if work.upload:
            assert work.full_key and work.preview_key
            result = await asyncio.to_thread(process_upload, data, work.make_avatar)
            await self.storage.put(work.full_key, result.full, "image/webp")
            await self.storage.put(work.preview_key, result.preview, "image/webp")
            if work.avatar_key and result.avatar is not None:
                await self.storage.put(work.avatar_key, result.avatar, "image/webp")
        else:
            assert work.avatar_key
            avatar = await asyncio.to_thread(crop_avatar, data)
            await self.storage.put(work.avatar_key, avatar, "image/webp")

    async def finish(
        self, work: Work, error: str | None = None, *, retry: bool = False
    ) -> None:
        async with self.sessions() as db, db.begin():
            resume = await lock_resume(db, work.resume_id)
            job = await lock_job(db, work.job_id) if resume else None
            if resume is None or job is None or job.lease_token != work.token:
                return
            row = await db.get(ResumeImage, work.image_id)
            assert row is not None
            if error is not None:
                if retry and job.attempts < MAX_ATTEMPTS:
                    job.lease_until = datetime.now(UTC) + timedelta(
                        seconds=2**job.attempts
                    )
                    job.lease_token = None
                    job.dispatch_at = job.lease_until
                    if work.upload:
                        row.status = "queued"
                else:
                    await record_failure(db, resume, row, job, error)
                return

            latest = resume.avatar_request_id == job.id
            keep_avatar = bool(work.avatar_key and latest)
            if work.upload:
                row.status = "ready"
                row.full_key = work.full_key
                row.preview_key = work.preview_key
                repo.schedule_cleanup(db, [row.staging_key], staged=True)
                row.staging_key = None
            if keep_avatar:
                row.avatar_key = work.avatar_key
                row.avatar_error = None
                resume.avatar_image_id = row.id
                resume.avatar_request_id = None

            # Только актуальный crop становится аватаром. Брошенный вариант удаляем отдельно.
            await db.execute(
                delete(ResumeImageCleanup).where(
                    ResumeImageCleanup.id == work.cleanup_id
                )
            )
            if not keep_avatar:
                repo.schedule_cleanup(db, [work.avatar_key])
            await db.delete(job)

    async def consume(self, message: AbstractIncomingMessage) -> None:
        if len(message.body) > 1024:
            await message.reject(requeue=False)
            return
        try:
            event = WorkMessage.model_validate_json(message.body)
        except ValidationError:
            await message.reject(requeue=False)
            return
        try:
            work = await self.claim(str(event.job_id))
            if work is not None:
                try:
                    await self.process(work)
                except (InvalidImage, ObjectMissing) as error:
                    await self.finish(work, str(error))
                except StorageUnavailable as error:
                    await self.finish(work, str(error), retry=True)
                else:
                    await self.finish(work)
        except Exception as error:  # noqa: BLE001 — lease позволяет безопасно начать заново
            logger.warning(
                "Ошибка обработки %s (%s)", event.job_id, type(error).__name__
            )
            await asyncio.sleep(1)
            await message.nack(requeue=True)
            return
        await message.ack()

    async def cleanup_one(self) -> bool:
        now = datetime.now(UTC)
        async with self.sessions() as db, db.begin():
            row = await db.scalar(
                select(ResumeImageCleanup)
                .where(ResumeImageCleanup.available_at <= now)
                .order_by(ResumeImageCleanup.available_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return False
            row_id, keys, staged = row.id, row.keys, row.staged
            row.available_at = now + LEASE
        try:
            await self.storage.delete(keys, staged=staged)
        except StorageUnavailable:
            logger.warning("Повторим очистку изображений %s", row_id)
            return True
        async with self.sessions() as db, db.begin():
            await db.execute(
                delete(ResumeImageCleanup).where(ResumeImageCleanup.id == row_id)
            )
        return True

    async def run(self) -> None:
        config = settings.reports.model_copy(update={"rabbitmq_prefetch_count": 1})
        async with RabbitMQService(config) as broker:
            queue = await broker.setup_work_queue(settings.images.image_queue)
            tag = await queue.consume(self.consume)
            try:
                while True:
                    try:
                        sent = await self.dispatch_one(broker)
                        removed = await self.cleanup_one()
                    except Exception as error:  # noqa: BLE001 — следующий цикл повторит работу
                        logger.warning(
                            "Ошибка цикла обработки изображений (%s)",
                            type(error).__name__,
                        )
                        sent = removed = False
                    await asyncio.sleep(0.1 if sent or removed else 1)
            finally:
                await queue.cancel(tag)


async def main() -> None:
    try:
        await ImageWorker(get_image_storage()).run()
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("aio_pika").setLevel(logging.CRITICAL)
    logging.getLogger("aiormq").setLevel(logging.CRITICAL)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as error:  # noqa: BLE001 — не раскрываем адрес брокера в traceback
        logger.error("Воркер изображений остановлен (%s)", type(error).__name__)
        raise SystemExit(1) from None
