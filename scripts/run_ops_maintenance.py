#!/usr/bin/env python3
"""Run automatic UTC quality reports and lossless segment compression."""

from __future__ import annotations

import argparse
import json
import signal
import sys
import time
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta

from hyperbot.maintenance import deduplicate_automatic_target, run_daily_maintenance
from hyperbot.ops import OpsConfigurationError, OpsSettings, atomic_write_json


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--date", type=date.fromisoformat)
    return parser.parse_args()


def _scheduled_date(settings: OpsSettings, now: datetime) -> date | None:
    scheduled = now.replace(
        hour=settings.maintenance_hour_utc,
        minute=settings.maintenance_minute_utc,
        second=0,
        microsecond=0,
    )
    if now < scheduled:
        return None
    return now.date() - timedelta(days=1)


def _automatic_attempt_blocked(settings: OpsSettings, target: date) -> bool:
    """Do not repeat an interrupted daily attempt until an operator retries it."""

    marker = settings.runtime_root / "maintenance" / f"{target.isoformat()}.json"
    if marker.is_file() and not marker.is_symlink():
        return False
    try:
        status = json.loads(
            settings.maintenance_status_path.read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return False
    return (
        isinstance(status, dict)
        and status.get("report_date") == target.isoformat()
        and status.get("state") in {"running", "failed"}
    )


def main() -> int:
    args = _arguments()
    try:
        settings = OpsSettings.from_environment(require_enabled=True)
    except OpsConfigurationError as exc:
        print(f"unsafe maintenance configuration: {exc}", file=sys.stderr)
        return 2
    stopped = False

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
    reported_blocked_dates: set[date] = set()
    last_attempted_automatic_date: date | None = None
    while not stopped:
        now = datetime.now(tz=UTC)
        target = args.date or _scheduled_date(settings, now)
        target = deduplicate_automatic_target(
            target,
            last_attempted_automatic_date,
            once=args.once,
        )
        if (
            target is not None
            and not args.once
            and _automatic_attempt_blocked(settings, target)
        ):
            if target not in reported_blocked_dates:
                print(
                    "maintenance retry blocked after an interrupted attempt; "
                    f"operator review required for {target.isoformat()}",
                    file=sys.stderr,
                )
                reported_blocked_dates.add(target)
            target = None
        if target is not None:
            if not args.once:
                last_attempted_automatic_date = target
            try:
                result = run_daily_maintenance(
                    settings,
                    report_date=target,
                    generated_at_ms=int(now.timestamp() * 1_000),
                )
                print(json.dumps(asdict(result), default=str, sort_keys=True))
            except Exception as exc:
                atomic_write_json(
                    settings.maintenance_status_path,
                    {
                        "state": "failed",
                        "updated_at_ms": int(now.timestamp() * 1_000),
                        "report_date": target.isoformat(),
                        "config_sha256": settings.config_sha256,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                print(
                    f"maintenance failed: {type(exc).__name__}: {exc}",
                    file=sys.stderr,
                )
                if args.once:
                    return 1
        if args.once:
            return 0
        deadline = time.monotonic() + settings.maintenance_poll_seconds
        while not stopped and time.monotonic() < deadline:
            time.sleep(min(1.0, max(deadline - time.monotonic(), 0.0)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
