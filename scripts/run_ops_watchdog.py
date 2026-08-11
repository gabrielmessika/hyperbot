#!/usr/bin/env python3
"""Monitor collector health and emit state-change/cooldown webhook alerts."""

from __future__ import annotations

import json
import os
import signal
import sys
import time

from hyperbot.ops import (
    OpsConfigurationError,
    OpsSettings,
    atomic_write_json,
    evaluate_collector_health,
)
from hyperbot.watchdog import (
    WatchdogSettings,
    alert_payload,
    default_alert_transport,
    should_alert,
    watchdog_status,
)


def main() -> int:
    try:
        settings = OpsSettings.from_environment(require_enabled=True)
        watchdog = WatchdogSettings.from_environment(os.environ)
    except OpsConfigurationError as exc:
        print(f"unsafe watchdog configuration: {exc}", file=sys.stderr)
        return 2
    stopped = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    started_at_ms = int(time.time() * 1_000)
    previous_healthy: bool | None = None
    last_alert_at_ms: int | None = None
    while not stopped:
        now_ms = int(time.time() * 1_000)
        result = evaluate_collector_health(settings, now_ms=now_ms)
        send_alert = should_alert(
            healthy=result.healthy,
            previous_healthy=previous_healthy,
            now_ms=now_ms,
            started_at_ms=started_at_ms,
            last_alert_at_ms=last_alert_at_ms,
            settings=watchdog,
        )
        sent = False
        delivery_error: str | None = None
        if send_alert:
            last_alert_at_ms = now_ms
        if send_alert and watchdog.webhook_url is not None:
            try:
                default_alert_transport(
                    watchdog.webhook_url,
                    alert_payload(result, recovered=result.healthy),
                )
                sent = True
            except Exception as exc:
                delivery_error = f"{type(exc).__name__}: {exc}"
        status = watchdog_status(
            result,
            alert_configured=watchdog.webhook_url is not None,
            alert_sent=sent,
            delivery_error=delivery_error,
        )
        atomic_write_json(settings.runtime_root / "watchdog_status.json", status)
        if send_alert or result.healthy != previous_healthy:
            print(json.dumps(status, sort_keys=True))
        previous_healthy = result.healthy
        deadline = time.monotonic() + watchdog.interval_seconds
        while not stopped and time.monotonic() < deadline:
            time.sleep(min(1.0, max(deadline - time.monotonic(), 0.0)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
