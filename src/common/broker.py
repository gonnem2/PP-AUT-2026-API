"""Самостоятельный сервис транспорта RabbitMQ без зависимости от projects."""

from dataclasses import dataclass
from types import TracebackType
from typing import Self

import aio_pika
from aio_pika.abc import (
    AbstractChannel,
    AbstractExchange,
    AbstractQueue,
    AbstractRobustConnection,
)

from src.settings import BrokerSettings


@dataclass(frozen=True, kw_only=True)
class BrokerMessage:
    """Готовое сообщение: сериализацией бизнес-объекта занимается вызывающий модуль."""

    body: bytes
    routing_key: str
    message_id: str
    correlation_id: str
    event_type: str


@dataclass(frozen=True, kw_only=True)
class BrokerQueues:
    requests: AbstractQueue
    responses: AbstractQueue
    rejected: AbstractQueue


class RabbitMQService:
    """Управляет подключением, очередями и подтверждаемой публикацией сообщений."""

    def __init__(self, config: BrokerSettings) -> None:
        self.config = config
        self._connection: AbstractRobustConnection | None = None
        self._channel: AbstractChannel | None = None
        self._exchange: AbstractExchange | None = None

    async def __aenter__(self) -> Self:
        """Открывает соединение; robust восстанавливает его при сетевом обрыве."""
        try:
            self._connection = await aio_pika.connect_robust(self.config.rabbitmq_url)
            self._channel = await self._connection.channel(
                publisher_confirms=True, on_return_raises=True
            )
            await self._channel.set_qos(
                prefetch_count=self.config.rabbitmq_prefetch_count
            )
        except BaseException:
            # Даже при ошибке создания канала уже открытое соединение закрывается.
            await self.close()
            raise
        return self

    async def __aexit__(
        self,
        error_type: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        """Освобождает соединение; повторный вызов безопасен."""
        if self._connection is not None:
            await self._connection.close()
        self._connection = None
        self._channel = None
        self._exchange = None

    async def setup(self) -> BrokerQueues:
        """Создаёт durable-очереди запросов, ответов и невалидных сообщений."""
        if self._channel is None:
            raise RuntimeError("Сервис RabbitMQ не подключён")
        channel = self._channel
        exchange = await channel.declare_exchange(
            self.config.ml_exchange, "direct", durable=True
        )
        dead = await channel.declare_exchange(
            self.config.ml_exchange + ".dlx", "direct", durable=True
        )
        rejected = await channel.declare_queue(
            self.config.ml_dead_letter_queue, durable=True
        )
        await rejected.bind(dead, routing_key="invalid")
        requests = await channel.declare_queue(
            self.config.ml_request_queue, durable=True
        )
        await requests.bind(exchange, routing_key=self.config.ml_request_key)
        # Mapping требуется библиотеке RabbitMQ для аргументов объявления очереди.
        responses = await channel.declare_queue(
            self.config.ml_result_queue,
            durable=True,
            arguments={
                "x-dead-letter-exchange": dead.name,
                "x-dead-letter-routing-key": "invalid",
            },
        )
        for routing_key in (self.config.ml_completed_key, self.config.ml_failed_key):
            await responses.bind(exchange, routing_key=routing_key)
        self._exchange = exchange
        return BrokerQueues(requests=requests, responses=responses, rejected=rejected)

    async def publish(self, message: BrokerMessage) -> None:
        """Ждёт confirm брокера; сообщение без подходящей очереди считается ошибкой."""
        if self._exchange is None:
            raise RuntimeError("Топология RabbitMQ не настроена")
        if len(message.body) > self.config.report_message_bytes:
            raise ValueError("Сообщение превышает допустимый размер")
        await self._exchange.publish(
            aio_pika.Message(
                body=message.body,
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                message_id=message.message_id,
                correlation_id=message.correlation_id,
                type=message.event_type,
            ),
            routing_key=message.routing_key,
            mandatory=True,
            timeout=20,
        )
