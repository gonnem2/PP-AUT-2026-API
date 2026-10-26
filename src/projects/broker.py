"""Транспорт RabbitMQ: топология и публикация с подтверждением брокера."""

import aio_pika
from aio_pika.abc import AbstractChannel, AbstractExchange, AbstractQueue
from pydantic import TypeAdapter

from src.projects.schemas import CoreRequest
from src.settings import ReportWorkerSettings

REQUEST = "github.report.requested"
COMPLETED = "github.report.completed"
FAILED = "github.report.failed"


def encode_payload(payload: CoreRequest, limit: int) -> bytes:
    body = TypeAdapter(CoreRequest).dump_json(payload)
    if len(body) > limit:
        raise ValueError("Отчёт превышает допустимый размер сообщения")
    return body


async def topology(
    channel: AbstractChannel, config: ReportWorkerSettings
) -> tuple[AbstractExchange, AbstractQueue]:
    exchange = await channel.declare_exchange(
        config.ml_exchange, "direct", durable=True
    )
    dead = await channel.declare_exchange(
        config.ml_exchange + ".dlx", "direct", durable=True
    )
    invalid = await channel.declare_queue(config.ml_dead_letter_queue, durable=True)
    await invalid.bind(dead, routing_key="invalid")
    requests = await channel.declare_queue(config.ml_request_queue, durable=True)
    await requests.bind(exchange, routing_key=REQUEST)
    results = await channel.declare_queue(
        config.ml_result_queue,
        durable=True,
        arguments={
            "x-dead-letter-exchange": dead.name,
            "x-dead-letter-routing-key": "invalid",
        },
    )
    await results.bind(exchange, routing_key=COMPLETED)
    await results.bind(exchange, routing_key=FAILED)
    return exchange, results


class ReportBroker:
    def __init__(self, exchange: AbstractExchange, limit: int) -> None:
        self.exchange, self.limit = exchange, limit

    async def publish(self, payload: CoreRequest) -> None:
        body = encode_payload(payload, self.limit)
        # Один message_id переживает повторную отправку после сбоя процесса.
        await self.exchange.publish(
            aio_pika.Message(
                body=body,
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=str(payload.message_id),
                correlation_id=str(payload.job_id),
                type=REQUEST,
            ),
            routing_key=REQUEST,
            mandatory=True,
            timeout=20,
        )
