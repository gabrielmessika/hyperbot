from __future__ import annotations

import json
from pathlib import Path

import pytest

from hyperbot.event_store import EventIntegrityError
from hyperbot.models import (
    EventContext,
    PublicMarketDataEvent,
    TimeSource,
)
from hyperbot.segmented_store import SegmentedEventStore


def _event(sequence: int) -> PublicMarketDataEvent:
    return PublicMarketDataEvent(
        context=EventContext("store-test", "test", "b" * 64, TimeSource.EXCHANGE),
        channel="bbo",
        coin="BTC",
        exchange_ts_ms=1_700_000_000_000 + sequence,
        receive_ts_ms=1_700_000_000_100 + sequence,
        receive_monotonic_ns=10_000 + sequence,
        local_sequence=sequence,
        payload_json=json.dumps({"coin": "BTC", "time": sequence}),
    )


def test_segment_rotation_manifest_and_compression_preserve_replay(
    tmp_path: Path,
) -> None:
    timestamps = iter(
        [
            1_700_000_000_000,
            1_700_000_000_001,
            1_700_086_400_000,
        ]
    )
    store = SegmentedEventStore(
        tmp_path,
        max_segment_bytes=1,
        fsync=False,
        clock_ms=lambda: next(timestamps),
    )
    for index in range(3):
        store.append("market-data", _event(index))
    store.close("market-data")

    validation = store.validate("market-data")
    before = list(store.iter_records("market-data"))
    manifest = json.loads(
        (tmp_path / "market-data" / "manifest.json").read_text(encoding="utf-8")
    )

    assert validation.segment_count == 3
    assert validation.record_count == 3
    assert [segment["record_count"] for segment in manifest["segments"]] == [1, 1, 1]
    assert manifest["segments"][0]["begin_recorded_at_ms"] == 1_700_000_000_000
    assert manifest["segments"][2]["end_recorded_at_ms"] == 1_700_086_400_000
    assert store.compress_closed_segments("market-data") == 3
    after = list(store.iter_records("market-data"))
    assert after == before
    assert all(
        segment["compression"] == "gzip"
        for segment in json.loads(
            (tmp_path / "market-data" / "manifest.json").read_text(encoding="utf-8")
        )["segments"]
    )


def test_utc_date_rotates_even_when_size_limit_is_not_reached(
    tmp_path: Path,
) -> None:
    timestamps = iter([1_700_000_000_000, 1_700_086_400_000])
    store = SegmentedEventStore(
        tmp_path,
        max_segment_bytes=10_000_000,
        fsync=False,
        clock_ms=lambda: next(timestamps),
    )
    store.append("market-data", _event(0))
    store.append("market-data", _event(1))
    store.close("market-data")

    manifest = json.loads(
        (tmp_path / "market-data" / "manifest.json").read_text(encoding="utf-8")
    )
    assert len(manifest["segments"]) == 2
    assert manifest["segments"][0]["utc_date"] != manifest["segments"][1]["utc_date"]

    first_date = manifest["segments"][0]["utc_date"]
    second_date = manifest["segments"][1]["utc_date"]
    first = list(store.iter_records_for_utc_date("market-data", first_date))
    second = list(store.iter_records_for_utc_date("market-data", second_date))

    assert [record["sequence"] for record in first] == [0]
    assert [record["sequence"] for record in second] == [1]


def test_date_reader_refuses_a_mutable_active_segment(tmp_path: Path) -> None:
    store = SegmentedEventStore(
        tmp_path,
        fsync=False,
        clock_ms=lambda: 1_700_000_000_000,
    )
    store.append("market-data", _event(0))

    with pytest.raises(EventIntegrityError, match="still has an active segment"):
        list(store.iter_records_for_utc_date("market-data", "2023-11-14"))


def test_compression_ignores_an_active_segment(tmp_path: Path) -> None:
    store = SegmentedEventStore(tmp_path, fsync=False, clock_ms=lambda: 1000)
    active = store.append("market-data", _event(0)).path

    assert store.compress_closed_segments("market-data") == 0
    assert active.exists()
    assert active.suffix == ".open"


def test_partial_active_line_is_removed_without_rewriting_valid_bytes(
    tmp_path: Path,
) -> None:
    store = SegmentedEventStore(tmp_path, fsync=False, clock_ms=lambda: 1000)
    result = store.append("market-data", _event(0))
    valid_prefix = result.path.read_bytes()
    with result.path.open("ab") as handle:
        handle.write(b'{"incomplete"')

    recovered = SegmentedEventStore(tmp_path, fsync=False, clock_ms=lambda: 1001)
    recovered.append("market-data", _event(1))
    content = result.path.read_bytes()

    assert content.startswith(valid_prefix)
    assert len(recovered.read_records("market-data")) == 2


