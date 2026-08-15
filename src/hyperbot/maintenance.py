"""Idempotent daily quality and lossless retention maintenance."""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import chain
from pathlib import Path
from typing import cast

from hyperbot.ops import OPS_SCHEMA_VERSION, OpsSettings, atomic_write_json
from hyperbot.quality import (
    QUALITY_SCHEMA_VERSION,
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
    archived_segments: int
    archived_bytes: int
    reused: bool
    qualified_day: bool


def deduplicate_automatic_target(
    target: date | None,
    last_attempted: date | None,
    *,
    once: bool,
) -> date | None:
    """Run each scheduled UTC date once per long-lived process."""

    if once or target is None or target != last_attempted:
        return target
    return None


def _valid_existing_report(json_path: Path) -> bool:
    checksum_path = json_path.with_suffix(".json.sha256")
    if (
        json_path.is_symlink()
        or checksum_path.is_symlink()
        or not json_path.is_file()
        or not checksum_path.is_file()
    ):
        return False
    expected = checksum_path.read_text(encoding="ascii").split()[0]
    return hashlib.sha256(json_path.read_bytes()).hexdigest() == expected


def _reuse_completed_day(
    settings: OpsSettings,
    marker: Path,
    *,
    report_date: date,
    generated_at_ms: int,
) -> MaintenanceResult | None:
    if marker.is_symlink() or not marker.is_file():
        return None
    try:
        marker_payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(marker_payload, dict):
        return None
    if marker_payload.get("report_date") != report_date.isoformat():
        return None
    raw_json = marker_payload.get("report_json")
    raw_markdown = marker_payload.get("report_markdown")
    expected_hash = marker_payload.get("report_sha256")
    if not all(isinstance(value, str) for value in (raw_json, raw_markdown)):
        return None
    report_json = Path(cast(str, raw_json))
    report_markdown = Path(cast(str, raw_markdown))
    try:
        review_root = settings.review_root.resolve(strict=True)
        resolved_json = report_json.resolve(strict=True)
        resolved_markdown = report_markdown.resolve(strict=True)
    except OSError:
        return None
    if (
        not resolved_json.is_relative_to(review_root)
        or not resolved_markdown.is_relative_to(review_root)
        or report_markdown.is_symlink()
        or not report_markdown.is_file()
        or not _valid_existing_report(report_json)
        or not isinstance(expected_hash, str)
        or hashlib.sha256(report_json.read_bytes()).hexdigest() != expected_hash
    ):
        return None
    payload = json.loads(report_json.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    archived_segments = marker_payload.get("archived_segments", 0)
    if not isinstance(archived_segments, int) or isinstance(archived_segments, bool):
        archived_segments = 0
    archived_bytes = marker_payload.get("archived_bytes", 0)
    if not isinstance(archived_bytes, int) or isinstance(archived_bytes, bool):
        archived_bytes = 0
    atomic_write_json(
        settings.maintenance_status_path,
        {
            **marker_payload,
            "schema_version": OPS_SCHEMA_VERSION,
            "state": "completed",
            "updated_at_ms": generated_at_ms,
            "reused": True,
            "active_config_sha256": settings.config_sha256,
            "archive_enabled": settings.archive_enabled,
            "hot_retention_days": settings.hot_retention_days,
            "archived_segments": archived_segments,
            "archived_bytes": archived_bytes,
            "raw_data_deleted": False,
        },
    )
    return MaintenanceResult(
        report_date=report_date.isoformat(),
        report_json=report_json,
        report_markdown=report_markdown,
        compressed_segments=0,
        archived_segments=archived_segments,
        archived_bytes=archived_bytes,
        reused=True,
        qualified_day=payload.get("qualified_day") is True,
    )


def _recover_existing_quality_report(
    *,
    report_json: Path,
    report_markdown: Path,
    report_date: date,
    run_id: str,
    quality_config_hash: str,
) -> tuple[int, bool] | None:
    paths_exist = any(
        path.exists() or path.is_symlink()
        for path in (
            report_json,
            report_json.with_suffix(".json.sha256"),
            report_markdown,
        )
    )
    if not paths_exist:
        return None
    if (
        report_markdown.is_symlink()
        or not report_markdown.is_file()
        or not _valid_existing_report(report_json)
    ):
        raise RuntimeError(f"existing quality report is invalid: {report_json}")
    try:
        payload = json.loads(report_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f"existing quality report is invalid: {report_json}"
        ) from exc
    expected_fields = {
        "schema_version": QUALITY_SCHEMA_VERSION,
        "report_date": report_date.isoformat(),
        "run_id": run_id,
        "quality_config_hash": quality_config_hash,
        "dataset_tier": "A",
    }
    if not isinstance(payload, dict) or any(
        payload.get(field) != expected for field, expected in expected_fields.items()
    ):
        raise RuntimeError(f"existing quality report is incompatible: {report_json}")
    qualified_day = payload.get("qualified_day")
    markets = payload.get("markets")
    if not isinstance(qualified_day, bool) or not isinstance(markets, list):
        raise RuntimeError(f"existing quality report is invalid: {report_json}")
    processed_market_events = 0
    for market in markets:
        if not isinstance(market, dict):
            raise RuntimeError(f"existing quality report is invalid: {report_json}")
        message_count = market.get("message_count")
        if (
            not isinstance(message_count, int)
            or isinstance(message_count, bool)
            or message_count < 0
        ):
            raise RuntimeError(f"existing quality report is invalid: {report_json}")
        processed_market_events += message_count
    return processed_market_events, qualified_day


def run_daily_maintenance(
    settings: OpsSettings,
    *,
    report_date: date,
    generated_at_ms: int,
) -> MaintenanceResult:
    """Generate one deterministic report and compress immutable raw segments."""

    generated_date = datetime.fromtimestamp(generated_at_ms / 1_000, tz=UTC).date()
    if report_date >= generated_date:
        raise ValueError("report_date must be a completed UTC day")
    settings.runtime_root.mkdir(parents=True, exist_ok=True)
    marker = settings.runtime_root / "maintenance" / f"{report_date.isoformat()}.json"
    reused = _reuse_completed_day(
        settings,
        marker,
        report_date=report_date,
        generated_at_ms=generated_at_ms,
    )
    if reused is not None:
        return reused
    run_id = f"ops-{report_date.strftime('%Y%m%d')}-{settings.config_sha256[:8]}"
    stem = f"quality-{report_date.isoformat()}-{run_id}"
    report_json = settings.review_root / f"{stem}.json"
    report_markdown = settings.review_root / f"{stem}.md"
    store = SegmentedEventStore(
        settings.data_root,
        archive_root=settings.archive_root,
    )
    date_value = report_date.isoformat()
    started_at_ms = generated_at_ms
    processed_market_events = 0
    expected_book_channels = tuple(
        (
            market,
            tuple(
                channel
                for channel, configured_market in settings.subscriptions
                if configured_market == market and channel in {"l2Book", "bbo"}
            ),
        )
        for market in settings.markets
    )
    quality_config = QualityConfig(
        expected_markets=settings.markets,
        expected_book_channels=expected_book_channels,
    )
    recovered_report = _recover_existing_quality_report(
        report_json=report_json,
        report_markdown=report_markdown,
        report_date=report_date,
        run_id=run_id,
        quality_config_hash=quality_config.sha256,
    )
    report_reused = recovered_report is not None
    qualified_day = recovered_report[1] if recovered_report is not None else False
    if recovered_report is not None:
        processed_market_events = recovered_report[0]

    def update_running_status(
        *,
        stage: str,
        processed_events: int | None = None,
        compressed_segments: int = 0,
        archived_segments: int = 0,
        archived_bytes: int = 0,
    ) -> None:
        effective_processed_events = (
            processed_market_events if processed_events is None else processed_events
        )
        atomic_write_json(
            settings.maintenance_status_path,
            {
                "schema_version": OPS_SCHEMA_VERSION,
                "state": "running",
                "stage": stage,
                "report_date": date_value,
                "run_id": run_id,
                "started_at_ms": started_at_ms,
                "updated_at_ms": int(time.time() * 1_000),
                "processed_market_events": effective_processed_events,
                "compressed_segments": compressed_segments,
                "archived_segments": archived_segments,
                "archived_bytes": archived_bytes,
                "archive_enabled": settings.archive_enabled,
                "hot_retention_days": settings.hot_retention_days,
                "config_sha256": settings.config_sha256,
                "minimum_retention_days": settings.minimum_retention_days,
                "raw_data_deleted": False,
            },
        )

    update_running_status(stage="segment_finalization")
    cutoff_utc_date = (report_date + timedelta(days=1)).isoformat()
    for stream in ("public-market-data", "collector-control"):
        store.finalize_active_before_utc_date(stream, cutoff_utc_date)

    if recovered_report is None:
        update_running_status(stage="quality_analysis")
        raw_market_events = (
            market_event_from_payload(cast(dict[str, object], record["payload"]))
            for record in store.iter_records_for_utc_date(
                "public-market-data", date_value
            )
        )
        control_events = [
            control_event_from_payload(cast(dict[str, object], record["payload"]))
            for record in store.iter_records_for_utc_date(
                "collector-control", date_value
            )
        ]
        first_market_event = next(raw_market_events, None)
        market_events = (
            chain((first_market_event,), raw_market_events)
            if first_market_event is not None
            else iter(())
        )
        source_context = (
            first_market_event.context
            if first_market_event is not None
            else control_events[0].context
            if control_events
            else None
        )

        def report_progress(processed_events: int) -> None:
            nonlocal processed_market_events
            processed_market_events = processed_events
            update_running_status(
                stage="quality_analysis",
                processed_events=processed_market_events,
            )

        report = DailyQualityAnalyzer(quality_config).analyze_ordered(
            report_date=report_date,
            market_events=market_events,
            control_events=control_events,
            generated_at_ms=generated_at_ms,
            run_id=run_id,
            code_version=(
                source_context.code_version if source_context else settings.code_version
            ),
            source_config_hash=(
                source_context.config_hash if source_context else settings.config_sha256
            ),
            spill_root=settings.runtime_root / "quality-spill",
            progress=report_progress,
        )
        report_json, report_markdown = write_daily_quality_report(
            report, settings.review_root
        )
        qualified_day = report.qualified_day

    compressed = 0
    update_running_status(
        stage="compression",
        processed_events=processed_market_events,
        compressed_segments=compressed,
    )
    for stream in ("public-market-data", "collector-control"):
        compressed_before_stream = compressed

        def compression_progress(
            stream_count: int,
            base_count: int = compressed_before_stream,
        ) -> None:
            update_running_status(
                stage="compression",
                processed_events=processed_market_events,
                compressed_segments=base_count + stream_count,
            )

        compressed += store.compress_closed_segments(
            stream,
            progress=compression_progress,
        )
    archived_segments = 0
    archived_bytes = 0
    if settings.archive_enabled:
        archive_cutoff = (
            report_date - timedelta(days=settings.hot_retention_days - 1)
        ).isoformat()
        update_running_status(
            stage="archival",
            processed_events=processed_market_events,
            compressed_segments=compressed,
            archived_segments=archived_segments,
            archived_bytes=archived_bytes,
        )
        for stream in ("public-market-data", "collector-control"):
            base_segments = archived_segments
            base_bytes = archived_bytes

            def archive_progress(
                stream_segments: int,
                stream_bytes: int,
                segment_base: int = base_segments,
                byte_base: int = base_bytes,
            ) -> None:
                update_running_status(
                    stage="archival",
                    processed_events=processed_market_events,
                    compressed_segments=compressed,
                    archived_segments=segment_base + stream_segments,
                    archived_bytes=byte_base + stream_bytes,
                )

            archive_result = store.archive_closed_segments_before(
                stream,
                archive_cutoff,
                progress=archive_progress,
            )
            archived_segments += archive_result.segment_count
            archived_bytes += archive_result.archived_bytes
    marker_payload = {
        "schema_version": OPS_SCHEMA_VERSION,
        "report_date": date_value,
        "completed_at_ms": generated_at_ms,
        "started_at_ms": started_at_ms,
        "config_sha256": settings.config_sha256,
        "report_json": str(report_json),
        "report_markdown": str(report_markdown),
        "report_sha256": hashlib.sha256(report_json.read_bytes()).hexdigest(),
        "compressed_segments": compressed,
        "archived_segments": archived_segments,
        "archived_bytes": archived_bytes,
        "archive_enabled": settings.archive_enabled,
        "hot_retention_days": settings.hot_retention_days,
        "processed_market_events": processed_market_events,
        "qualified_day": qualified_day,
        "report_reused": report_reused,
        "minimum_retention_days": settings.minimum_retention_days,
        "raw_data_deleted": False,
    }
    atomic_write_json(marker, marker_payload)
    atomic_write_json(
        settings.maintenance_status_path,
        {
            **marker_payload,
            "state": "completed",
            "updated_at_ms": int(time.time() * 1_000),
        },
    )
    return MaintenanceResult(
        report_date=date_value,
        report_json=report_json,
        report_markdown=report_markdown,
        compressed_segments=compressed,
        archived_segments=archived_segments,
        archived_bytes=archived_bytes,
        reused=report_reused,
        qualified_day=qualified_day,
    )
