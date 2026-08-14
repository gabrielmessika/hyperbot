"""Deterministic daily quality metrics for public market-data captures."""

from __future__ import annotations

import hashlib
import heapq
import json
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TextIO, TypeVar, cast

from hyperbot.models import (
    CollectorControlEvent,
    CollectorControlKind,
    DatasetTier,
    EventContext,
    PublicMarketDataEvent,
    TimeSource,
)

QUALITY_SCHEMA_VERSION = 2
_REQUIRED_BOOK_CHANNELS = frozenset({"l2Book", "bbo"})
_PERCENTILE_SORT_CHUNK_VALUES = 50_000
_MAX_GAP_DETAILS_PER_MARKET = 1_000
_Numeric = TypeVar("_Numeric", int, Decimal)


def _parsed_numeric_lines(
    handle: TextIO,
    parse: Callable[[str], _Numeric],
) -> Iterator[_Numeric]:
    for line in handle:
        value = line.strip()
        if value:
            yield parse(value)


class GapCause(StrEnum):
    COLLECTOR_OUTAGE = "collector_outage"
    COLLECTOR_NOT_RUNNING = "collector_not_running"
    MARKET_STALE = "market_stale"


class QualityGateStage(StrEnum):
    INSUFFICIENT_SEVEN_DAYS = "insufficient_seven_days"
    COLLECT_TO_THIRTY_DAYS = "collect_to_thirty_days"
    THIRTY_DAYS_COMPLETE = "thirty_days_complete"


@dataclass(frozen=True, slots=True)
class QualityConfig:
    expected_markets: tuple[str, ...]
    stale_after_ms: int = 500
    major_gap_ms: int = 5_000
    minimum_coverage_pct: Decimal = Decimal("99")

    def __post_init__(self) -> None:
        if not self.expected_markets:
            raise ValueError("expected_markets must not be empty")
        if len(set(self.expected_markets)) != len(self.expected_markets):
            raise ValueError("expected_markets must be unique")
        if any(not market.strip() for market in self.expected_markets):
            raise ValueError("expected market names must not be empty")
        if self.stale_after_ms <= 0:
            raise ValueError("stale_after_ms must be positive")
        if self.major_gap_ms < self.stale_after_ms:
            raise ValueError("major_gap_ms must be at least stale_after_ms")
        if not Decimal("0") <= self.minimum_coverage_pct <= Decimal("100"):
            raise ValueError("minimum_coverage_pct must be between 0 and 100")

    @property
    def sha256(self) -> str:
        payload = {
            "expected_markets": list(self.expected_markets),
            "stale_after_ms": self.stale_after_ms,
            "major_gap_ms": self.major_gap_ms,
            "minimum_coverage_pct": str(self.minimum_coverage_pct),
        }
        return hashlib.sha256(_canonical_json(payload)).hexdigest()


@dataclass(frozen=True, slots=True)
class QualityGap:
    coin: str
    begin_ts_ms: int
    end_ts_ms: int
    duration_ms: int
    cause: GapCause
    major: bool


@dataclass(frozen=True, slots=True)
class MarketQuality:
    coin: str
    message_count: int
    channel_counts: tuple[tuple[str, int], ...]
    coverage_pct: Decimal
    latency_p50_ms: int | None
    latency_p95_ms: int | None
    latency_p99_ms: int | None
    negative_latency_count: int
    gap_count: int
    major_gap_count: int
    collector_outage_gap_count: int
    collector_not_running_gap_count: int
    market_stale_gap_count: int
    stale_duration_ms: int
    spread_p10_bps: Decimal | None
    spread_p50_bps: Decimal | None
    spread_p90_bps: Decimal | None
    average_bid_depth: Decimal | None
    average_ask_depth: Decimal | None
    trade_count: int
    trade_notional_usd: Decimal


