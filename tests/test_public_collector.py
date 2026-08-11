from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

from websockets.asyncio.server import ServerConnection, serve

from hyperbot.event_store import JsonlEventStore
from hyperbot.models import (
    CollectorControlEvent,
    CollectorControlKind,
    DomainEvent,
    EventContext,
    TimeSource,
)
from hyperbot.services.public_collector import (
    CollectorConfig,
    PublicWebSocketCollector,
    Subscription,
)


def _context() -> EventContext:
    return EventContext("collector-test", "test", "c" * 64, TimeSource.EXCHANGE)


def test_fake_server_reconnect_malformed_heartbeat_and_clean_shutdown(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[object, list[dict[str, object]]]:
        stop = asyncio.Event()
        sent_by_client: list[dict[str, object]] = []
        connections = 0

        async def handler(socket: ServerConnection) -> None:
            nonlocal connections
            connections += 1
            for _ in range(3):
                sent_by_client.append(json.loads(await socket.recv()))
            if connections == 1:
                await socket.send("not-json")
                await socket.send(
                    json.dumps(
                        {
                            "channel": "l2Book",
                            "data": {"coin": "BTC", "time": 101, "levels": [[], []]},
                        }
                    )
                )
                return
            await socket.send(
                json.dumps(
                    {
                        "channel": "bbo",
                        "data": {"coin": "BTC", "time": 102, "bbo": [None, None]},
                    }
                )
            )
            await socket.send(
                json.dumps(
                    {
                        "channel": "trades",
                        "data": [
                            {
                                "coin": "BTC",
                                "time": 103,
                                "px": "1",
                                "sz": "2",
                                "side": "B",
                            }
                        ],
                    }
                )
            )
            ping = json.loads(await socket.recv())
            sent_by_client.append(ping)
            await socket.send(json.dumps({"channel": "pong"}))
            await asyncio.sleep(0.03)
            stop.set()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            store = JsonlEventStore(tmp_path, fsync=False)
            config = CollectorConfig(
                subscriptions=(
                    Subscription("l2Book", "BTC"),
                    Subscription("bbo", "BTC"),
                    Subscription("trades", "BTC"),
                ),
                websocket_url=f"ws://127.0.0.1:{port}",
                heartbeat_interval_seconds=0.01,
                stale_after_seconds=0.1,
                reconnect_initial_seconds=0.01,
                reconnect_max_seconds=0.02,
            )
            metrics = await PublicWebSocketCollector(
                config=config,
                context=_context(),
                store=store,
            ).run(stop)
            control = store.read_records("collector-control")
            data = store.read_records("public-market-data")
        return metrics, sent_by_client + [
            {"control": record["payload"]} for record in control
        ] + [{"data": record["payload"]} for record in data]

    metrics, observations = asyncio.run(scenario())
    client_messages = [item for item in observations if "method" in item]
    controls = [
        item["control"]
        for item in observations
        if "control" in item and isinstance(item["control"], dict)
    ]
    data = [item["data"] for item in observations if "data" in item]

    assert metrics.reconnects == 1
    assert metrics.malformed_messages == 1
    assert {item["channel"] for item in data} == {"l2Book", "bbo", "trades"}
    control_kinds = {item["kind"] for item in controls}
    assert CollectorControlKind.GAP.value in control_kinds
    assert CollectorControlKind.RECONNECTED.value in control_kinds
    assert CollectorControlKind.HEARTBEAT_SENT.value in control_kinds
    assert CollectorControlKind.HEARTBEAT_ACK.value in control_kinds
    assert CollectorControlKind.SHUTDOWN.value in control_kinds
    assert {message["method"] for message in client_messages} <= {
        "subscribe",
        "ping",
    }


class _SlowStore:
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []
        self._lock = threading.Lock()

    def append(self, stream: str, event: DomainEvent) -> object:
        del stream
        time.sleep(0.005)
        with self._lock:
            self.events.append(event)
        return None


class _BatchStore:
    def __init__(self) -> None:
        self.batch_sizes: list[int] = []
        self.events: list[DomainEvent] = []

    def append(self, stream: str, event: DomainEvent) -> object:
        del stream
        self.batch_sizes.append(1)
        self.events.append(event)
        return None

    def append_many(self, stream: str, events: list[DomainEvent]) -> object:
        del stream
        self.batch_sizes.append(len(events))
        self.events.extend(events)
        return None


def test_persistence_writer_batches_a_busy_public_feed() -> None:
    async def scenario() -> tuple[object, _BatchStore]:
        stop = asyncio.Event()
        store = _BatchStore()

        async def handler(socket: ServerConnection) -> None:
            await socket.recv()
            for index in range(100):
                await socket.send(
                    json.dumps(
                        {
                            "channel": "bbo",
                            "data": {
                                "coin": "BTC",
                                "time": index,
                                "bbo": [None, None],
                            },
                        }
                    )
                )
            await asyncio.sleep(0.05)
            stop.set()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            collector = PublicWebSocketCollector(
                config=CollectorConfig(
                    subscriptions=(Subscription("bbo", "BTC"),),
                    queue_capacity=1_000,
                    persistence_batch_size=16,
                    websocket_url=f"ws://127.0.0.1:{port}",
                    heartbeat_interval_seconds=0.02,
                    stale_after_seconds=0.1,
                ),
                context=_context(),
                store=store,
            )
            metrics = await collector.run(stop)
        return metrics, store

    metrics, store = asyncio.run(scenario())

    assert metrics.dropped_events == 0
    assert metrics.received_messages == 100
    assert max(store.batch_sizes) > 1
    assert len(store.events) == metrics.persisted_events


def test_bounded_queue_reports_overload_without_silent_blocking() -> None:
    async def scenario() -> tuple[object, _SlowStore]:
        stop = asyncio.Event()
        store = _SlowStore()

        async def handler(socket: ServerConnection) -> None:
            await socket.recv()
            for index in range(200):
                await socket.send(
                    json.dumps(
                        {
                            "channel": "l2Book",
                            "data": {
                                "coin": "BTC",
                                "time": index,
                                "levels": [[], []],
                            },
                        }
                    )
                )
            await asyncio.sleep(0.05)
            stop.set()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            collector = PublicWebSocketCollector(
                config=CollectorConfig(
                    subscriptions=(Subscription("l2Book", "BTC"),),
                    queue_capacity=1,
                    websocket_url=f"ws://127.0.0.1:{port}",
                    heartbeat_interval_seconds=0.02,
                    stale_after_seconds=0.1,
                ),
                context=_context(),
                store=store,
            )
            metrics = await collector.run(stop)
        return metrics, store

    metrics, store = asyncio.run(scenario())

    assert metrics.dropped_events > 0
    assert any(
        isinstance(event, CollectorControlEvent)
        and event.kind is CollectorControlKind.QUEUE_DROP
        and event.dropped_messages > 0
        for event in store.events
    )


def test_only_public_channels_and_loopback_or_tls_endpoints_are_allowed() -> None:
    try:
        Subscription("userFills", "BTC")
    except ValueError as exc:
        assert "unsupported public channel" in str(exc)
    else:
        raise AssertionError("private subscription unexpectedly accepted")

    try:
        CollectorConfig(
            subscriptions=(Subscription("bbo", "BTC"),),
            websocket_url="ws://example.invalid/ws",
        )
    except ValueError as exc:
        assert "WSS" in str(exc)
    else:
        raise AssertionError("insecure remote endpoint unexpectedly accepted")
