"""Signal-friendly runtime wrapper for the public-only collector."""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Callable

from hyperbot.models import EventContext, TimeSource
from hyperbot.ops import OPS_SCHEMA_VERSION, OpsSettings, atomic_write_json
from hyperbot.segmented_store import SegmentedEventStore
from hyperbot.services.public_collector import (
    CollectorConfig,
    CollectorMetrics,
    ConnectionFactory,
    PublicWebSocketCollector,
    Subscription,
)


def _status_payload(
    settings: OpsSettings,
    metrics: CollectorMetrics,
    *,
    state: str,
    run_id: str,
    started_at_ms: int,
    updated_at_ms: int,
    error: str | None = None,
) -> dict[str, object]:
    return {
        "schema_version": OPS_SCHEMA_VERSION,
        "state": state,
        "run_id": run_id,
        "code_version": settings.code_version,
        "code_commit": settings.code_commit,
        "config_sha256": settings.config_sha256,
        "started_at_ms": started_at_ms,
        "updated_at_ms": updated_at_ms,
        "public_only": True,
        "live_enabled": settings.live_enabled,
        "shadow_only": settings.shadow_only,
        "markets": list(settings.markets),
        "channels": list(settings.channels),
        "depth_markets": list(settings.depth_markets),
        "breadth_markets": list(settings.breadth_markets),
        "subscription_count": len(settings.subscriptions),
        "persistence_batch_size": settings.persistence_batch_size,
        "fsync_every_records": settings.fsync_every_records,
        "data_mount_guard_enabled": settings.data_mount_sentinel is not None,
        "archive_enabled": settings.archive_enabled,
        "archive_mount_guard_enabled": settings.archive_mount_sentinel is not None,
        "received_messages": metrics.received_messages,
        "persisted_events": metrics.persisted_events,
        "dropped_events": metrics.dropped_events,
        "reconnects": metrics.reconnects,
        "malformed_messages": metrics.malformed_messages,
        "connected": metrics.connected,
        "last_message_receive_ms": metrics.last_message_receive_ms,
        "persistence_queue_depth": metrics.persistence_queue_depth,
        "persistence_queue_capacity": metrics.persistence_queue_capacity,
        "error": error,
    }


async def run_collector_service(
    settings: OpsSettings,
    stop: asyncio.Event,
    *,
    connection_factory: ConnectionFactory | None = None,
    wall_clock_ms: Callable[[], int] | None = None,
) -> CollectorMetrics:
    """Run one durable collector service and publish atomic health state."""

    if not settings.collector_enabled:
        raise ValueError("collector service cannot run while disabled")
    clock_ms = wall_clock_ms or (lambda: int(time.time() * 1000))
    started_at_ms = clock_ms()
    run_id = f"collector-ops-{started_at_ms}-{uuid.uuid4().hex[:8]}"
    context = EventContext(
        run_id,
        settings.code_version,
        settings.config_sha256,
        TimeSource.EXCHANGE,
    )
    store = SegmentedEventStore(
        settings.data_root,
        fsync_every_records=settings.fsync_every_records,
        always_fsync_streams=frozenset({"collector-control"}),
    )
    config = CollectorConfig(
        subscriptions=tuple(
            Subscription(channel, market) for channel, market in settings.subscriptions
        ),
        queue_capacity=settings.queue_capacity,
        persistence_batch_size=settings.persistence_batch_size,
        heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
        stale_after_seconds=settings.stale_after_seconds,
        reconnect_initial_seconds=settings.reconnect_initial_seconds,
        reconnect_max_seconds=settings.reconnect_max_seconds,
    )
    if connection_factory is None:
        collector = PublicWebSocketCollector(
            config=config,
            context=context,
            store=store,
            wall_clock_ms=wall_clock_ms,
        )
    else:
        collector = PublicWebSocketCollector(
            config=config,
            context=context,
            store=store,
            connection_factory=connection_factory,
            wall_clock_ms=wall_clock_ms,
        )

    async def publish_status() -> None:
        while not stop.is_set():
            atomic_write_json(
                settings.status_path,
                _status_payload(
                    settings,
                    collector.metrics,
                    state="running",
                    run_id=run_id,
                    started_at_ms=started_at_ms,
                    updated_at_ms=clock_ms(),
                ),
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    stop.wait(), timeout=settings.status_interval_seconds
                )

    monitor = asyncio.create_task(publish_status(), name="hyperbot-status-writer")
    try:
        metrics = await collector.run(stop)
    except BaseException as exc:
        atomic_write_json(
            settings.status_path,
            _status_payload(
                settings,
                collector.metrics,
                state="failed",
                run_id=run_id,
                started_at_ms=started_at_ms,
                updated_at_ms=clock_ms(),
                error=f"{type(exc).__name__}: {exc}",
            ),
        )
        raise
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
        store.close()
    atomic_write_json(
        settings.status_path,
        _status_payload(
            settings,
            metrics,
            state="stopped",
            run_id=run_id,
            started_at_ms=started_at_ms,
            updated_at_ms=clock_ms(),
        ),
    )
    return metrics
