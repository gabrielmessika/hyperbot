#!/usr/bin/env python3
"""Generate one JSON and Markdown quality report from segmented M2 streams."""

from __future__ import annotations

import argparse
import time
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

from hyperbot.quality import (
    DailyQualityAnalyzer,
    QualityConfig,
    control_event_from_payload,
    market_event_from_payload,
    write_daily_quality_report,
)
from hyperbot.segmented_store import SegmentedEventStore


def _default_date() -> str:
    return (datetime.now(tz=UTC).date() - timedelta(days=1)).isoformat()


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=_default_date())
    parser.add_argument("--market", action="append", required=True)
    parser.add_argument("--data-root", type=Path, default=Path("data/raw/collector"))
    parser.add_argument("--output-root", type=Path, default=Path("data/reviews"))
    parser.add_argument("--stale-after-ms", type=int, default=500)
    parser.add_argument("--major-gap-ms", type=int, default=5_000)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    report_date = date.fromisoformat(args.date)
    store = SegmentedEventStore(args.data_root)
    market_events = [
        market_event_from_payload(cast(dict[str, object], record["payload"]))
        for record in store.iter_records("public-market-data")
    ]
    control_events = [
        control_event_from_payload(cast(dict[str, object], record["payload"]))
        for record in store.iter_records("collector-control")
    ]
    source_context = (
        market_events[0].context
        if market_events
        else control_events[0].context
        if control_events
        else None
    )
    report = DailyQualityAnalyzer(
        QualityConfig(
            expected_markets=tuple(args.market),
            stale_after_ms=args.stale_after_ms,
            major_gap_ms=args.major_gap_ms,
        )
    ).analyze(
        report_date=report_date,
        market_events=market_events,
        control_events=control_events,
        generated_at_ms=int(time.time() * 1000),
        run_id=f"quality-{uuid.uuid4().hex[:12]}",
        code_version=source_context.code_version if source_context else "0.1.0",
        source_config_hash=source_context.config_hash if source_context else "0" * 64,
    )
    json_path, markdown_path = write_daily_quality_report(report, args.output_root)
    print(json_path)
    print(markdown_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
