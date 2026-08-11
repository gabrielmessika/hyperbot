#!/usr/bin/env python3
"""Run the explicitly enabled public-only HyperBot collector service."""

from __future__ import annotations

import asyncio
import json
import signal
import sys

from hyperbot.ops import OpsConfigurationError, OpsSettings
from hyperbot.services.collector_runtime import run_collector_service


async def _run() -> int:
    settings = OpsSettings.from_environment(require_enabled=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for current_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(current_signal, stop.set)
    metrics = await run_collector_service(settings, stop)
    print(
        json.dumps(
            {
                "received_messages": metrics.received_messages,
                "persisted_events": metrics.persisted_events,
                "dropped_events": metrics.dropped_events,
                "reconnects": metrics.reconnects,
                "malformed_messages": metrics.malformed_messages,
                "public_only": True,
            },
            sort_keys=True,
        )
    )
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except (OpsConfigurationError, ValueError) as exc:
        print(f"unsafe collector configuration: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
