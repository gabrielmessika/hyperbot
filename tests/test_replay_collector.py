from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from hyperbot.models import EventContext, PublicMarketDataEvent, Side, TimeSource
from hyperbot.replay import (
    CollectorReplayError,
    ReplayBook,
    ReplayTrade,
    build_collector_replay_dataset,
    collector_replay_dataset_payload,
    generate_top_of_book_probes,
    read_collector_replay_dataset,
    write_collector_replay_dataset,
)
from hyperbot.segmented_store import SegmentedEventStore

DAY = int(datetime(2026, 8, 16, tzinfo=UTC).timestamp() * 1_000)
SOURCE_COMMIT = "a" * 40
BUILDER_COMMIT = "b" * 40
SOURCE_CODE_VERSION = f"0.1.0+g{SOURCE_COMMIT}"
BUILDER_CODE_VERSION = f"0.1.0+g{BUILDER_COMMIT}"
CONFIG_HASH = "c" * 64


def _context(*, config_hash: str = CONFIG_HASH) -> EventContext:
    return EventContext(
        run_id="collector-qualified-day",
        code_version=SOURCE_CODE_VERSION,
        config_hash=config_hash,
        time_source=TimeSource.EXCHANGE,
    )


def _event(
    *,
    channel: str,
    exchange_ts_ms: int,
    sequence: int,
    payload: dict[str, object],
    config_hash: str = CONFIG_HASH,
) -> PublicMarketDataEvent:
    return PublicMarketDataEvent(
        context=_context(config_hash=config_hash),
        channel=channel,
        coin="BTC",
        exchange_ts_ms=exchange_ts_ms,
        receive_ts_ms=exchange_ts_ms + 100,
        receive_monotonic_ns=(exchange_ts_ms + 100) * 1_000_000,
        local_sequence=sequence,
        payload_json=json.dumps(payload, separators=(",", ":"), sort_keys=True),
    )


def _book_payload(timestamp_ms: int, *, bid: str, ask: str) -> dict[str, object]:
    return {
        "coin": "BTC",
        "time": timestamp_ms,
        "levels": [
            [
                {"n": 2, "px": bid, "sz": "3"},
                {"n": 1, "px": str(Decimal(bid) - 1), "sz": "4"},
            ],
            [
                {"n": 3, "px": ask, "sz": "5"},
                {"n": 1, "px": str(Decimal(ask) + 1), "sz": "6"},
            ],
        ],
    }


def _quality_report(
    root: Path,
    *,
    qualified: bool = True,
    source_config_hash: str = CONFIG_HASH,
) -> Path:
    path = root / "quality-2026-08-16.json"
    payload = {
        "schema_version": 3,
        "report_date": "2026-08-16",
        "dataset_tier": "A",
        "qualified_day": qualified,
        "source_config_hash": source_config_hash,
        "code_version": SOURCE_CODE_VERSION,
        "markets": [
            {
                "coin": "BTC",
                "negative_latency_count": 0,
                "channel_counts": [["bbo", 1], ["l2Book", 2], ["trades", 1]],
            }
        ],
    }
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode() + b"\n"
    path.write_bytes(encoded)
    path.with_suffix(".json.sha256").write_text(
        f"{hashlib.sha256(encoded).hexdigest()}  {path.name}\n",
        encoding="ascii",
    )
    return path


def _store(root: Path, *, mixed_config: bool = False) -> SegmentedEventStore:
    timestamps = iter((DAY + 101, DAY + 201, DAY + 301, DAY + 5_101))
    store = SegmentedEventStore(
        root,
        fsync=False,
        clock_ms=lambda: next(timestamps),
    )
    store.append(
        "public-market-data",
        _event(
            channel="bbo",
            exchange_ts_ms=DAY + 1,
            sequence=0,
            payload={
                "coin": "BTC",
                "time": DAY + 1,
                "bbo": [{"px": "100", "sz": "2"}, {"px": "101", "sz": "2"}],
            },
        ),
    )
    store.append(
        "public-market-data",
        _event(
            channel="l2Book",
            exchange_ts_ms=DAY + 101,
            sequence=1,
            payload=_book_payload(DAY + 101, bid="100", ask="101"),
        ),
    )
    store.append(
        "public-market-data",
        _event(
            channel="trades",
            exchange_ts_ms=DAY + 201,
            sequence=2,
            payload={
                "coin": "BTC",
                "time": DAY + 201,
                "side": "A",
                "px": "100",
                "sz": "1.5",
            },
            config_hash="d" * 64 if mixed_config else CONFIG_HASH,
        ),
    )
    store.append(
        "public-market-data",
        _event(
            channel="l2Book",
            exchange_ts_ms=DAY + 5_001,
            sequence=3,
            payload=_book_payload(DAY + 5_001, bid="101", ask="102"),
        ),
    )
    store.close("public-market-data")
    return store


