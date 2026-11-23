"""Проверки общего RabbitMQ-сервиса без запущенного брокера."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.common.broker import BrokerMessage, RabbitMQService
from src.settings import BrokerSettings


class BrokerTests(unittest.IsolatedAsyncioTestCase):
    async def test_connection_topology_confirms_and_cleanup(self) -> None:
        exchange = SimpleNamespace(publish=AsyncMock())
        dead = SimpleNamespace(name="test.dlx")
        rejected = SimpleNamespace(bind=AsyncMock())
        requests = SimpleNamespace(bind=AsyncMock())
        responses = SimpleNamespace(bind=AsyncMock())
        channel = SimpleNamespace(
            set_qos=AsyncMock(),
            declare_exchange=AsyncMock(side_effect=[exchange, dead]),
            declare_queue=AsyncMock(side_effect=[rejected, requests, responses]),
        )
        connection = SimpleNamespace(
            channel=AsyncMock(return_value=channel), close=AsyncMock()
        )
        config = BrokerSettings(
            _env_file=None,
            rabbitmq_url="amqp://test",
            ml_exchange="test",
            ml_request_queue="requests",
            ml_result_queue="responses",
            ml_dead_letter_queue="invalid",
            ml_request_key="requested",
            ml_completed_key="completed",
            ml_failed_key="failed",
            report_message_bytes=1024,
        )
        message = BrokerMessage(
            body=b"{}",
            routing_key="requested",
            message_id="message",
            correlation_id="job",
            event_type="requested",
        )
        with patch(
            "src.common.broker.aio_pika.connect_robust",
            AsyncMock(return_value=connection),
        ):
            async with RabbitMQService(config) as service:
                queues = await service.setup()
                self.assertIs(queues.responses, responses)
                await service.publish(message)
        channel.declare_exchange.assert_any_await("test", "direct", durable=True)
        channel.declare_queue.assert_any_await("requests", durable=True)
        connection.channel.assert_awaited_once_with(
            publisher_confirms=True, on_return_raises=True
        )
        requests.bind.assert_awaited_once_with(exchange, routing_key="requested")
        self.assertEqual(
            [call.kwargs["routing_key"] for call in responses.bind.await_args_list],
            ["completed", "failed"],
        )
        outgoing = exchange.publish.call_args.args[0]
        self.assertEqual(outgoing.delivery_mode, 2)
        self.assertEqual(outgoing.message_id, "message")
        self.assertEqual(outgoing.type, "requested")
        self.assertTrue(exchange.publish.call_args.kwargs["mandatory"])
        connection.close.assert_awaited_once()

    async def test_unconnected_service_rejects_publish(self) -> None:
        message = BrokerMessage(
            body=b"{}",
            routing_key="requested",
            message_id="message",
            correlation_id="job",
            event_type="requested",
        )
        with self.assertRaises(RuntimeError):
            await RabbitMQService(BrokerSettings(_env_file=None)).publish(message)

    async def test_channel_failure_closes_connection(self) -> None:
        connection = SimpleNamespace(
            channel=AsyncMock(side_effect=RuntimeError("Канал недоступен")),
            close=AsyncMock(),
        )
        with (
            patch(
                "src.common.broker.aio_pika.connect_robust",
                AsyncMock(return_value=connection),
            ),
            self.assertRaises(RuntimeError),
        ):
            async with RabbitMQService(BrokerSettings(_env_file=None)):
                self.fail("Не должны войти при ошибке подключения")
        connection.close.assert_awaited_once()
