"""Qualified tier-A collector datasets for deterministic M4 replays."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import cast

from hyperbot.models import BookLevel, DatasetTier, Side, TimeSource
from hyperbot.quality import QUALITY_SCHEMA_VERSION, market_event_from_payload
from hyperbot.replay.engine import (
    ReplayBook,
    ReplayMarketEvent,
    ReplayQuote,
    ReplayTrade,
)
from hyperbot.segmented_store import SegmentedEventStore

COLLECTOR_REPLAY_DATASET_SCHEMA_VERSION = 2
_CODE_VERSION = re.compile(r"^.+\+g[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CollectorReplayError(RuntimeError):
    """Raised when collector evidence is insufficient or inconsistent."""


@dataclass(frozen=True, slots=True)
class ReplaySourceSegment:
    path: str
    storage_tier: str
    record_count: int
    first_sequence: int
    last_sequence: int
    storage_sha256: str
    content_sha256: str


@dataclass(frozen=True, slots=True)
class CollectorReplayDataset:
    schema_version: int
    dataset_id: str
    report_date: str
    market: str
    dataset_tier: DatasetTier
    builder_code_version: str
    source_code_version: str
    source_config_sha256: str
    quality_report_sha256: str
    source_manifest_sha256: str
    source_segments: tuple[ReplaySourceSegment, ...]
    source_run_ids: tuple[str, ...]
    events: tuple[ReplayMarketEvent, ...]
    book_count: int
    trade_count: int
    ignored_bbo_count: int
    first_exchange_ts_ms: int
    last_exchange_ts_ms: int
    maximum_receive_latency_ms: int
    maximum_book_receive_latency_ms: int
    maximum_trade_receive_latency_ms: int
    maximum_book_gap_ms: int
    dataset_sha256: str


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _checked_sha256_file(path: Path) -> tuple[bytes, str]:
    if path.is_symlink() or not path.is_file():
        raise CollectorReplayError(f"unsafe or missing file: {path}")
    checksum_candidates = (
        path.with_suffix(path.suffix + ".sha256"),
        path.with_suffix(".sha256"),
    )
    checksum_path = next(
        (
            candidate
            for candidate in checksum_candidates
            if not candidate.is_symlink() and candidate.is_file()
        ),
        None,
    )
    if checksum_path is None:
        raise CollectorReplayError(f"checksum is missing for: {path}")
    raw = path.read_bytes()
    actual = hashlib.sha256(raw).hexdigest()
    try:
        expected = checksum_path.read_text(encoding="ascii").split()[0]
    except (OSError, IndexError) as exc:
        raise CollectorReplayError(f"checksum is invalid: {checksum_path}") from exc
    if actual != expected:
        raise CollectorReplayError(f"checksum mismatch: {path}")
    return raw, actual


def _quality_evidence(
    path: Path,
    *,
    report_date: date,
    market: str,
) -> tuple[dict[str, object], str]:
    raw, checksum = _checked_sha256_file(path)
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CollectorReplayError(f"invalid quality report JSON: {path}") from exc
    if not isinstance(decoded, dict):
        raise CollectorReplayError("quality report must be an object")
    report = cast(dict[str, object], decoded)
    expected = {
        "schema_version": QUALITY_SCHEMA_VERSION,
        "report_date": report_date.isoformat(),
        "dataset_tier": DatasetTier.A.value,
        "qualified_day": True,
    }
    for field, value in expected.items():
        if report.get(field) != value:
            raise CollectorReplayError(
                f"quality report is not qualified tier-A evidence: {field}"
            )
    source_config = report.get("source_config_hash")
    source_code = report.get("code_version")
    if not isinstance(source_config, str) or _SHA256.fullmatch(source_config) is None:
        raise CollectorReplayError("quality source config hash is invalid")
    if not isinstance(source_code, str) or _CODE_VERSION.fullmatch(source_code) is None:
        raise CollectorReplayError("quality source code version lacks a full Git SHA")
    raw_markets = report.get("markets")
    if not isinstance(raw_markets, list):
        raise CollectorReplayError("quality report markets are invalid")
    selected = next(
        (
            item
            for item in raw_markets
            if isinstance(item, dict) and item.get("coin") == market
        ),
        None,
    )
    if not isinstance(selected, dict):
        raise CollectorReplayError(f"market is absent from quality report: {market}")
    if selected.get("negative_latency_count") != 0:
        raise CollectorReplayError(f"negative latency evidence for market: {market}")
    raw_counts = selected.get("channel_counts")
    if not isinstance(raw_counts, list):
        raise CollectorReplayError("quality channel counts are invalid")
    counts: dict[str, int] = {}
    for item in raw_counts:
        if (
            isinstance(item, list)
            and len(item) == 2
            and isinstance(item[0], str)
            and isinstance(item[1], int)
            and not isinstance(item[1], bool)
        ):
            counts[item[0]] = item[1]
    for channel in ("l2Book", "trades"):
        if counts.get(channel, 0) <= 0:
            raise CollectorReplayError(
                f"market lacks required replay channel {channel}: {market}"
            )
    return report, checksum


def _source_segments(
    store: SegmentedEventStore,
    *,
    report_date: date,
    stream: str,
) -> tuple[tuple[ReplaySourceSegment, ...], str]:
    manifest_path = store.root / stream / "manifest.json"
    raw, checksum = _checked_sha256_file(manifest_path)
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CollectorReplayError("collector manifest JSON is invalid") from exc
    if (
        not isinstance(decoded, dict)
        or decoded.get("stream") != stream
        or not isinstance(decoded.get("segments"), list)
    ):
        raise CollectorReplayError("collector manifest is invalid")
    selected: list[ReplaySourceSegment] = []
    for raw_segment in decoded["segments"]:
        if not isinstance(raw_segment, dict):
            raise CollectorReplayError("collector segment evidence is invalid")
        if raw_segment.get("utc_date") != report_date.isoformat():
            continue
        values = {
            "path": raw_segment.get("path"),
            "storage_tier": raw_segment.get("storage_tier"),
            "record_count": raw_segment.get("record_count"),
            "first_sequence": raw_segment.get("first_sequence"),
            "last_sequence": raw_segment.get("last_sequence"),
            "storage_sha256": raw_segment.get("storage_sha256"),
            "content_sha256": raw_segment.get("content_sha256"),
        }
        if (
            not isinstance(values["path"], str)
            or values["storage_tier"] not in {"hot", "archive"}
            or not isinstance(values["record_count"], int)
            or isinstance(values["record_count"], bool)
            or not isinstance(values["first_sequence"], int)
            or isinstance(values["first_sequence"], bool)
            or not isinstance(values["last_sequence"], int)
            or isinstance(values["last_sequence"], bool)
            or not isinstance(values["storage_sha256"], str)
            or _SHA256.fullmatch(values["storage_sha256"]) is None
            or not isinstance(values["content_sha256"], str)
            or _SHA256.fullmatch(values["content_sha256"]) is None
        ):
            raise CollectorReplayError("collector segment evidence is incomplete")
        selected.append(
            ReplaySourceSegment(
                path=values["path"],
                storage_tier=values["storage_tier"],
                record_count=values["record_count"],
                first_sequence=values["first_sequence"],
                last_sequence=values["last_sequence"],
                storage_sha256=values["storage_sha256"],
                content_sha256=values["content_sha256"],
            )
        )
    if not selected:
        raise CollectorReplayError(
            f"no closed collector segments for {report_date.isoformat()}"
        )
    return tuple(selected), checksum


def _decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise CollectorReplayError(f"invalid decimal field: {field}") from exc
    if not result.is_finite() or result <= 0:
        raise CollectorReplayError(f"non-positive decimal field: {field}")
    return result


def _level(value: object, *, field: str) -> BookLevel:
    if not isinstance(value, dict):
        raise CollectorReplayError(f"invalid L2 level: {field}")
    count = value.get("n")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        raise CollectorReplayError(f"invalid order count: {field}")
    return BookLevel(
        price=_decimal(value.get("px"), f"{field}.px"),
        size=_decimal(value.get("sz"), f"{field}.sz"),
        order_count=count,
    )


def _strictly_ordered(levels: tuple[BookLevel, ...], *, reverse: bool) -> bool:
    pairs = zip(levels, levels[1:], strict=False)
    if reverse:
        return all(left.price > right.price for left, right in pairs)
    return all(left.price < right.price for left, right in pairs)


def _decode_book(
    payload: dict[str, object],
    *,
    market: str,
    timestamp_ms: int,
    receive_ts_ms: int,
    sequence: int,
) -> ReplayBook:
    levels = payload.get("levels")
    if (
        payload.get("coin") != market
        or payload.get("time") != timestamp_ms
        or not isinstance(levels, list)
        or len(levels) != 2
        or not all(isinstance(side, list) for side in levels)
    ):
        raise CollectorReplayError("L2 payload is inconsistent")
    bids = tuple(
        _level(value, field=f"bids[{index}]")
        for index, value in enumerate(cast(list[object], levels[0]))
    )
    asks = tuple(
        _level(value, field=f"asks[{index}]")
        for index, value in enumerate(cast(list[object], levels[1]))
    )
    if not bids or not asks:
        raise CollectorReplayError("L2 replay evidence requires both sides")
    if not _strictly_ordered(bids, reverse=True) or not _strictly_ordered(
        asks, reverse=False
    ):
        raise CollectorReplayError("L2 levels are not strictly price ordered")
    try:
        return ReplayBook(
            market,
            timestamp_ms,
            sequence,
            bids,
            asks,
            receive_ts_ms,
        )
    except ValueError as exc:
        raise CollectorReplayError(f"invalid replay book: {exc}") from exc


def _decode_trade(
    payload: dict[str, object],
    *,
    market: str,
    timestamp_ms: int,
    receive_ts_ms: int,
    sequence: int,
) -> ReplayTrade:
    if payload.get("coin") != market or payload.get("time") != timestamp_ms:
        raise CollectorReplayError("trade payload is inconsistent")
    side_label = payload.get("side")
    side = (
        {"B": Side.BUY, "A": Side.SELL}.get(side_label)
        if isinstance(side_label, str)
        else None
    )
    if side is None:
        raise CollectorReplayError("trade aggressor side is invalid")
    try:
        return ReplayTrade(
            market=market,
            timestamp_ms=timestamp_ms,
            source_sequence=sequence,
            aggressor_side=side,
            price=_decimal(payload.get("px"), "trade.px"),
            size=_decimal(payload.get("sz"), "trade.sz"),
            receive_ts_ms=receive_ts_ms,
        )
    except ValueError as exc:
        raise CollectorReplayError(f"invalid replay trade: {exc}") from exc


def _event_payload(event: ReplayMarketEvent) -> dict[str, object]:
    if isinstance(event, ReplayBook):
        return {
            "type": "book",
            "market": event.market,
            "timestamp_ms": event.timestamp_ms,
            "source_sequence": event.source_sequence,
            "receive_ts_ms": event.receive_ts_ms,
            "bids": [
                {
                    "price": str(level.price),
                    "size": str(level.size),
                    "order_count": level.order_count,
                }
                for level in event.bids
            ],
            "asks": [
                {
                    "price": str(level.price),
                    "size": str(level.size),
                    "order_count": level.order_count,
                }
                for level in event.asks
            ],
        }
    return {
        "type": "trade",
        "market": event.market,
        "timestamp_ms": event.timestamp_ms,
        "source_sequence": event.source_sequence,
        "receive_ts_ms": event.receive_ts_ms,
        "aggressor_side": event.aggressor_side.value,
        "price": str(event.price),
        "size": str(event.size),
    }


def _segment_payload(segment: ReplaySourceSegment) -> dict[str, object]:
    return {
        "path": segment.path,
        "storage_tier": segment.storage_tier,
        "record_count": segment.record_count,
        "first_sequence": segment.first_sequence,
        "last_sequence": segment.last_sequence,
        "storage_sha256": segment.storage_sha256,
        "content_sha256": segment.content_sha256,
    }


def _dataset_base_payload(dataset: CollectorReplayDataset) -> dict[str, object]:
    return {
        "schema_version": dataset.schema_version,
        "kind": "hyperbot_collector_replay_dataset",
        "dataset_id": dataset.dataset_id,
        "report_date": dataset.report_date,
        "market": dataset.market,
        "dataset_tier": dataset.dataset_tier.value,
        "builder_code_version": dataset.builder_code_version,
        "source_code_version": dataset.source_code_version,
        "source_config_sha256": dataset.source_config_sha256,
        "quality_report_sha256": dataset.quality_report_sha256,
        "source_manifest_sha256": dataset.source_manifest_sha256,
        "source_segments": [
            _segment_payload(segment) for segment in dataset.source_segments
        ],
        "source_run_ids": list(dataset.source_run_ids),
        "events": [_event_payload(event) for event in dataset.events],
        "metrics": {
            "book_count": dataset.book_count,
            "trade_count": dataset.trade_count,
            "ignored_bbo_count": dataset.ignored_bbo_count,
            "first_exchange_ts_ms": dataset.first_exchange_ts_ms,
            "last_exchange_ts_ms": dataset.last_exchange_ts_ms,
            "maximum_receive_latency_ms": dataset.maximum_receive_latency_ms,
            "maximum_book_receive_latency_ms": (
                dataset.maximum_book_receive_latency_ms
            ),
            "maximum_trade_receive_latency_ms": (
                dataset.maximum_trade_receive_latency_ms
            ),
            "maximum_book_gap_ms": dataset.maximum_book_gap_ms,
        },
    }


def collector_replay_dataset_payload(
    dataset: CollectorReplayDataset,
) -> dict[str, object]:
    payload = _dataset_base_payload(dataset)
    payload["dataset_sha256"] = dataset.dataset_sha256
    return payload


def build_collector_replay_dataset(
    store: SegmentedEventStore,
    *,
    quality_report: Path,
    report_date: date,
    market: str,
    builder_code_version: str,
    stream: str = "public-market-data",
) -> CollectorReplayDataset:
    """Convert one immutable, qualified UTC day to M4 market events."""

    if not market.strip():
        raise ValueError("market must not be empty")
    if _CODE_VERSION.fullmatch(builder_code_version) is None:
        raise ValueError("builder_code_version must include a full Git SHA")
    report, quality_sha256 = _quality_evidence(
        quality_report,
        report_date=report_date,
        market=market,
    )
    segments, manifest_sha256 = _source_segments(
        store,
        report_date=report_date,
        stream=stream,
    )
    source_code = cast(str, report["code_version"])
    source_config = cast(str, report["source_config_hash"])
    begin_ms = int(
        datetime.combine(report_date, datetime.min.time(), tzinfo=UTC).timestamp()
        * 1_000
    )
    end_ms = int(
        datetime.combine(
            report_date + timedelta(days=1),
            datetime.min.time(),
            tzinfo=UTC,
        ).timestamp()
        * 1_000
    )
    events: list[ReplayMarketEvent] = []
    run_ids: set[str] = set()
    ignored_bbo = 0
    maximum_latency = 0
    for record in store.iter_records_for_utc_date(stream, report_date.isoformat()):
        raw_payload = record.get("payload")
        if not isinstance(raw_payload, dict):
            raise CollectorReplayError("collector record payload is invalid")
        if raw_payload.get("coin") != market:
            continue
        event = market_event_from_payload(cast(dict[str, object], raw_payload))
        if not begin_ms <= event.receive_ts_ms < end_ms:
            continue
        if event.context.time_source is not TimeSource.EXCHANGE:
            raise CollectorReplayError("collector event time source is not exchange")
        if event.context.code_version != source_code:
            raise CollectorReplayError("collector event code version is mixed")
        if event.context.config_hash != source_config:
            raise CollectorReplayError("collector event configuration is mixed")
        if event.exchange_ts_ms is None:
            raise CollectorReplayError("replay channel lacks exchange timestamp")
        latency = event.receive_ts_ms - event.exchange_ts_ms
        if latency < 0:
            raise CollectorReplayError("collector event has negative latency")
        maximum_latency = max(maximum_latency, latency)
        run_ids.add(event.context.run_id)
        try:
            decoded_payload = json.loads(event.payload_json)
        except json.JSONDecodeError as exc:
            raise CollectorReplayError("public payload JSON is invalid") from exc
        if not isinstance(decoded_payload, dict):
            raise CollectorReplayError("public payload must be an object")
        sequence = record.get("sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 0:
            raise CollectorReplayError("collector source sequence is invalid")
        if event.channel == "l2Book":
            events.append(
                _decode_book(
                    cast(dict[str, object], decoded_payload),
                    market=market,
                    timestamp_ms=event.exchange_ts_ms,
                    receive_ts_ms=event.receive_ts_ms,
                    sequence=sequence,
                )
            )
        elif event.channel == "trades":
            events.append(
                _decode_trade(
                    cast(dict[str, object], decoded_payload),
                    market=market,
                    timestamp_ms=event.exchange_ts_ms,
                    receive_ts_ms=event.receive_ts_ms,
                    sequence=sequence,
                )
            )
        elif event.channel == "bbo":
            ignored_bbo += 1
        else:
            raise CollectorReplayError(
                f"unsupported collector channel: {event.channel}"
            )
    ordered = tuple(
        sorted(
            events,
            key=lambda item: (
                item.timestamp_ms,
                item.source_sequence,
                0 if isinstance(item, ReplayBook) else 1,
            ),
        )
    )
    books = tuple(item for item in ordered if isinstance(item, ReplayBook))
    trades = tuple(item for item in ordered if isinstance(item, ReplayTrade))
    if len(books) < 2 or not trades:
        raise CollectorReplayError(
            "replay requires at least two L2 books and one trade"
        )
    if len(run_ids) != 1:
        raise CollectorReplayError(
            "qualified replay day must contain exactly one run_id"
        )
    book_gaps = [
        current.timestamp_ms - previous.timestamp_ms
        for previous, current in zip(books, books[1:], strict=False)
    ]
    if any(gap < 0 for gap in book_gaps):
        raise CollectorReplayError("L2 exchange timestamps move backwards")
    book_latencies = [item.observed_ts_ms - item.timestamp_ms for item in books]
    trade_latencies = [
        cast(int, item.receive_ts_ms) - item.timestamp_ms for item in trades
    ]
    evidence = {
        "schema_version": COLLECTOR_REPLAY_DATASET_SCHEMA_VERSION,
        "report_date": report_date.isoformat(),
        "market": market,
        "quality_report_sha256": quality_sha256,
        "source_manifest_sha256": manifest_sha256,
        "source_segments": [_segment_payload(segment) for segment in segments],
    }
    dataset_id = f"collector-replay-{report_date.isoformat()}-{_sha256(evidence)[:16]}"
    provisional = CollectorReplayDataset(
        schema_version=COLLECTOR_REPLAY_DATASET_SCHEMA_VERSION,
        dataset_id=dataset_id,
        report_date=report_date.isoformat(),
        market=market,
        dataset_tier=DatasetTier.A,
        builder_code_version=builder_code_version,
        source_code_version=source_code,
        source_config_sha256=source_config,
        quality_report_sha256=quality_sha256,
        source_manifest_sha256=manifest_sha256,
        source_segments=segments,
        source_run_ids=tuple(sorted(run_ids)),
        events=ordered,
        book_count=len(books),
        trade_count=len(trades),
        ignored_bbo_count=ignored_bbo,
        first_exchange_ts_ms=ordered[0].timestamp_ms,
        last_exchange_ts_ms=ordered[-1].timestamp_ms,
        maximum_receive_latency_ms=maximum_latency,
        maximum_book_receive_latency_ms=max(book_latencies),
        maximum_trade_receive_latency_ms=max(trade_latencies),
        maximum_book_gap_ms=max(book_gaps),
        dataset_sha256="",
    )
    dataset_sha256 = _sha256(_dataset_base_payload(provisional))
    return replace(provisional, dataset_sha256=dataset_sha256)


def write_collector_replay_dataset(
    dataset: CollectorReplayDataset,
    output_root: Path,
) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    safe_market = re.sub(r"[^A-Za-z0-9._-]+", "_", dataset.market)
    path = (
        output_root
        / f"{dataset.report_date}-{safe_market}-{dataset.dataset_id[-16:]}.json"
    )
    checksum_path = path.with_suffix(".json.sha256")
    if path.exists() or checksum_path.exists():
        raise FileExistsError(f"replay dataset already exists: {path}")
    encoded = (
        json.dumps(
            collector_replay_dataset_payload(dataset),
            ensure_ascii=False,
            indent=2,
            sort_keys=False,
        ).encode("utf-8")
        + b"\n"
    )
    path.write_bytes(encoded)
    checksum_path.write_text(
        f"{hashlib.sha256(encoded).hexdigest()}  {path.name}\n",
        encoding="ascii",
    )
    return path


def _dataset_level(value: object, *, field: str) -> BookLevel:
    if not isinstance(value, dict):
        raise CollectorReplayError(f"invalid dataset level: {field}")
    count = value.get("order_count")
    if count is not None and (
        isinstance(count, bool) or not isinstance(count, int) or count < 0
    ):
        raise CollectorReplayError(f"invalid dataset order count: {field}")
    return BookLevel(
        price=_decimal(value.get("price"), f"{field}.price"),
        size=_decimal(value.get("size"), f"{field}.size"),
        order_count=count,
    )


def _dataset_event(value: object) -> ReplayMarketEvent:
    if not isinstance(value, dict):
        raise CollectorReplayError("dataset event must be an object")
    try:
        market = str(value["market"])
        timestamp_ms = int(cast(int, value["timestamp_ms"]))
        sequence = int(cast(int, value["source_sequence"]))
        event_type = value["type"]
    except (KeyError, TypeError, ValueError) as exc:
        raise CollectorReplayError("dataset event metadata is invalid") from exc
    if event_type == "book":
        raw_bids = value.get("bids")
        raw_asks = value.get("asks")
        if not isinstance(raw_bids, list) or not isinstance(raw_asks, list):
            raise CollectorReplayError("dataset book levels are invalid")
        try:
            return ReplayBook(
                market=market,
                timestamp_ms=timestamp_ms,
                source_sequence=sequence,
                bids=tuple(
                    _dataset_level(item, field=f"bids[{index}]")
                    for index, item in enumerate(raw_bids)
                ),
                asks=tuple(
                    _dataset_level(item, field=f"asks[{index}]")
                    for index, item in enumerate(raw_asks)
                ),
                receive_ts_ms=(
                    int(cast(int, value["receive_ts_ms"]))
                    if value.get("receive_ts_ms") is not None
                    else None
                ),
            )
        except ValueError as exc:
            raise CollectorReplayError(f"invalid dataset book: {exc}") from exc
    if event_type == "trade":
        try:
            return ReplayTrade(
                market=market,
                timestamp_ms=timestamp_ms,
                source_sequence=sequence,
                aggressor_side=Side(str(value["aggressor_side"])),
                price=_decimal(value.get("price"), "trade.price"),
                size=_decimal(value.get("size"), "trade.size"),
                receive_ts_ms=(
                    int(cast(int, value["receive_ts_ms"]))
                    if value.get("receive_ts_ms") is not None
                    else None
                ),
            )
        except (KeyError, ValueError) as exc:
            raise CollectorReplayError(f"invalid dataset trade: {exc}") from exc
    raise CollectorReplayError(f"unsupported dataset event type: {event_type!r}")


def collector_replay_dataset_from_payload(value: object) -> CollectorReplayDataset:
    if not isinstance(value, dict):
        raise CollectorReplayError("collector replay dataset must be an object")
    payload = cast(dict[str, object], value)
    if (
        payload.get("schema_version") != COLLECTOR_REPLAY_DATASET_SCHEMA_VERSION
        or payload.get("kind") != "hyperbot_collector_replay_dataset"
        or payload.get("dataset_tier") != DatasetTier.A.value
    ):
        raise CollectorReplayError("unsupported collector replay dataset")
    raw_segments = payload.get("source_segments")
    raw_events = payload.get("events")
    raw_runs = payload.get("source_run_ids")
    metrics = payload.get("metrics")
    if (
        not isinstance(raw_segments, list)
        or not isinstance(raw_events, list)
        or not isinstance(raw_runs, list)
        or not isinstance(metrics, dict)
    ):
        raise CollectorReplayError("collector replay dataset structure is invalid")
    segments: list[ReplaySourceSegment] = []
    for item in raw_segments:
        if not isinstance(item, dict):
            raise CollectorReplayError("dataset source segment is invalid")
        try:
            segment = ReplaySourceSegment(
                path=str(item["path"]),
                storage_tier=str(item["storage_tier"]),
                record_count=int(cast(int, item["record_count"])),
                first_sequence=int(cast(int, item["first_sequence"])),
                last_sequence=int(cast(int, item["last_sequence"])),
                storage_sha256=str(item["storage_sha256"]),
                content_sha256=str(item["content_sha256"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CollectorReplayError("dataset source segment is invalid") from exc
        if (
            segment.storage_tier not in {"hot", "archive"}
            or segment.record_count <= 0
            or segment.first_sequence < 0
            or segment.last_sequence < segment.first_sequence
            or _SHA256.fullmatch(segment.storage_sha256) is None
            or _SHA256.fullmatch(segment.content_sha256) is None
        ):
            raise CollectorReplayError("dataset source segment evidence is invalid")
        segments.append(segment)
    events = tuple(_dataset_event(item) for item in raw_events)
    books = sum(isinstance(item, ReplayBook) for item in events)
    trades = sum(isinstance(item, ReplayTrade) for item in events)
    try:
        dataset = CollectorReplayDataset(
            schema_version=COLLECTOR_REPLAY_DATASET_SCHEMA_VERSION,
            dataset_id=str(payload["dataset_id"]),
            report_date=str(payload["report_date"]),
            market=str(payload["market"]),
            dataset_tier=DatasetTier.A,
            builder_code_version=str(payload["builder_code_version"]),
            source_code_version=str(payload["source_code_version"]),
            source_config_sha256=str(payload["source_config_sha256"]),
            quality_report_sha256=str(payload["quality_report_sha256"]),
            source_manifest_sha256=str(payload["source_manifest_sha256"]),
            source_segments=tuple(segments),
            source_run_ids=tuple(str(item) for item in raw_runs),
            events=events,
            book_count=int(cast(int, metrics["book_count"])),
            trade_count=int(cast(int, metrics["trade_count"])),
            ignored_bbo_count=int(cast(int, metrics["ignored_bbo_count"])),
            first_exchange_ts_ms=int(cast(int, metrics["first_exchange_ts_ms"])),
            last_exchange_ts_ms=int(cast(int, metrics["last_exchange_ts_ms"])),
            maximum_receive_latency_ms=int(
                cast(int, metrics["maximum_receive_latency_ms"])
            ),
            maximum_book_receive_latency_ms=int(
                cast(int, metrics["maximum_book_receive_latency_ms"])
            ),
            maximum_trade_receive_latency_ms=int(
                cast(int, metrics["maximum_trade_receive_latency_ms"])
            ),
            maximum_book_gap_ms=int(cast(int, metrics["maximum_book_gap_ms"])),
            dataset_sha256=str(payload["dataset_sha256"]),
        )
        parsed_date = date.fromisoformat(dataset.report_date)
    except (KeyError, TypeError, ValueError) as exc:
        raise CollectorReplayError(
            "collector replay dataset metadata is invalid"
        ) from exc
    if parsed_date.isoformat() != dataset.report_date:
        raise CollectorReplayError("collector replay dataset date is invalid")
    for checksum in (
        dataset.source_config_sha256,
        dataset.quality_report_sha256,
        dataset.source_manifest_sha256,
        dataset.dataset_sha256,
    ):
        if _SHA256.fullmatch(checksum) is None:
            raise CollectorReplayError("collector replay dataset hash is invalid")
    if (
        not dataset.dataset_id.strip()
        or not dataset.market.strip()
        or _CODE_VERSION.fullmatch(dataset.builder_code_version) is None
        or _CODE_VERSION.fullmatch(dataset.source_code_version) is None
        or len(dataset.source_run_ids) != 1
        or books != dataset.book_count
        or trades != dataset.trade_count
        or books < 2
        or trades < 1
        or not events
        or dataset.first_exchange_ts_ms != events[0].timestamp_ms
        or dataset.last_exchange_ts_ms != events[-1].timestamp_ms
    ):
        raise CollectorReplayError("collector replay dataset metadata is inconsistent")
    if _sha256(_dataset_base_payload(dataset)) != dataset.dataset_sha256:
        raise CollectorReplayError("collector replay dataset checksum mismatch")
    return dataset


def read_collector_replay_dataset(path: Path) -> CollectorReplayDataset:
    raw, _ = _checked_sha256_file(path)
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CollectorReplayError(f"invalid collector replay dataset: {path}") from exc
    return collector_replay_dataset_from_payload(decoded)


def generate_top_of_book_probes(
    dataset: CollectorReplayDataset,
    *,
    interval_ms: int,
    ttl_ms: int,
    notional_usd: Decimal,
    maker_fee_bps: Decimal,
    maximum_book_age_ms: int = 500,
    minimum_markout_horizon_ms: int = 30_000,
) -> tuple[ReplayQuote, ...]:
    """Generate sparse execution probes, never a profitability strategy."""

    if (
        interval_ms <= 0
        or ttl_ms <= 0
        or maximum_book_age_ms < 0
        or minimum_markout_horizon_ms < 0
    ):
        raise ValueError("probe timing values are invalid")
    if not notional_usd.is_finite() or notional_usd <= 0:
        raise ValueError("probe notional must be finite and positive")
    if not maker_fee_bps.is_finite() or maker_fee_bps < 0:
        raise ValueError("maker fee must be finite and non-negative")
    books = tuple(item for item in dataset.events if isinstance(item, ReplayBook))
    next_probe_ts = books[0].observed_ts_ms
    latest_submission = dataset.last_exchange_ts_ms - minimum_markout_horizon_ms
    quotes: list[ReplayQuote] = []
    for book in books:
        if (
            book.observed_ts_ms < next_probe_ts
            or book.observed_ts_ms > latest_submission
            or book.observed_ts_ms - book.timestamp_ms > maximum_book_age_ms
        ):
            continue
        for side, price in (
            (Side.BUY, book.bids[0].price),
            (Side.SELL, book.asks[0].price),
        ):
            quote_key = (
                f"{dataset.dataset_id}|top-of-book-probe-v1|{book.source_sequence}|"
                f"{side.value}|{price}"
            )
            quotes.append(
                ReplayQuote(
                    quote_id=hashlib.sha256(quote_key.encode("utf-8")).hexdigest()[:32],
                    market=dataset.market,
                    side=side,
                    price=price,
                    size=notional_usd / price,
                    submitted_ts_ms=book.observed_ts_ms,
                    cancel_requested_ts_ms=book.observed_ts_ms + ttl_ms,
                    maker_fee_bps=maker_fee_bps,
                )
            )
        next_probe_ts = book.observed_ts_ms + interval_ms
    if not quotes:
        raise CollectorReplayError("probe plan generated no quotes")
    return tuple(quotes)
