from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from hyperbot.models import EventContext, PublicMarketDataEvent, TimeSource
from hyperbot.ops_export import (
    ExportError,
    build_export_bundle,
    completed_utc_dates,
    materialize_export_store_manifests,
    verify_export_manifest,
)
from hyperbot.segmented_store import SegmentedEventStore


def test_export_contains_only_closed_selected_public_artifacts(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    stream = root / "data" / "raw" / "collector" / "public-market-data"
    reviews = root / "data" / "reviews"
    runtime = root / "runtime"
    archive_stream = root / "archive" / "collector" / "collector-control"
    stream.mkdir(parents=True)
    reviews.mkdir(parents=True)
    runtime.mkdir(parents=True)
    archive_stream.mkdir(parents=True)
    (stream / "2026-08-10-000001.jsonl.gz").write_bytes(b"closed")
    (stream / "2026-08-11-000002.jsonl.open").write_bytes(b"mutable")
    (stream / "2026-08-09-000001.jsonl.gz").write_bytes(b"older")
    (archive_stream / "2026-08-10-000001.jsonl.gz").write_bytes(b"archived")
    (reviews / "quality-2026-08-10.json").write_text("{}\n", encoding="utf-8")
    (root / ".env.hyperbot").write_text("SECRET=value\n", encoding="utf-8")
    (runtime / "collector_status.json").write_text(
        json.dumps({"state": "running", "password": "redacted"}),
        encoding="utf-8",
    )

    bundle = build_export_bundle(
        root,
        dates=("2026-08-10",),
        include_all=False,
        generated_at=datetime(2026, 8, 11, 12, tzinfo=UTC),
    )
    paths = {item.path for item in bundle.files}

    assert paths == {
        "data/raw/collector/public-market-data/2026-08-10-000001.jsonl.gz",
        "archive/collector/collector-control/2026-08-10-000001.jsonl.gz",
        "data/reviews/quality-2026-08-10.json",
    }
    manifest = json.loads(bundle.manifest_path.read_text(encoding="utf-8"))
    assert manifest["open_segments_included"] is False
    assert manifest["environment_files_included"] is False
    assert "password" not in manifest["runtime_status"]["collector_status.json"]
    assert len(verify_export_manifest(bundle.manifest_path, root)) == 3

    (reviews / "quality-2026-08-10.json").write_text("tampered", encoding="utf-8")
    with pytest.raises(ExportError, match="checksum mismatch"):
        verify_export_manifest(bundle.manifest_path, root)


def test_export_rejects_symlinks_and_manifest_traversal(tmp_path: Path) -> None:
    root = tmp_path / "shared"
    reviews = root / "data" / "reviews"
    reviews.mkdir(parents=True)
    target = tmp_path / "outside"
    target.write_text("outside", encoding="utf-8")
    (reviews / "quality-2026-08-10.json").symlink_to(target)

    with pytest.raises(ExportError, match="symlinks are forbidden"):
        build_export_bundle(
            root,
            dates=("2026-08-10",),
            include_all=False,
            generated_at=datetime(2026, 8, 11, tzinfo=UTC),
        )

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "public_only": True,
                "files": [{"path": "../outside", "size_bytes": 7, "sha256": "0" * 64}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ExportError, match="escapes local root"):
        verify_export_manifest(manifest, root)


def test_completed_dates_never_include_current_utc_day() -> None:
    assert completed_utc_dates(today=date(2026, 8, 11), days=3) == (
        "2026-08-10",
        "2026-08-09",
        "2026-08-08",
    )


def test_export_materializes_replayable_store_manifest_snapshot(
    tmp_path: Path,
) -> None:
    root = tmp_path / "shared"
    store_root = root / "data" / "raw" / "collector"
    timestamp_ms = int(datetime(2026, 8, 10, 12, tzinfo=UTC).timestamp() * 1_000)
    store = SegmentedEventStore(
        store_root,
        fsync=False,
        clock_ms=lambda: timestamp_ms,
    )
    store.append(
        "public-market-data",
        PublicMarketDataEvent(
            context=EventContext(
                "export-replay",
                f"0.1.0+g{'a' * 40}",
                "b" * 64,
                TimeSource.EXCHANGE,
            ),
            channel="bbo",
            coin="BTC",
            exchange_ts_ms=timestamp_ms - 10,
            receive_ts_ms=timestamp_ms,
            receive_monotonic_ns=timestamp_ms * 1_000_000,
            local_sequence=0,
            payload_json='{"coin":"BTC","time":1786363199990}',
        ),
    )
    store.close("public-market-data")
    bundle = build_export_bundle(
        root,
        dates=("2026-08-10",),
        include_all=False,
        generated_at=datetime(2026, 8, 11, 12, tzinfo=UTC),
    )
    payload_root = tmp_path / "payload"
    for item in bundle.files:
        source = root / item.path
        destination = payload_root / item.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())

    assert len(verify_export_manifest(bundle.manifest_path, payload_root)) == 1
    paths = materialize_export_store_manifests(
        bundle.manifest_path,
        payload_root,
    )
    fetched_store = SegmentedEventStore(
        payload_root / "data" / "raw" / "collector",
        fsync=False,
    )

    assert len(paths) == 1
    assert [
        record["sequence"]
        for record in fetched_store.iter_records_for_utc_date(
            "public-market-data",
            "2026-08-10",
        )
    ] == [0]
