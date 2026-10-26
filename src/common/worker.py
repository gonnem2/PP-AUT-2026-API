"""Фоновый процесс обмена с ML Core; бизнес-правила находятся в projects.service."""

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import NoReturn

import aio_pika
from aio_pika.abc import AbstractIncomingMessage
from pydantic import TypeAdapter, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.common.db.session import Session, async_engine
from src.projects import repo
from src.projects.broker import ReportBroker, encode_payload, topology
from src.projects.dto import ClaimedJob, ClaimUpdate, ReportPublisher
from src.projects.exception import InvalidCoreResult
from src.projects.schemas import CoreRequest, CoreResult
from src.projects.service import ReportProcessingService
from src.settings import ReportWorkerSettings, settings

logger = logging.getLogger(__name__)


class ReportWorker:
    """Доставляет задания из PostgreSQL и подтверждает сохранённые ответы RabbitMQ."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession] = Session,
        processor: ReportProcessingService | None = None,
        config: ReportWorkerSettings | None = None,
    ) -> None:
        self.sessions = session_factory
        self.processor = processor or ReportProcessingService()
        self.config = config or settings.reports

    async def _claim_job(self) -> ClaimedJob | None:
        """Захватывает одно актуальное задание и закрывает транзакцию до сетевых вызовов."""
        async with self.sessions() as db, db.begin():
            await repo.expire(db)
            row = await repo.claim(db, self.config.report_lease_seconds)
            if row is None:
                return None
            state = await repo.state(db, row.user_id)
            if (
                state is None
                or state.latest_job_id != row.id
                or state.library_revision != row.input_revision
            ):
                # Пользователь уже изменил выбор: дорогая подготовка больше не нужна.
                row.status = "superseded"
                return None
            assert row.lease_token is not None
            return ClaimedJob(
                id=row.id,
                message_id=row.message_id,
                input_revision=row.input_revision,
                input_snapshot=row.input_snapshot,
                request_payload=row.request_payload,
                lease_token=row.lease_token,
                attempts=row.attempts,
            )

    async def _prepare_payload(self, job: ClaimedJob) -> CoreRequest | None:
        """Возвращает сохранённый запрос либо фиксирует новый перед первой отправкой."""
        if job.request_payload is not None:
            # Повтор обязан иметь прежние SHA и message_id: ML дедуплицирует запросы.
            return job.request_payload
        async with asyncio.timeout(self.config.report_prepare_timeout):
            payload = await self.processor.prepare(job)
        encode_payload(payload, self.config.report_message_bytes)
        async with self.sessions() as db, db.begin():
            saved = await repo.update_claim(
                db,
                job.id,
                job.lease_token,
                changes=ClaimUpdate(request_payload=payload),
            )
        # Если lease уже забрал другой процесс, этот воркер не отправляет запрос.
        return payload if saved else None

    async def _mark_waiting(self, job: ClaimedJob) -> None:
        """После confirm переводит задание в ожидание ответа, не затирая быстрый результат."""
        async with self.sessions() as db, db.begin():
            await repo.update_claim(
                db,
                job.id,
                job.lease_token,
                changes=ClaimUpdate(
                    status="waiting",
                    lease_until=None,
                    lease_token=None,
                    deadline_at=datetime.now(UTC)
                    + timedelta(seconds=self.config.report_result_timeout),
                    error=None,
                ),
            )

    async def _record_failure(self, job: ClaimedJob, error: Exception) -> None:
        """Планирует повтор с задержкой либо завершает неисправимое задание."""
        permanent = isinstance(error, (ValueError, ValidationError))
        exhausted = job.attempts >= self.config.report_max_attempts
        # Экспоненциальная задержка ограничена минутой, чтобы не перегружать зависимости.
        retry_delay = min(60, 2**job.attempts)
        async with self.sessions() as db, db.begin():
            await repo.update_claim(
                db,
                job.id,
                job.lease_token,
                changes=ClaimUpdate(
                    status="failed" if permanent or exhausted else "queued",
                    lease_until=None,
                    lease_token=None,
                    available_at=datetime.now(UTC) + timedelta(seconds=retry_delay),
                    error="Не удалось подготовить или доставить запрос в ML Core",
                ),
            )
        # Текст исключения может содержать URL с паролем: логируем только его тип.
        logger.warning("Ошибка доставки задания %s (%s)", job.id, type(error).__name__)

    async def dispatch_one(self, broker: ReportPublisher) -> bool:
        """Обрабатывает одно задание; False означает, что пора сделать паузу."""
        job = await self._claim_job()
        if job is None:
            return False
        try:
            if job.attempts > self.config.report_max_attempts:
                raise ValueError("Исчерпан лимит попыток доставки")
            payload = await self._prepare_payload(job)
            if payload is None:
                return True
            # Сеть не держит транзакцию БД; таймаут оставляет запас до конца lease.
            async with asyncio.timeout(25):
                await broker.publish(payload)
            await self._mark_waiting(job)
        except Exception as error:  # noqa: BLE001 — ошибка задания не останавливает процесс
            await self._record_failure(job, error)
        return True

    async def consume(self, message: AbstractIncomingMessage) -> None:
        """Проверяет ответ ML и отправляет ack строго после успешного commit."""
        try:
            if len(message.body) > self.config.report_message_bytes:
                raise InvalidCoreResult("Слишком большое сообщение")
            event = TypeAdapter(CoreResult).validate_json(message.body)
            async with self.sessions() as db, db.begin():
                await self.processor.receive(db, event)
        except (ValidationError, InvalidCoreResult):
            # Повтор не исправит неверный контракт: сообщение попадёт в DLQ для разбора.
            await message.reject(requeue=False)
            return
        except Exception as error:  # noqa: BLE001 — временная ошибка не должна терять ответ
            logger.warning(
                "Не удалось сохранить результат ML (%s)", type(error).__name__
            )
            await asyncio.sleep(1)
            await message.nack(requeue=True)
            return
        # Выход из db.begin выше уже выполнил commit. При сбое раньше ack не отправляется.
        await message.ack()

    async def publish_loop(self, broker: ReportPublisher) -> NoReturn:
        """Опрашивает outbox; ошибки БД не останавливают последующие попытки."""
        while True:
            try:
                worked = await self.dispatch_one(broker)
            except Exception as error:  # noqa: BLE001 — сбой зависимости не останавливает цикл
                logger.warning("Ошибка цикла воркера (%s)", type(error).__name__)
                worked = False
            await asyncio.sleep(0.1 if worked else 1)

    async def run(self) -> None:
        """Объявляет топологию RabbitMQ и одновременно запускает отправку и приём."""
        connection = await aio_pika.connect_robust(self.config.rabbitmq_url)
        async with connection:
            channel = await connection.channel(
                publisher_confirms=True, on_return_raises=True
            )
            # Один неподтверждённый ответ на процесс ограничивает нагрузку на БД.
            await channel.set_qos(prefetch_count=1)
            exchange, results = await topology(channel, self.config)
            tag = await results.consume(self.consume)
            try:
                await self.publish_loop(
                    ReportBroker(exchange, self.config.report_message_bytes)
                )
            finally:
                await results.cancel(tag)


async def main() -> None:
    """Запускает процесс и освобождает пул PostgreSQL при остановке."""
    try:
        await ReportWorker().run()
    finally:
        await async_engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Транспорт может логировать адрес подключения с паролем: скрываем такие сообщения.
    logging.getLogger("aio_pika").setLevel(logging.CRITICAL)
    logging.getLogger("aiormq").setLevel(logging.CRITICAL)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as error:  # noqa: BLE001 — не раскрываем адрес брокера в traceback
        logger.error("Воркер остановлен (%s)", type(error).__name__)
        raise SystemExit(1) from None
