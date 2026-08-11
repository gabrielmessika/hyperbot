"""Idempotent daily quality and lossless retention maintenance."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import cast

from hyperbot import __version__
from hyperbot.ops import OPS_SCHEMA_VERSION, OpsSettings, atomic_write_json
from hyperbot.quality import (
    DailyQualityAnalyzer,
    QualityConfig,
    control_event_from_payload,
    market_event_from_payload,
    write_daily_quality_report,
)
from hyperbot.segmented_store import SegmentedEventStore


@dataclass(frozen=True, slots=True)
class MaintenanceResult:
    report_date: str
    report_json: Path
    report_markdown: Path
    compressed_segments: int
    reused: bool
    qualified_day: bool


def _valid_existing_report(json_path: Path) -> bool:
    checksum_path = json_path.with_suffix(".json.sha256")
    if not json_path.is_file() or not checksum_path.is_file():
        return False
    expected = checksum_path.read_text(encoding="ascii").split()[0]
    return hashlib.sha256(json_path.read_bytes()).hexdigest() == expected


def run_daily_maintenance(
    settings: OpsSettings,
    *,
    report_date: date,
    generated_at_ms: int,
) -> MaintenanceResult:
    """Generate one deterministic report and compress immutable raw segments."""

    settings.runtime_root.mkdir(parents=True, exist_ok=True)
    marker = settings.runtime_root / "maintenance" / f"{report_date.isoformat()}.json"
    run_id = f"ops-{report_date.strftime('%Y%m%d')}-{settings.config_sha256[:8]}"
    stem = f"quality-{report_date.isoformat()}-{run_id}"
    report_json = settings.review_root / f"{stem}.json"
    report_markdown = settings.review_root / f"{stem}.md"
    if (
        marker.is_file()
        and report_markdown.is_file()
        and _valid_existing_report(report_json)
    ):
        payload = json.loads(report_json.read_text(encoding="utf-8"))
        return MaintenanceResult(
            report_date=report_date.isoformat(),
            report_json=report_json,
            report_markdown=report_markdown,
            compressed_segments=0,
            reused=True,
            qualified_day=payload.get("qualified_day") is True,
        )

    store = SegmentedEventStore(settings.data_root)
    date_value = report_date.isoformat()
    market_events = [
        market_event_from_payload(cast(dict[str, object], record["payload"]))
        for record in store.iter_records_for_utc_date(
            "public-market-data", date_value
        )
    ]
    control_events = [
        control_event_from_payload(cast(dict[str, object], record["payload"]))
        for record in store.iter_records_for_utc_date("collector-control", date_value)
    ]
    source_context = (
        market_events[0].context
        if market_events
        else control_events[0].context
        if control_events
        else None
    )
    report = DailyQualityAnalyzer(
        QualityConfig(expected_markets=settings.markets)
    ).analyze(
        report_date=report_date,
        market_events=market_events,
        control_events=control_events,
        generated_at_ms=generated_at_ms,
        run_id=run_id,
        code_version=source_context.code_version if source_context else __version__,
        source_config_hash=(
            source_context.config_hash if source_context else settings.config_sha256
        ),
    )
    if report_json.exists() or report_markdown.exists():
        if not report_markdown.is_file() or not _valid_existing_report(report_json):
            raise RuntimeError(f"existing quality report is invalid: {report_json}")
    else:
        report_json, report_markdown = write_daily_quality_report(
            report, settings.review_root
        )

    compressed = sum(
        store.compress_closed_segments(stream)
        for stream in ("public-market-data", "collector-control")
    )
    marker_payload = {
        "schema_version": OPS_SCHEMA_VERSION,
        "report_date": date_value,
        "completed_at_ms": generated_at_ms,
        "config_sha256": settings.config_sha256,
        "report_json": str(report_json),
        "report_markdown": str(report_markdown),
        "report_sha256": hashlib.sha256(report_json.read_bytes()).hexdigest(),
        "compressed_segments": compressed,
        "qualified_day": report.qualified_day,
        "minimum_retention_days": settings.minimum_retention_days,
        "raw_data_deleted": False,
    }
    atomic_write_json(marker, marker_payload)
    atomic_write_json(
        settings.maintenance_status_path,
        {
            **marker_payload,
            "state": "completed",
            "updated_at_ms": generated_at_ms,
        },
    )
    return MaintenanceResult(
        report_date=date_value,
        report_json=report_json,
        report_markdown=report_markdown,
        compressed_segments=compressed,
        reused=False,
        qualified_day=report.qualified_day,
    )
