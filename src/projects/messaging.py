"""Контракт сообщений GitHub-отчётов поверх общего транспорта RabbitMQ."""

from pydantic import TypeAdapter

from src.common.broker import BrokerMessage, RabbitMQService
from src.projects.ml_protocol import WireRequest


def encode_payload(payload: WireRequest, limit: int) -> bytes:
    body = TypeAdapter(WireRequest).dump_json(payload)
    if len(body) > limit:
        raise ValueError("Отчёт превышает допустимый размер сообщения")
    return body


class ReportBroker:
    """Преобразует CoreRequest в транспортное сообщение, не управляя соединением."""

    def __init__(self, transport: RabbitMQService) -> None:
        self.transport = transport

    async def publish(self, payload: WireRequest) -> None:
        # При повторе сохраняем ID: ML Core должен дедуплицировать такой запрос.
        message = BrokerMessage(
            body=encode_payload(payload, self.transport.config.report_message_bytes),
            routing_key=self.transport.config.ml_request_key,
            message_id=str(payload.message_id),
            correlation_id=str(payload.job_id),
            event_type=payload.type,
        )
        await self.transport.publish(message)
