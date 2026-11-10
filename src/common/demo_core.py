"""Явная заглушка ML Core для локальной проверки транспорта и кеша."""

import argparse
import asyncio
import logging
import os
from uuid import NAMESPACE_URL, uuid5

from aio_pika.abc import AbstractIncomingMessage
from pydantic import TypeAdapter, ValidationError

from src.common.broker import BrokerMessage, RabbitMQService
from src.projects.schemas import CoreRepositoryResult, CoreRequest, CoreResult
from src.settings import settings

logger = logging.getLogger(__name__)


def demo_result(request: CoreRequest, *, fail: bool = False) -> CoreResult:
    """Возвращает детерминированный тестовый ответ, не анализируя содержимое GitHub."""
    message_id = uuid5(NAMESPACE_URL, f"resumehub-demo:{request.message_id}:{fail}")
    if fail:
        return CoreResult(
            type="github.report.failed",
            message_id=message_id,
            job_id=request.job_id,
            error="Демонстрационная ошибка Core. Предыдущий отчёт сохранён.",
        )
    repositories: list[CoreRepositoryResult] = []
    lines = [
        "# Демонстрационный отчёт — заглушка Core",
        "Это проверка интеграции. Анализ кода и оценка личного вклада не выполнялись.",
        "",
        "## Выбранные репозитории",
    ]
    for item in request.repositories:
        cached = item.cached_report
        repositories.append(
            CoreRepositoryResult(
                repo_id=item.repo_id,
                commit_sha=item.commit_sha,
                context_hash=item.context_hash,
                report_text=cached.report_text
                if cached
                else (
                    "Демонстрационный отчёт проекта. Анализ кода не выполнялся.\n"
                    f"Репозиторий: {item.repository_url}\nSHA: {item.commit_sha}"
                ),
                summary_ru=cached.summary_ru
                if cached
                else "Тестовый результат заглушки Core",
            )
        )
        source = "сохранённый анализ (кеш)" if cached else "новый тестовый результат"
        lines.append(f"- {item.repository_url} — {source}; SHA {item.commit_sha}")
    return CoreResult(
        type="github.report.completed",
        message_id=message_id,
        job_id=request.job_id,
        repositories=repositories,
        report_text="\n".join(lines),
        summary_ru=f"Тестовый общий отчёт по {len(repositories)} репозиториям. Это заглушка Core.",
    )


async def consume_demo(
    message: AbstractIncomingMessage,
    transport: RabbitMQService,
    delay: float,
    fail: bool,
) -> None:
    """Подтверждает запрос только после подтверждаемой публикации ответа."""
    try:
        if len(message.body) > transport.config.report_message_bytes:
            raise ValueError("Слишком большой запрос")
        request = TypeAdapter(CoreRequest).validate_json(message.body)
    except (ValidationError, ValueError):
        await message.reject(requeue=False)
        return
    try:
        await asyncio.sleep(delay)
        result = demo_result(request, fail=fail)
        await transport.publish(
            BrokerMessage(
                body=TypeAdapter(CoreResult).dump_json(result),
                routing_key=transport.config.ml_failed_key
                if fail
                else transport.config.ml_completed_key,
                message_id=str(result.message_id),
                correlation_id=str(result.job_id),
                event_type=result.type,
            )
        )
    except Exception as error:  # noqa: BLE001 — временный сбой не теряет запрос
        logger.warning("Ошибка заглушки (%s)", type(error).__name__)
        await message.nack(requeue=True)
        return
    await message.ack()


async def run(delay: float, fail: bool) -> None:
    """Подключает отдельного тестового потребителя вместо настоящего Core."""
    async with RabbitMQService(settings.reports) as transport:
        queues = await transport.setup()

        async def consume(message: AbstractIncomingMessage) -> None:
            await consume_demo(message, transport, delay, fail)

        tag = await queues.requests.consume(consume)
        logger.info("Запущена ЗАГЛУШКА Core; анализ кода не выполняется")
        try:
            await asyncio.Future()
        finally:
            await queues.requests.cancel(tag)


def main() -> None:
    """Включает заглушку только при явном запуске отдельного процесса."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay", type=float, default=5.0)
    parser.add_argument(
        "--fail", action="store_true", default=os.getenv("DEMO_CORE_FAIL") == "1"
    )
    args = parser.parse_args()
    if not 0 <= args.delay <= 60:
        parser.error("--delay должен быть от 0 до 60 секунд")
    logging.basicConfig(level=logging.INFO)
    logging.getLogger("aio_pika").setLevel(logging.CRITICAL)
    logging.getLogger("aiormq").setLevel(logging.CRITICAL)
    try:
        asyncio.run(run(args.delay, args.fail))
    except KeyboardInterrupt:
        pass
    except Exception as error:  # noqa: BLE001 — не печатаем адрес брокера с паролем
        logger.error("Заглушка остановлена (%s)", type(error).__name__)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
