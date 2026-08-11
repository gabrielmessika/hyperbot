#!/usr/bin/env python3
"""Run a bounded public-only collector session and validate its segments."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path

from hyperbot.models import EventContext, TimeSource
from hyperbot.segmented_store import SegmentedEventStore
from hyperbot.services.collector import (
    CollectorConfig,
    PublicWebSocketCollector,
    Subscription,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coin", action="append", required=True)
    parser.add_argument(
        "--channel",
        action="append",
        choices=("l2Book", "bbo", "trades"),
        default=None,
    )
    parser.add_argument("--duration-seconds", type=float, default=10.0)
    parser.add_argument("--data-root", type=Path, default=Path("data/raw/collector"))
    return parser.parse_args()


async def _run(args: argparse.Namespace) -> dict[str, object]:
    if args.duration_seconds <= 0:
        raise ValueError("duration-seconds must be positive")
    channels = args.channel or ["l2Book", "bbo", "trades"]
    subscriptions = tuple(
        Subscription(channel, coin) for coin in args.coin for channel in channels
    )
    serialized_config = {
        "coins": sorted(args.coin),
        "channels": sorted(channels),
        "duration_seconds": args.duration_seconds,
        "public_only": True,
    }
    config_hash = hashlib.sha256(
        json.dumps(
            serialized_config,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    context = EventContext(
        f"collector-{int(time.time())}-{uuid.uuid4().hex[:8]}",
        "0.1.0",
        config_hash,
        TimeSource.EXCHANGE,
    )
    store = SegmentedEventStore(args.data_root)
    collector = PublicWebSocketCollector(
        config=CollectorConfig(subscriptions=subscriptions),
        context=context,
        store=store,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    loop.call_later(args.duration_seconds, stop.set)
    metrics = await collector.run(stop)
    store.close()
    data_validation = store.validate("public-market-data")
    control_validation = store.validate("collector-control")
    return {
        "run_id": context.run_id,
        "config_sha256": config_hash,
        "public_only": True,
        "metrics": {
            "received_messages": metrics.received_messages,
            "persisted_events": metrics.persisted_events,
            "dropped_events": metrics.dropped_events,
            "reconnects": metrics.reconnects,
            "malformed_messages": metrics.malformed_messages,
        },
        "data_validation": {
            "record_count": data_validation.record_count,
            "segment_count": data_validation.segment_count,
            "last_record_sha256": data_validation.last_record_sha256,
        },
        "control_validation": {
            "record_count": control_validation.record_count,
            "segment_count": control_validation.segment_count,
            "last_record_sha256": control_validation.last_record_sha256,
        },
    }


def main() -> int:
    result = asyncio.run(_run(_arguments()))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