@dataclass(frozen=True, slots=True)
class DailyQualityReport:
    schema_version: int
    report_date: str
    generated_at_ms: int
    run_id: str
    code_version: str
    source_config_hash: str
    quality_config_hash: str
    dataset_tier: DatasetTier
    window_begin_ms: int
    window_end_ms: int
    collector_outage_count: int
    collector_outage_duration_ms: int
    total_gap_count: int
    retained_gap_detail_count: int
    gap_details_truncated: bool
    gap_detail_limit_per_market: int
    gaps: tuple[QualityGap, ...]
    markets: tuple[MarketQuality, ...]
    qualified_day: bool
    qualification_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualityGateResult:
    stage: QualityGateStage
    observed_days: int
    consecutive_qualified_days: int
    required_initial_days: int
    required_evidence_days: int
    missing_dates: tuple[str, ...]


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _percentile(values: Sequence[Decimal], percentile: Decimal) -> Decimal | None:
    if not values:
        return None
    ordered = sorted(values)
    return _ordered_percentile(ordered, percentile)


def _ordered_percentile(
    ordered: Sequence[Decimal], percentile: Decimal
) -> Decimal | None:
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = percentile * Decimal(len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def _integer_percentile(values: Sequence[int], percentile: Decimal) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    return _ordered_integer_percentile(ordered, percentile)


def _ordered_integer_percentile(
    ordered: Sequence[int], percentile: Decimal
) -> int | None:
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    position = percentile * Decimal(len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    result = Decimal(ordered[lower]) + Decimal(ordered[upper] - ordered[lower]) * weight
    return int(result.to_integral_value())


def _merge_intervals(
    intervals: Iterable[tuple[int, int]],
) -> list[tuple[int, int]]:
    ordered = sorted((begin, end) for begin, end in intervals if end > begin)
    merged: list[tuple[int, int]] = []
    for begin, end in ordered:
        if not merged or begin > merged[-1][1]:
            merged.append((begin, end))
        else:
            previous_begin, previous_end = merged[-1]
            merged[-1] = (previous_begin, max(previous_end, end))
    return merged


def _overlaps(
    interval: tuple[int, int],
    outages: Sequence[tuple[int, int]],
) -> bool:
    begin, end = interval
    return any(
        max(begin, outage_begin) < min(end, outage_end)
        for outage_begin, outage_end in outages
    )


def _collector_outages(
    controls: Sequence[CollectorControlEvent],
    window_begin_ms: int,
    window_end_ms: int,
) -> list[tuple[int, int]]:
    start: int | None = None
    outages: list[tuple[int, int]] = []
    saw_connection_transition = False
    for control in sorted(controls, key=lambda item: item.receive_ts_ms):
        timestamp = min(max(control.receive_ts_ms, window_begin_ms), window_end_ms)
        if control.kind in {
            CollectorControlKind.GAP,
            CollectorControlKind.DISCONNECTED,
        }:
            if start is None:
                start = timestamp
        elif control.kind in {
            CollectorControlKind.CONNECTED,
            CollectorControlKind.RECONNECTED,
        }:
            if start is not None:
                outages.append((start, timestamp))
            elif (
                control.kind is CollectorControlKind.RECONNECTED
                and not saw_connection_transition
            ):
                # A first reconnection proves that the outage started before the
                # UTC window. The preceding disconnect lives in the prior day's
                # immutable segment, so carry the outage from midnight.
                outages.append((window_begin_ms, timestamp))
            start = None
            saw_connection_transition = True
        elif control.kind is CollectorControlKind.SHUTDOWN:
            saw_connection_transition = True
    if start is not None:
        outages.append((start, window_end_ms))
    return _merge_intervals(outages)


def _collector_sessions(
    controls: Sequence[CollectorControlEvent],
    window_begin_ms: int,
    window_end_ms: int,
) -> list[tuple[int, int]]:
    lifecycle_controls = tuple(
        control
        for control in controls
        if control.kind
        in {
            CollectorControlKind.CONNECTED,
            CollectorControlKind.RECONNECTED,
            CollectorControlKind.DISCONNECTED,
            CollectorControlKind.SHUTDOWN,
        }
    )
    if not lifecycle_controls:
        return [(window_begin_ms, window_end_ms)]
    start: int | None = None
    sessions: list[tuple[int, int]] = []
    saw_lifecycle_transition = False
    for control in sorted(lifecycle_controls, key=lambda item: item.receive_ts_ms):
        timestamp = min(max(control.receive_ts_ms, window_begin_ms), window_end_ms)
        if (
            control.kind
            in {
                CollectorControlKind.CONNECTED,
                CollectorControlKind.RECONNECTED,
            }
            and start is None
        ):
            start = timestamp
            saw_lifecycle_transition = True
        elif control.kind in {
            CollectorControlKind.DISCONNECTED,
            CollectorControlKind.SHUTDOWN,
        }:
            if (
                control.kind is CollectorControlKind.DISCONNECTED
                and start is None
                and not saw_lifecycle_transition
            ):
                # A first disconnect proves that this collector session was
                # already active at midnight. Its opening event is stored in a
                # previous UTC segment and must not make the start of this day
                # look like collector downtime. A shutdown alone is not enough
                # evidence because it can also stop a disconnected collector.
                start = window_begin_ms
            if start is not None:
                sessions.append((start, timestamp))
            start = None
            saw_lifecycle_transition = True
    if start is not None:
        sessions.append((start, window_end_ms))
    return _merge_intervals(sessions)


def _book_metrics(
    event: PublicMarketDataEvent,
) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    try:
        payload = json.loads(event.payload_json)
    except json.JSONDecodeError:
        return None, None, None
    if not isinstance(payload, dict):
        return None, None, None
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_depth: Decimal | None = None
    ask_depth: Decimal | None = None
    if event.channel == "bbo":
        bbo = payload.get("bbo")
        if isinstance(bbo, list) and len(bbo) >= 2:
            bid_item = bbo[0] if isinstance(bbo[0], dict) else {}
            ask_item = bbo[1] if isinstance(bbo[1], dict) else {}
            bid = _decimal(bid_item.get("px"))
            ask = _decimal(ask_item.get("px"))
            bid_depth = _decimal(bid_item.get("sz"))
            ask_depth = _decimal(ask_item.get("sz"))
    elif event.channel == "l2Book":
        levels = payload.get("levels")
        if isinstance(levels, list) and len(levels) >= 2:
            bid_levels = levels[0] if isinstance(levels[0], list) else []
            ask_levels = levels[1] if isinstance(levels[1], list) else []
            if bid_levels and isinstance(bid_levels[0], dict):
                bid = _decimal(bid_levels[0].get("px"))
            if ask_levels and isinstance(ask_levels[0], dict):
                ask = _decimal(ask_levels[0].get("px"))
            bid_sizes = [
                size
                for level in bid_levels
                if isinstance(level, dict)
                for size in [_decimal(level.get("sz"))]
                if size is not None and size >= 0
            ]
            ask_sizes = [
                size
                for level in ask_levels
                if isinstance(level, dict)
                for size in [_decimal(level.get("sz"))]
                if size is not None and size >= 0
            ]
            bid_depth = sum(bid_sizes, Decimal(0)) if bid_sizes else None
            ask_depth = sum(ask_sizes, Decimal(0)) if ask_sizes else None
    spread: Decimal | None = None
    if bid is not None and ask is not None and Decimal(0) < bid <= ask:
        midpoint = (bid + ask) / 2
        if midpoint > 0:
            spread = (ask - bid) / midpoint * Decimal(10_000)
    return spread, bid_depth, ask_depth


def _trade_notional(event: PublicMarketDataEvent) -> Decimal:
    if event.channel != "trades":
        return Decimal(0)
    try:
        payload = json.loads(event.payload_json)
    except json.JSONDecodeError:
        return Decimal(0)
    if not isinstance(payload, dict):
        return Decimal(0)
    price = _decimal(payload.get("px"))
    size = _decimal(payload.get("sz"))
    if price is None or size is None or price < 0 or size < 0:
        return Decimal(0)
    return price * size


@dataclass(slots=True)
class _OrderedMarketAccumulator:
    coin: str
    previous_book_ts_ms: int
    spread_handle: TextIO
    latency_handle: TextIO
    message_count: int = 0
    channel_counts: Counter[str] = field(default_factory=Counter)
    last_receive_ts_ms: int | None = None
    latency_count: int = 0
    spread_count: int = 0
    negative_latency_count: int = 0
    gap_count: int = 0
    major_gap_count: int = 0
    collector_outage_gap_count: int = 0
    collector_not_running_gap_count: int = 0
    market_stale_gap_count: int = 0
    stale_duration_ms: int = 0
    retained_gaps: list[QualityGap] = field(default_factory=list)
    bid_depth_sum: Decimal = Decimal(0)
    bid_depth_count: int = 0
    ask_depth_sum: Decimal = Decimal(0)
    ask_depth_count: int = 0
    trade_count: int = 0
    trade_notional_usd: Decimal = Decimal(0)


def _record_gap(
    accumulator: _OrderedMarketAccumulator,
    gap: QualityGap,
) -> None:
    """Record exact aggregates while bounding serialized gap details."""

    accumulator.gap_count += 1
    accumulator.stale_duration_ms += gap.duration_ms
    if gap.major:
        accumulator.major_gap_count += 1
    if gap.cause is GapCause.COLLECTOR_OUTAGE:
        accumulator.collector_outage_gap_count += 1
    elif gap.cause is GapCause.COLLECTOR_NOT_RUNNING:
        accumulator.collector_not_running_gap_count += 1
    else:
        accumulator.market_stale_gap_count += 1
    if len(accumulator.retained_gaps) < _MAX_GAP_DETAILS_PER_MARKET:
        accumulator.retained_gaps.append(gap)


def _external_percentiles(
    handle: TextIO,
    *,
    count: int,
    percentiles: Sequence[Decimal],
    parse: Callable[[str], _Numeric],
    chunk_root: Path,
    chunk_prefix: str,
    progress: Callable[[], None] | None = None,
) -> tuple[_Numeric | None, ...]:
    """Compute exact percentiles with a bounded-memory external merge sort."""

    if count == 0:
        return tuple(None for _ in percentiles)
    targets: list[tuple[int, int, Decimal]] = []
    required_indices: set[int] = set()
    for percentile in percentiles:
        position = percentile * Decimal(count - 1)
        lower = int(position)
        upper = min(lower + 1, count - 1)
        targets.append((lower, upper, position - lower))
        required_indices.update((lower, upper))

    handle.seek(0)
    chunk_paths: list[Path] = []
    chunk_values: list[_Numeric] = []
    ordered_values: dict[int, _Numeric]
    try:
        for line in handle:
            raw_value = line.strip()
            if not raw_value:
                continue
            chunk_values.append(parse(raw_value))
            if len(chunk_values) < _PERCENTILE_SORT_CHUNK_VALUES:
                continue
            chunk_values.sort()
            chunk_path = chunk_root / f"{chunk_prefix}-{len(chunk_paths):06d}.sorted"
            chunk_path.write_text(
                "".join(f"{item}\n" for item in chunk_values),
                encoding="ascii",
            )
            chunk_paths.append(chunk_path)
            chunk_values.clear()
            if progress is not None:
                progress()

        if not chunk_paths:
            chunk_values.sort()
            ordered_values = {index: chunk_values[index] for index in required_indices}
        else:
            if chunk_values:
                chunk_values.sort()
                chunk_path = (
                    chunk_root / f"{chunk_prefix}-{len(chunk_paths):06d}.sorted"
                )
                chunk_path.write_text(
                    "".join(f"{item}\n" for item in chunk_values),
                    encoding="ascii",
                )
                chunk_paths.append(chunk_path)
                chunk_values.clear()
            chunk_handles = [path.open(encoding="ascii") for path in chunk_paths]
            try:
                iterators: list[Iterator[_Numeric]] = [
                    _parsed_numeric_lines(current, parse) for current in chunk_handles
                ]
                ordered_values = {}
                maximum_index = max(required_indices)
                for position_index, merged_value in enumerate(heapq.merge(*iterators)):
                    if position_index in required_indices:
                        ordered_values[position_index] = merged_value
                    if position_index >= maximum_index:
                        break
                    if progress is not None and position_index % 50_000 == 0:
                        progress()
            finally:
                for current in chunk_handles:
                    current.close()

        results: list[_Numeric] = []
        for lower, upper, weight in targets:
            lower_value = ordered_values[lower]
            upper_value = ordered_values[upper]
            interpolated = (
                Decimal(lower_value) + Decimal(upper_value - lower_value) * weight
            )
            if isinstance(lower_value, int):
                results.append(int(interpolated.to_integral_value()))
            else:
                results.append(interpolated)
        return tuple(results)
    finally:
        for path in chunk_paths:
            path.unlink(missing_ok=True)


def _gap_between(
    *,
    coin: str,
    previous: int,
    current: int,
    config: QualityConfig,
    outages: Sequence[tuple[int, int]],
    sessions: Sequence[tuple[int, int]],
) -> QualityGap | None:
    gap_begin = previous + config.stale_after_ms
    if current <= gap_begin:
        return None
    interval = (gap_begin, current)
    duration = current - gap_begin
    if _overlaps(interval, outages):
        cause = GapCause.COLLECTOR_OUTAGE
    elif not any(
        session_begin <= gap_begin and current <= session_end
        for session_begin, session_end in sessions
    ):
        cause = GapCause.COLLECTOR_NOT_RUNNING
    else:
        cause = GapCause.MARKET_STALE
    return QualityGap(
        coin=coin,
        begin_ts_ms=gap_begin,
        end_ts_ms=current,
        duration_ms=duration,
        cause=cause,
        major=duration >= config.major_gap_ms,
    )


class DailyQualityAnalyzer:
    def __init__(self, config: QualityConfig) -> None:
        self.config = config

    def analyze(
        self,
        *,
        report_date: date,
        market_events: Iterable[PublicMarketDataEvent],
        control_events: Iterable[CollectorControlEvent],
        generated_at_ms: int,
        run_id: str,
        code_version: str,
        source_config_hash: str,
    ) -> DailyQualityReport:
        """Analyze arbitrary iterables after ordering them deterministically."""

        window_begin = int(
            datetime.combine(report_date, datetime.min.time(), tzinfo=UTC).timestamp()
            * 1000
        )
        window_end = int(
            datetime.combine(
                report_date + timedelta(days=1),
                datetime.min.time(),
                tzinfo=UTC,
            ).timestamp()
            * 1000
        )
        selected_market_events = sorted(
            (
                event
                for event in market_events
                if window_begin <= event.receive_ts_ms < window_end
                and event.coin in self.config.expected_markets
            ),
            key=lambda item: (item.receive_ts_ms, item.local_sequence),
        )
        selected_controls = tuple(
            event
            for event in control_events
            if window_begin <= event.receive_ts_ms <= window_end
        )
        return self.analyze_ordered(
            report_date=report_date,
            market_events=selected_market_events,
            control_events=selected_controls,
            generated_at_ms=generated_at_ms,
            run_id=run_id,
            code_version=code_version,
            source_config_hash=source_config_hash,
        )

    def analyze_ordered(
        self,
        *,
        report_date: date,
        market_events: Iterable[PublicMarketDataEvent],
        control_events: Iterable[CollectorControlEvent],
        generated_at_ms: int,
        run_id: str,
        code_version: str,
        source_config_hash: str,
        spill_root: str | Path | None = None,
        progress: Callable[[int], None] | None = None,
    ) -> DailyQualityReport:
        """Analyze receive-time-ordered events with bounded resident memory."""

        window_begin = int(
            datetime.combine(report_date, datetime.min.time(), tzinfo=UTC).timestamp()
            * 1000
        )
        window_end = int(
            datetime.combine(
                report_date + timedelta(days=1),
                datetime.min.time(),
                tzinfo=UTC,
            ).timestamp()
            * 1000
        )
        selected_controls = tuple(
            event
            for event in control_events
            if window_begin <= event.receive_ts_ms <= window_end
        )
        outages = _collector_outages(selected_controls, window_begin, window_end)
        sessions = _collector_sessions(selected_controls, window_begin, window_end)
        spill_parent = Path(spill_root) if spill_root is not None else None
        if spill_parent is not None:
            spill_parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(
            prefix=f"m3-{report_date.isoformat()}-",
            dir=spill_parent,
        ) as temporary:
            temporary_root = Path(temporary)
            accumulators: dict[str, _OrderedMarketAccumulator] = {}
            handles: list[TextIO] = []
            try:
                for index, coin in enumerate(self.config.expected_markets):
                    spread_handle = (temporary_root / f"{index:04d}.spreads").open(
                        "w+", encoding="ascii"
                    )
                    latency_handle = (temporary_root / f"{index:04d}.latencies").open(
                        "w+", encoding="ascii"
                    )
                    handles.extend((spread_handle, latency_handle))
                    accumulators[coin] = _OrderedMarketAccumulator(
                        coin=coin,
                        previous_book_ts_ms=window_begin,
                        spread_handle=spread_handle,
                        latency_handle=latency_handle,
                    )

                processed_events = 0
                for event in market_events:
                    processed_events += 1
                    if progress is not None and processed_events % 50_000 == 0:
                        progress(processed_events)
                    if not (
                        window_begin <= event.receive_ts_ms < window_end
                        and event.coin in accumulators
                    ):
                        continue
                    accumulator = accumulators[event.coin]
                    if (
                        accumulator.last_receive_ts_ms is not None
                        and event.receive_ts_ms < accumulator.last_receive_ts_ms
                    ):
                        raise ValueError(
                            "ordered quality analysis received decreasing timestamps "
                            f"for {event.coin}"
                        )
                    accumulator.last_receive_ts_ms = event.receive_ts_ms
                    accumulator.message_count += 1
                    accumulator.channel_counts[event.channel] += 1
                    if event.exchange_ts_ms is not None:
                        latency = event.receive_ts_ms - event.exchange_ts_ms
                        if latency < 0:
                            accumulator.negative_latency_count += 1
                        else:
                            accumulator.latency_handle.write(f"{latency}\n")
                            accumulator.latency_count += 1

                    if event.channel in _REQUIRED_BOOK_CHANNELS:
                        if event.receive_ts_ms > accumulator.previous_book_ts_ms:
                            gap = _gap_between(
                                coin=event.coin,
                                previous=accumulator.previous_book_ts_ms,
                                current=event.receive_ts_ms,
                                config=self.config,
                                outages=outages,
                                sessions=sessions,
                            )
                            if gap is not None:
                                _record_gap(accumulator, gap)
                            accumulator.previous_book_ts_ms = event.receive_ts_ms
                        spread, bid_depth, ask_depth = _book_metrics(event)
                        if spread is not None:
                            accumulator.spread_handle.write(f"{spread}\n")
                            accumulator.spread_count += 1
                        if bid_depth is not None:
                            accumulator.bid_depth_sum += bid_depth
                            accumulator.bid_depth_count += 1
                        if ask_depth is not None:
                            accumulator.ask_depth_sum += ask_depth
                            accumulator.ask_depth_count += 1
                    if event.channel == "trades":
                        accumulator.trade_count += 1
                        accumulator.trade_notional_usd += _trade_notional(event)

                if progress is not None:
                    progress(processed_events)
                for accumulator in accumulators.values():
                    final_gap = _gap_between(
                        coin=accumulator.coin,
                        previous=accumulator.previous_book_ts_ms,
                        current=window_end,
                        config=self.config,
                        outages=outages,
                        sessions=sessions,
                    )
                    if final_gap is not None:
                        _record_gap(accumulator, final_gap)
                    accumulator.spread_handle.flush()
                    accumulator.latency_handle.flush()

                all_gaps: list[QualityGap] = []
                market_metrics: list[MarketQuality] = []
                window_duration = window_end - window_begin
                for coin in self.config.expected_markets:
                    accumulator = accumulators[coin]
                    percentile_progress = (
                        (lambda: progress(processed_events))
                        if progress is not None
                        else None
                    )
                    latency_percentiles = _external_percentiles(
                        accumulator.latency_handle,
                        count=accumulator.latency_count,
                        percentiles=(Decimal("0.50"), Decimal("0.95"), Decimal("0.99")),
                        parse=int,
                        chunk_root=temporary_root,
                        chunk_prefix=f"{coin.replace(':', '_')}-latency",
                        progress=percentile_progress,
                    )
                    spread_percentiles = _external_percentiles(
                        accumulator.spread_handle,
                        count=accumulator.spread_count,
                        percentiles=(Decimal("0.10"), Decimal("0.50"), Decimal("0.90")),
                        parse=Decimal,
                        chunk_root=temporary_root,
                        chunk_prefix=f"{coin.replace(':', '_')}-spread",
                        progress=percentile_progress,
                    )
                    all_gaps.extend(accumulator.retained_gaps)
                    coverage = (
                        Decimal(max(0, window_duration - accumulator.stale_duration_ms))
                        / Decimal(window_duration)
                        * 100
                    ).quantize(Decimal("0.001"))
                    market_metrics.append(
                        MarketQuality(
                            coin=coin,
                            message_count=accumulator.message_count,
                            channel_counts=tuple(
                                sorted(accumulator.channel_counts.items())
                            ),
                            coverage_pct=coverage,
                            latency_p50_ms=latency_percentiles[0],
                            latency_p95_ms=latency_percentiles[1],
                            latency_p99_ms=latency_percentiles[2],
                            negative_latency_count=(accumulator.negative_latency_count),
                            gap_count=accumulator.gap_count,
                            major_gap_count=accumulator.major_gap_count,
                            collector_outage_gap_count=(
                                accumulator.collector_outage_gap_count
                            ),
                            collector_not_running_gap_count=(
                                accumulator.collector_not_running_gap_count
                            ),
                            market_stale_gap_count=(accumulator.market_stale_gap_count),
                            stale_duration_ms=accumulator.stale_duration_ms,
                            spread_p10_bps=spread_percentiles[0],
                            spread_p50_bps=spread_percentiles[1],
                            spread_p90_bps=spread_percentiles[2],
                            average_bid_depth=(
                                accumulator.bid_depth_sum / accumulator.bid_depth_count
                                if accumulator.bid_depth_count
                                else None
                            ),
                            average_ask_depth=(
                                accumulator.ask_depth_sum / accumulator.ask_depth_count
                                if accumulator.ask_depth_count
                                else None
                            ),
                            trade_count=accumulator.trade_count,
                            trade_notional_usd=accumulator.trade_notional_usd,
                        )
                    )
            finally:
                for current_handle in handles:
                    current_handle.close()

        reasons: list[str] = []
        for market in market_metrics:
            if market.coverage_pct < self.config.minimum_coverage_pct:
                reasons.append(f"coverage_below_threshold:{market.coin}")
            if market.major_gap_count > 0:
                reasons.append(f"major_gap:{market.coin}")
            if market.negative_latency_count > 0:
                reasons.append(f"negative_latency:{market.coin}")
        total_gap_count = sum(market.gap_count for market in market_metrics)
        return DailyQualityReport(
            schema_version=QUALITY_SCHEMA_VERSION,
            report_date=report_date.isoformat(),
            generated_at_ms=generated_at_ms,
            run_id=run_id,
            code_version=code_version,
            source_config_hash=source_config_hash,
            quality_config_hash=self.config.sha256,
            dataset_tier=DatasetTier.A,
            window_begin_ms=window_begin,
            window_end_ms=window_end,
            collector_outage_count=len(outages),
            collector_outage_duration_ms=sum(end - begin for begin, end in outages),
            total_gap_count=total_gap_count,
            retained_gap_detail_count=len(all_gaps),
            gap_details_truncated=total_gap_count > len(all_gaps),
            gap_detail_limit_per_market=_MAX_GAP_DETAILS_PER_MARKET,
            gaps=tuple(all_gaps),
            markets=tuple(market_metrics),
            qualified_day=not reasons,
            qualification_reasons=tuple(reasons),
        )


def evaluate_quality_gate(
    reports: Iterable[DailyQualityReport],
    *,
    initial_days: int = 7,
    evidence_days: int = 30,
) -> QualityGateResult:
    if initial_days <= 0 or evidence_days < initial_days:
        raise ValueError("quality gate day requirements are invalid")
    by_date = {date.fromisoformat(report.report_date): report for report in reports}
    ordered_dates = sorted(by_date)
    missing: list[str] = []
    if ordered_dates:
        current = ordered_dates[0]
        while current <= ordered_dates[-1]:
            if current not in by_date:
                missing.append(current.isoformat())
            current += timedelta(days=1)
    consecutive = 0
    for report_date in reversed(ordered_dates):
        report = by_date[report_date]
        if not report.qualified_day:
            break
        if consecutive > 0:
            expected = ordered_dates[-1] - timedelta(days=consecutive)
            if report_date != expected:
                break
        consecutive += 1
    if consecutive < initial_days:
        stage = QualityGateStage.INSUFFICIENT_SEVEN_DAYS
    elif consecutive < evidence_days:
        stage = QualityGateStage.COLLECT_TO_THIRTY_DAYS
    else:
        stage = QualityGateStage.THIRTY_DAYS_COMPLETE
    return QualityGateResult(
        stage=stage,
        observed_days=len(by_date),
        consecutive_qualified_days=consecutive,
        required_initial_days=initial_days,
        required_evidence_days=evidence_days,
        missing_dates=tuple(missing),
    )


def quality_report_payload(report: DailyQualityReport) -> dict[str, object]:
    def encode(value: object) -> object:
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, StrEnum):
            return value.value
        if isinstance(value, tuple | list):
            return [encode(item) for item in value]
        if isinstance(value, dict):
            return {str(key): encode(item) for key, item in value.items()}
        return value

    encoded = encode(asdict(report))
    if not isinstance(encoded, dict):
        raise TypeError("quality report must encode to an object")
    return cast(dict[str, object], encoded)


def write_daily_quality_report(
    report: DailyQualityReport,
    output_root: str | Path,
) -> tuple[Path, Path]:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    stem = f"quality-{report.report_date}-{report.run_id}"
    json_path = root / f"{stem}.json"
    markdown_path = root / f"{stem}.md"
    if json_path.exists() or markdown_path.exists():
        raise FileExistsError(f"quality report already exists: {stem}")
    payload = quality_report_payload(report)
    json_bytes = (
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )
    json_path.write_bytes(json_bytes)
    json_path.with_suffix(".json.sha256").write_text(
        f"{hashlib.sha256(json_bytes).hexdigest()}  {json_path.name}\n",
        encoding="ascii",
    )
    lines = [
        f"# Qualité des données — {report.report_date}",
        "",
        f"- run : `{report.run_id}` ;",
        f"- niveau : `{report.dataset_tier.value}` ;",
        f"- journée qualifiée : `{str(report.qualified_day).lower()}` ;",
        f"- pannes collector : {report.collector_outage_count} ;",
        f"- durée de panne : {report.collector_outage_duration_ms} ms ;",
        f"- gaps exacts : {report.total_gap_count} ;",
        (
            "- détails de gaps conservés : "
            f"{report.retained_gap_detail_count} "
            f"(tronqués : {str(report.gap_details_truncated).lower()})."
        ),
        "",
        "| Marché | Couverture | Latence p50/p95/p99 | Gaps majeurs | Trades |",
        "|---|---:|---:|---:|---:|",
    ]
    for market in report.markets:
        latency = "/".join(
            "n/a" if value is None else str(value)
            for value in (
                market.latency_p50_ms,
                market.latency_p95_ms,
                market.latency_p99_ms,
            )
        )
        lines.append(
            f"| {market.coin} | {market.coverage_pct}% | {latency} ms | "
            f"{market.major_gap_count} | {market.trade_count} |"
        )
    if report.qualification_reasons:
        lines.extend(("", "## Raisons de non-qualification", ""))
        lines.extend(f"- `{reason}`" for reason in report.qualification_reasons)
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path


def market_event_from_payload(payload: Mapping[str, object]) -> PublicMarketDataEvent:
    return PublicMarketDataEvent(
        context=_context_from_payload(payload.get("context")),
        channel=str(payload["channel"]),
        coin=str(payload["coin"]),
        exchange_ts_ms=cast(int | None, payload.get("exchange_ts_ms")),
        receive_ts_ms=int(cast(int, payload["receive_ts_ms"])),
        receive_monotonic_ns=int(cast(int, payload["receive_monotonic_ns"])),
        local_sequence=int(cast(int, payload["local_sequence"])),
        payload_json=str(payload["payload_json"]),
    )


def control_event_from_payload(payload: Mapping[str, object]) -> CollectorControlEvent:
    return CollectorControlEvent(
        context=_context_from_payload(payload.get("context")),
        kind=CollectorControlKind(str(payload["kind"])),
        receive_ts_ms=int(cast(int, payload["receive_ts_ms"])),
        receive_monotonic_ns=int(cast(int, payload["receive_monotonic_ns"])),
        connection_attempt=int(cast(int, payload["connection_attempt"])),
        dropped_messages=int(cast(int, payload["dropped_messages"])),
        reason=cast(str | None, payload.get("reason")),
    )


def _context_from_payload(value: object) -> EventContext:
    if not isinstance(value, dict):
        raise ValueError("event context is missing")
    payload = cast(dict[str, object], value)
    return EventContext(
        run_id=str(payload["run_id"]),
        code_version=str(payload["code_version"]),
        config_hash=str(payload["config_hash"]),
        time_source=TimeSource(str(payload["time_source"])),
    )
