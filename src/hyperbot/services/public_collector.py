"""Durable, public-only Hyperliquid WebSocket collector."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from websockets.asyncio.client import connect

from hyperbot.models import (
    CollectorControlEvent,
    CollectorControlKind,
    DomainEvent,
    EventContext,
    PublicMarketDataEvent,
)

PUBLIC_WS_URL = "wss://api.hyperliquid.xyz/ws"
ALLOWED_CHANNELS = frozenset({"l2Book", "bbo", "trades"})


class EventAppender(Protocol):
    def append(self, stream: str, event: DomainEvent) -> object:
        """Persist one domain event."""


class WebSocketConnection(Protocol):
    async def send(self, message: str) -> None:
        """Send one text message."""

    async def recv(self) -> str | bytes:
        """Receive one text or binary message."""


class ConnectionFactory(Protocol):
    def __call__(
        self,
        url: str,
    ) -> contextlib.AbstractAsyncContextManager[WebSocketConnection]: ...


@dataclass(frozen=True, slots=True)
class Subscription:
    channel: str
    coin: str

    def __post_init__(self) -> None:
        if self.channel not in ALLOWED_CHANNELS:
            raise ValueError(f"unsupported public channel: {self.channel}")
        if not self.coin.strip():
            raise ValueError("subscription coin must not be empty")


@dataclass(frozen=True, slots=True)
class CollectorConfig:
    subscriptions: tuple[Subscription, ...]
    queue_capacity: int = 10_000
    persistence_batch_size: int = 256
    heartbeat_interval_seconds: float = 20.0
    stale_after_seconds: float = 60.0
    reconnect_initial_seconds: float = 0.25
    reconnect_max_seconds: float = 15.0
    websocket_url: str = PUBLIC_WS_URL
    data_stream: str = "public-market-data"
    control_stream: str = "collector-control"

    def __post_init__(self) -> None:
        if not self.subscriptions:
            raise ValueError("at least one public subscription is required")
        if len(set(self.subscriptions)) != len(self.subscriptions):
            raise ValueError("subscriptions must be unique")
        if len(self.subscriptions) > 1_000:
            raise ValueError("Hyperliquid allows at most 1,000 subscriptions")
        if self.queue_capacity <= 0:
            raise ValueError("queue_capacity must be positive")
        if self.persistence_batch_size <= 0:
            raise ValueError("persistence batch size must be positive")
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat interval must be positive")
        if self.stale_after_seconds < self.heartbeat_interval_seconds:
            raise ValueError("stale timeout must cover at least one heartbeat")
        if self.reconnect_initial_seconds <= 0:
            raise ValueError("initial reconnect delay must be positive")
        if self.reconnect_max_seconds < self.reconnect_initial_seconds:
            raise ValueError("maximum reconnect delay is inconsistent")
        is_public_wss = self.websocket_url.startswith("wss://")
        is_loopback_test = self.websocket_url.startswith("ws://127.0.0.1")
        if not is_public_wss and not is_loopback_test:
            raise ValueError("collector URL must be WSS or a loopback test server")


@dataclass(frozen=True, slots=True)
class CollectorMetrics:
    received_messages: int
    persisted_events: int
    dropped_events: int
    reconnects: int
    malformed_messages: int
    connected: bool
    last_message_receive_ms: int | None


class _ReconnectRequired(RuntimeError):
    pass


def _default_connection_factory(
    url: str,
) -> contextlib.AbstractAsyncContextManager[WebSocketConnection]:
    return cast(
        contextlib.AbstractAsyncContextManager[WebSocketConnection],
        connect(url, ping_interval=None, open_timeout=15),
    )


class PublicWebSocketCollector:
    """Collect only whitelisted public feeds; this class has no order API."""

    def __init__(
        self,
        *,
        config: CollectorConfig,
        context: EventContext,
        store: EventAppender,
        connection_factory: ConnectionFactory = _default_connection_factory,
        wall_clock_ms: Callable[[], int] | None = None,
        monotonic_ns: Callable[[], int] | None = None,
    ) -> None:
        self.config = config
        self.context = context
        self.store = store
        self.connection_factory = connection_factory
        self.wall_clock_ms = wall_clock_ms or (lambda: int(time.time() * 1000))
        self.monotonic_ns = monotonic_ns or time.monotonic_ns
        self._queue: asyncio.Queue[DomainEvent | None] = asyncio.Queue(
            maxsize=config.queue_capacity
        )
        self._received_messages = 0
        self._persisted_events = 0
        self._dropped_events = 0
        self._reported_drops = 0
        self._reconnects = 0
        self._malformed_messages = 0
        self._local_sequence = 0
        self._attempt = 0
        self._connected = False
        self._last_message_receive_ms: int | None = None

    @property
    def metrics(self) -> CollectorMetrics:
        return CollectorMetrics(
            received_messages=self._received_messages,
            persisted_events=self._persisted_events,
            dropped_events=self._dropped_events,
            reconnects=self._reconnects,
            malformed_messages=self._malformed_messages,
            connected=self._connected,
            last_message_receive_ms=self._last_message_receive_ms,
        )

    async def run(self, stop: asyncio.Event) -> CollectorMetrics:
        """Run until stop is set, then drain every queued event."""

        writer = asyncio.create_task(self._writer(), name="hyperbot-public-writer")
        delay = self.config.reconnect_initial_seconds
        try:
            while not stop.is_set():
                self._attempt += 1
                try:
                    async with self.connection_factory(
                        self.config.websocket_url
                    ) as connection:
                        self._connected = True
                        kind = (
                            CollectorControlKind.CONNECTED
                            if self._attempt == 1
                            else CollectorControlKind.RECONNECTED
                        )
                        if kind is CollectorControlKind.RECONNECTED:
                            self._reconnects += 1
                        self._enqueue_control(kind, None)
                        await self._subscribe(connection)
                        delay = self.config.reconnect_initial_seconds
                        await self._consume_connection(connection, stop)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._connected = False
                    if stop.is_set():
                        break
                    reason = f"{type(exc).__name__}: {exc}"
                    self._enqueue_control(CollectorControlKind.DISCONNECTED, reason)
                    self._enqueue_control(CollectorControlKind.GAP, reason)
                    with contextlib.suppress(TimeoutError):
                        await asyncio.wait_for(stop.wait(), timeout=delay)
                    delay = min(delay * 2, self.config.reconnect_max_seconds)
            self._enqueue_control(CollectorControlKind.SHUTDOWN, "requested")
        finally:
            self._connected = False
            await self._queue.put(None)
            await writer
        return self.metrics

    async def _subscribe(self, connection: WebSocketConnection) -> None:
        for subscription in self.config.subscriptions:
            message = {
                "method": "subscribe",
                "subscription": {
                    "type": subscription.channel,
                    "coin": subscription.coin,
                },
            }
            await connection.send(
                json.dumps(message, separators=(",", ":"), sort_keys=True)
            )

    async def _consume_connection(
        self,
        connection: WebSocketConnection,
        stop: asyncio.Event,
    ) -> None:
        last_message_ns = self.monotonic_ns()
        receive_task = asyncio.create_task(connection.recv())
        stop_task = asyncio.create_task(stop.wait())
        try:
            while not stop.is_set():
                done, _ = await asyncio.wait(
                    {receive_task, stop_task},
                    timeout=self.config.heartbeat_interval_seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if stop_task in done:
                    return
                if receive_task in done:
                    raw = receive_task.result()
                    receive_ts_ms = self.wall_clock_ms()
                    self._last_message_receive_ms = receive_ts_ms
                    receive_monotonic_ns = self.monotonic_ns()
                    last_message_ns = receive_monotonic_ns
                    self._received_messages += 1
                    self._handle_message(raw, receive_ts_ms, receive_monotonic_ns)
                    receive_task = asyncio.create_task(connection.recv())
                    continue
                elapsed_seconds = (self.monotonic_ns() - last_message_ns) / 1e9
                if elapsed_seconds >= self.config.stale_after_seconds:
                    self._enqueue_control(
                        CollectorControlKind.GAP,
                        f"no message for {elapsed_seconds:.3f}s",
                    )
                    raise _ReconnectRequired("public feed became stale")
                await connection.send('{"method":"ping"}')
                self._enqueue_control(CollectorControlKind.HEARTBEAT_SENT, None)
        finally:
            for task in (receive_task, stop_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(receive_task, stop_task, return_exceptions=True)

    def _handle_message(
        self,
        raw: str | bytes,
        receive_ts_ms: int,
        receive_monotonic_ns: int,
    ) -> None:
        try:
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            decoded = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            self._malformed_messages += 1
            self._enqueue_control(
                CollectorControlKind.MALFORMED_MESSAGE,
                f"{type(exc).__name__}: {exc}",
            )
            return
        if not isinstance(decoded, dict):
            self._malformed_messages += 1
            self._enqueue_control(
                CollectorControlKind.MALFORMED_MESSAGE,
                "message is not a JSON object",
            )
            return
        channel = decoded.get("channel")
        if channel == "pong":
            self._enqueue_control(CollectorControlKind.HEARTBEAT_ACK, None)
            return
        if channel == "subscriptionResponse":
            return
        if not isinstance(channel, str) or channel not in ALLOWED_CHANNELS:
            self._malformed_messages += 1
            self._enqueue_control(
                CollectorControlKind.MALFORMED_MESSAGE,
                f"unsupported channel: {channel!r}",
            )
            return
        data = decoded.get("data")
        payloads: Sequence[object] = (
            data if channel == "trades" and isinstance(data, list) else (data,)
        )
        for payload in payloads:
            if not isinstance(payload, dict):
                self._malformed_messages += 1
                self._enqueue_control(
                    CollectorControlKind.MALFORMED_MESSAGE,
                    f"invalid {channel} payload",
                )
                continue
            coin = payload.get("coin")
            allowed_coins = {
                item.coin
                for item in self.config.subscriptions
                if item.channel == channel
            }
            if not isinstance(coin, str) or coin not in allowed_coins:
                self._enqueue_control(
                    CollectorControlKind.UNEXPECTED_MARKET,
                    f"{channel}:{coin!r}",
                )
                continue
            timestamp = payload.get("time")
            exchange_ts_ms = timestamp if isinstance(timestamp, int) else None
            event = PublicMarketDataEvent(
                context=self.context,
                channel=channel,
                coin=coin,
                exchange_ts_ms=exchange_ts_ms,
                receive_ts_ms=receive_ts_ms,
                receive_monotonic_ns=receive_monotonic_ns,
                local_sequence=self._local_sequence,
                payload_json=json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
            self._local_sequence += 1
            self._enqueue(event)

    def _enqueue_control(
        self,
        kind: CollectorControlKind,
        reason: str | None,
    ) -> None:
        self._enqueue(
            CollectorControlEvent(
                context=self.context,
                kind=kind,
                receive_ts_ms=self.wall_clock_ms(),
                receive_monotonic_ns=self.monotonic_ns(),
                connection_attempt=self._attempt,
                dropped_messages=self._dropped_events,
                reason=reason,
            )
        )

    def _enqueue(self, event: DomainEvent) -> None:
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self._dropped_events += 1

    async def _writer(self) -> None:
        while True:
            event = await self._queue.get()
            if self._reported_drops < self._dropped_events:
                drop_event = CollectorControlEvent(
                    context=self.context,
                    kind=CollectorControlKind.QUEUE_DROP,
                    receive_ts_ms=self.wall_clock_ms(),
                    receive_monotonic_ns=self.monotonic_ns(),
                    connection_attempt=self._attempt,
                    dropped_messages=self._dropped_events,
                    reason="bounded persistence queue overflow",
                )
                await asyncio.to_thread(
                    self.store.append,
                    self.config.control_stream,
                    drop_event,
                )
                self._persisted_events += 1
                self._reported_drops = self._dropped_events
            if event is None:
                self._queue.task_done()
                return
            events = [event]
            stop_after_batch = False
            while len(events) < self.config.persistence_batch_size:
                try:
                    queued = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if queued is None:
                    stop_after_batch = True
                    break
                events.append(queued)
            await asyncio.to_thread(self._persist_events, events)
            self._persisted_events += len(events)
            for _ in events:
                self._queue.task_done()
            if stop_after_batch:
                self._queue.task_done()
                return

    def _persist_events(self, events: Sequence[DomainEvent]) -> None:
        groups: list[tuple[str, list[DomainEvent]]] = []
        for event in events:
            stream = (
                self.config.data_stream
                if isinstance(event, PublicMarketDataEvent)
                else self.config.control_stream
            )
            if groups and groups[-1][0] == stream:
                groups[-1][1].append(event)
            else:
                groups.append((stream, [event]))
        raw_append_many = getattr(self.store, "append_many", None)
        append_many = (
            cast(Callable[[str, Sequence[DomainEvent]], object], raw_append_many)
            if callable(raw_append_many)
            else None
        )
        for stream, group in groups:
            if append_many is not None:
                append_many(stream, group)
            else:
                for event in group:
                    self.store.append(stream, event)