def test_append_cache_avoids_rescanning_unchanged_active_segment(
    tmp_path: Path,
) -> None:
    store = SegmentedEventStore(tmp_path, fsync=False, clock_ms=lambda: 1000)
    original = store._recover_active
    recovery_calls = 0

    def counted_recovery(
        stream: str,
        manifest: dict[str, object],
    ) -> object:
        nonlocal recovery_calls
        recovery_calls += 1
        return original(stream, manifest)  # type: ignore[arg-type]

    store._recover_active = counted_recovery  # type: ignore[method-assign,assignment]
    for sequence in range(100):
        store.append("market-data", _event(sequence))

    assert recovery_calls == 1
    assert store.validate("market-data").record_count == 100


def test_group_commit_batches_market_fsync_but_can_force_control_streams(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fsync_calls: list[int] = []
    monkeypatch.setattr(
        "hyperbot.segmented_store.os.fsync",
        lambda descriptor: fsync_calls.append(descriptor),
    )
    batched = SegmentedEventStore(
        tmp_path / "batched",
        fsync_every_records=3,
        clock_ms=lambda: 1000,
    )

    batched.append("market-data", _event(0))
    batched.append("market-data", _event(1))
    assert fsync_calls == []
    batched.append("market-data", _event(2))
    assert len(fsync_calls) == 1
    batched.append("market-data", _event(3))
    batched.close("market-data")
    assert len(fsync_calls) > 1

    forced = SegmentedEventStore(
        tmp_path / "forced",
        fsync_every_records=100,
        always_fsync_streams=frozenset({"market-data"}),
        clock_ms=lambda: 1000,
    )
    before_forced = len(fsync_calls)
    forced.append("market-data", _event(0))
    assert len(fsync_calls) == before_forced + 1


def test_batch_append_preserves_record_order_and_integrity(tmp_path: Path) -> None:
    store = SegmentedEventStore(
        tmp_path,
        fsync=False,
        clock_ms=iter((1000, 1001, 1002)).__next__,
    )

    results = store.append_many(
        "market-data",
        tuple(_event(sequence) for sequence in range(3)),
    )
    store.close("market-data")
    records = store.read_records("market-data")

    assert len(results) == 3
    assert [record["sequence"] for record in records] == [0, 1, 2]
    assert [record["payload"]["local_sequence"] for record in records] == [
        0,
        1,
        2,
    ]
    assert store.validate("market-data").record_count == 3


def test_append_cache_is_invalidated_by_an_external_writer(tmp_path: Path) -> None:
    first = SegmentedEventStore(tmp_path, fsync=False, clock_ms=lambda: 1000)
    second = SegmentedEventStore(tmp_path, fsync=False, clock_ms=lambda: 1001)

    first.append("market-data", _event(0))
    second.append("market-data", _event(1))
    first.append("market-data", _event(2))

    assert [record["sequence"] for record in first.read_records("market-data")] == [
        0,
        1,
        2,
    ]


def test_close_detects_same_size_active_corruption_with_append_cache(
    tmp_path: Path,
) -> None:
    store = SegmentedEventStore(tmp_path, fsync=False, clock_ms=lambda: 1000)
    active = store.append("market-data", _event(0)).path
    content = bytearray(active.read_bytes())
    content[len(content) // 2] ^= 1
    active.write_bytes(content)

    with pytest.raises(EventIntegrityError):
        store.close("market-data")


def _closed_store(tmp_path: Path) -> tuple[SegmentedEventStore, Path]:
    store = SegmentedEventStore(tmp_path, fsync=False, clock_ms=lambda: 1000)
    store.append("market-data", _event(0))
    store.append("market-data", _event(1))
    store.close("market-data")
    manifest = json.loads(
        (tmp_path / "market-data" / "manifest.json").read_text(encoding="utf-8")
    )
    return store, tmp_path / "market-data" / manifest["segments"][0]["path"]


def test_closed_segment_deletion_is_detected(tmp_path: Path) -> None:
    store, segment = _closed_store(tmp_path)
    segment.unlink()
    with pytest.raises(EventIntegrityError, match="missing segment"):
        store.validate("market-data")


def test_closed_segment_truncation_is_detected(tmp_path: Path) -> None:
    store, segment = _closed_store(tmp_path)
    with segment.open("r+b") as handle:
        handle.truncate(segment.stat().st_size - 10)
    with pytest.raises(EventIntegrityError, match="checksum mismatch"):
        store.validate("market-data")


def test_closed_segment_corruption_is_detected(tmp_path: Path) -> None:
    store, segment = _closed_store(tmp_path)
    content = bytearray(segment.read_bytes())
    content[len(content) // 2] ^= 1
    segment.write_bytes(content)
    with pytest.raises(EventIntegrityError, match="checksum mismatch"):
        store.validate("market-data")