def test_qualified_collector_day_builds_reproducible_replay_dataset(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path / "collector")
    report = _quality_report(tmp_path)

    first = build_collector_replay_dataset(
        store,
        quality_report=report,
        report_date=datetime(2026, 8, 16, tzinfo=UTC).date(),
        market="BTC",
        builder_code_version=BUILDER_CODE_VERSION,
    )
    second = build_collector_replay_dataset(
        store,
        quality_report=report,
        report_date=datetime(2026, 8, 16, tzinfo=UTC).date(),
        market="BTC",
        builder_code_version=BUILDER_CODE_VERSION,
    )

    assert first == second
    assert first.dataset_sha256 == second.dataset_sha256
    assert first.book_count == 2
    assert first.trade_count == 1
    assert first.ignored_bbo_count == 1
    assert first.maximum_book_gap_ms == 4_900
    assert first.maximum_receive_latency_ms == 100
    assert isinstance(first.events[0], ReplayBook)
    assert isinstance(first.events[1], ReplayTrade)
    trade = first.events[1]
    assert isinstance(trade, ReplayTrade)
    assert trade.aggressor_side is Side.SELL
    assert first.source_segments[0].record_count == 4

    output = write_collector_replay_dataset(first, tmp_path / "datasets")
    loaded = read_collector_replay_dataset(output)

    assert loaded == first
    assert collector_replay_dataset_payload(loaded)["dataset_sha256"] == (
        first.dataset_sha256
    )


def test_top_of_book_probe_plan_is_sparse_and_explicit(tmp_path: Path) -> None:
    dataset = build_collector_replay_dataset(
        _store(tmp_path / "collector"),
        quality_report=_quality_report(tmp_path),
        report_date=datetime(2026, 8, 16, tzinfo=UTC).date(),
        market="BTC",
        builder_code_version=BUILDER_CODE_VERSION,
    )

    probes = generate_top_of_book_probes(
        dataset,
        interval_ms=1_000,
        ttl_ms=500,
        notional_usd=Decimal("10"),
        maker_fee_bps=Decimal("1"),
        minimum_markout_horizon_ms=0,
    )

    assert len(probes) == 4
    assert {probe.side for probe in probes} == {Side.BUY, Side.SELL}
    assert all(
        probe.cancel_requested_ts_ms == probe.submitted_ts_ms + 500 for probe in probes
    )
    assert all(probe.price * probe.size == Decimal("10") for probe in probes)


def test_replay_dataset_rejects_unqualified_or_mixed_evidence(tmp_path: Path) -> None:
    report = _quality_report(tmp_path, qualified=False)
    with pytest.raises(CollectorReplayError, match="not qualified"):
        build_collector_replay_dataset(
            _store(tmp_path / "unqualified"),
            quality_report=report,
            report_date=datetime(2026, 8, 16, tzinfo=UTC).date(),
            market="BTC",
            builder_code_version=BUILDER_CODE_VERSION,
        )

    report = _quality_report(tmp_path, qualified=True)
    with pytest.raises(CollectorReplayError, match="configuration is mixed"):
        build_collector_replay_dataset(
            _store(tmp_path / "mixed", mixed_config=True),
            quality_report=report,
            report_date=datetime(2026, 8, 16, tzinfo=UTC).date(),
            market="BTC",
            builder_code_version=BUILDER_CODE_VERSION,
        )


def test_replay_dataset_file_checksum_and_internal_hash_are_enforced(
    tmp_path: Path,
) -> None:
    dataset = build_collector_replay_dataset(
        _store(tmp_path / "collector"),
        quality_report=_quality_report(tmp_path),
        report_date=datetime(2026, 8, 16, tzinfo=UTC).date(),
        market="BTC",
        builder_code_version=BUILDER_CODE_VERSION,
    )
    output = write_collector_replay_dataset(dataset, tmp_path / "datasets")
    decoded = json.loads(output.read_text(encoding="utf-8"))
    decoded["dataset_sha256"] = "0" * 64
    encoded = json.dumps(decoded, indent=2).encode() + b"\n"
    output.write_bytes(encoded)
    output.with_suffix(".json.sha256").write_text(
        f"{hashlib.sha256(encoded).hexdigest()}  {output.name}\n",
        encoding="ascii",
    )

    with pytest.raises(CollectorReplayError, match="dataset checksum mismatch"):
        read_collector_replay_dataset(output)
