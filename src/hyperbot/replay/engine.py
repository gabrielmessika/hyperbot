"""Bit-reproducible replay with explicit queue and latency assumptions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields, is_dataclass, replace
from decimal import Decimal
from enum import Enum, StrEnum
from typing import TypeAlias, cast

from hyperbot.legacy.policy import ReplayUse, require_replay_use
from hyperbot.models import BookLevel, DatasetTier, Side

REPLAY_SCHEMA_VERSION = 1
MARKOUT_HORIZONS_MS = (100, 1_000, 5_000, 30_000)


class ReplayError(RuntimeError):
    """Base deterministic replay error."""


class ReplayDataError(ReplayError):
    """Raised when queue evidence is absent or input ordering is ambiguous."""


class FillModelKind(StrEnum):
    PESSIMISTIC = "pessimistic"
    CENTRAL = "central"
    OPTIMISTIC_TOUCH = "optimistic_touch"


@dataclass(frozen=True, slots=True)
class ReplayBook:
    market: str
    timestamp_ms: int
    source_sequence: int
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    receive_ts_ms: int | None = None

    def __post_init__(self) -> None:
        if not self.market.strip():
            raise ValueError("market must not be empty")
        if self.timestamp_ms < 0 or self.source_sequence < 0:
            raise ValueError("book timestamp and sequence must be non-negative")
        if self.receive_ts_ms is not None and self.receive_ts_ms < self.timestamp_ms:
            raise ValueError("book receive timestamp cannot precede exchange time")
        if self.bids and self.asks and self.bids[0].price > self.asks[0].price:
            raise ValueError("book must not be crossed")

    @property
    def midpoint(self) -> Decimal | None:
        if not self.bids or not self.asks:
            return None
        return (self.bids[0].price + self.asks[0].price) / 2

    @property
    def observed_ts_ms(self) -> int:
        return (
            self.receive_ts_ms if self.receive_ts_ms is not None else self.timestamp_ms
        )


@dataclass(frozen=True, slots=True)
class ReplayTrade:
    market: str
    timestamp_ms: int
    source_sequence: int
    aggressor_side: Side
    price: Decimal
    size: Decimal
    receive_ts_ms: int | None = None

    def __post_init__(self) -> None:
        if not self.market.strip():
            raise ValueError("market must not be empty")
        if self.timestamp_ms < 0 or self.source_sequence < 0:
            raise ValueError("trade timestamp and sequence must be non-negative")
        if self.receive_ts_ms is not None and self.receive_ts_ms < self.timestamp_ms:
            raise ValueError("trade receive timestamp cannot precede exchange time")
        if not self.price.is_finite() or self.price <= 0:
            raise ValueError("trade price must be finite and positive")
        if not self.size.is_finite() or self.size <= 0:
            raise ValueError("trade size must be finite and positive")


ReplayMarketEvent: TypeAlias = ReplayBook | ReplayTrade


@dataclass(frozen=True, slots=True)
class ReplayQuote:
    quote_id: str
    market: str
    side: Side
    price: Decimal
    size: Decimal
    submitted_ts_ms: int
    cancel_requested_ts_ms: int | None
    maker_fee_bps: Decimal

    def __post_init__(self) -> None:
        if not self.quote_id.strip() or not self.market.strip():
            raise ValueError("quote_id and market must not be empty")
        if not self.price.is_finite() or self.price <= 0:
            raise ValueError("quote price must be finite and positive")
        if not self.size.is_finite() or self.size <= 0:
            raise ValueError("quote size must be finite and positive")
        if self.submitted_ts_ms < 0:
            raise ValueError("submitted_ts_ms must be non-negative")
        if (
            self.cancel_requested_ts_ms is not None
            and self.cancel_requested_ts_ms < self.submitted_ts_ms
        ):
            raise ValueError("cancel cannot precede quote submission")
        if not self.maker_fee_bps.is_finite() or self.maker_fee_bps < 0:
            raise ValueError("maker_fee_bps must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class ReplayConfig:
    run_id: str
    code_version: str
    model: FillModelKind
    dataset_tiers: tuple[DatasetTier, ...]
    placement_latency_ms: int
    cancel_latency_ms: int
    central_queue_fraction: Decimal = Decimal("0.5")
    markout_tolerance_ms: int = 250

    def __post_init__(self) -> None:
        if not self.run_id.strip() or not self.code_version.strip():
            raise ValueError("run_id and code_version must not be empty")
        if not self.dataset_tiers:
            raise ValueError("dataset_tiers must not be empty")
        if self.placement_latency_ms < 0 or self.cancel_latency_ms < 0:
            raise ValueError("latencies must be non-negative")
        if self.markout_tolerance_ms < 0:
            raise ValueError("markout_tolerance_ms must be non-negative")
        if not Decimal(0) <= self.central_queue_fraction <= Decimal(1):
            raise ValueError("central_queue_fraction must be between zero and one")

    @property
    def sha256(self) -> str:
        return _hash(self)


@dataclass(frozen=True, slots=True)
class SimulatedFill:
    quote_id: str
    market: str
    side: Side
    fill_ts_ms: int
    price: Decimal
    size: Decimal
    fee_usd: Decimal
    queue_ahead_before: Decimal
    queue_ahead_after: Decimal
    model: FillModelKind
    markout_100ms: Decimal | None
    markout_1s: Decimal | None
    markout_5s: Decimal | None
    markout_30s: Decimal | None


@dataclass(frozen=True, slots=True)
class ReplayResult:
    schema_version: int
    run_id: str
    code_version: str
    config_sha256: str
    input_sha256: str
    model: FillModelKind
    evidence_label: str | None
    fills: tuple[SimulatedFill, ...]
    filled_notional_usd: Decimal
    fees_usd: Decimal
    gross_markout_30s_usd: Decimal
    economic_pnl_30s_usd: Decimal
    fills_missing_30s_markout: int
    result_sha256: str


@dataclass(frozen=True, slots=True)
class StressReplayResult:
    base: ReplayResult
    double_latency: ReplayResult
    double_fees: ReplayResult


@dataclass(slots=True)
class _QuoteState:
    quote: ReplayQuote
    active_ts_ms: int
    cancel_effective_ts_ms: int | None
    remaining_size: Decimal
    queue_ahead: Decimal | None


class VirtualClock:
    """Monotonic replay clock advanced only by ordered input events."""

    def __init__(self, initial_ts_ms: int = 0) -> None:
        if initial_ts_ms < 0:
            raise ValueError("initial timestamp must be non-negative")
        self._now_ms = initial_ts_ms

    @property
    def now_ms(self) -> int:
        return self._now_ms

    def advance_to(self, timestamp_ms: int) -> None:
        if timestamp_ms < self._now_ms:
            raise ReplayDataError("virtual clock cannot move backwards")
        self._now_ms = timestamp_ms


def _encode(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, tuple | list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _encode(getattr(value, field.name)) for field in fields(value)
        }
    return value


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        _encode(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _hash(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _event_sort_key(event: ReplayMarketEvent) -> tuple[int, int, str, int]:
    type_order = 0 if isinstance(event, ReplayBook) else 1
    return event.timestamp_ms, event.source_sequence, event.market, type_order


def _visible_size(book: ReplayBook, quote: ReplayQuote) -> Decimal:
    levels = book.bids if quote.side is Side.BUY else book.asks
    return sum(
        (level.size for level in levels if level.price == quote.price),
        Decimal(0),
    )


def _trade_reaches_quote(trade: ReplayTrade, quote: ReplayQuote) -> bool:
    if quote.market != trade.market:
        return False
    if quote.side is Side.BUY:
        return trade.aggressor_side is Side.SELL and trade.price <= quote.price
    return trade.aggressor_side is Side.BUY and trade.price >= quote.price


def _book_crosses_quote(book: ReplayBook, quote: ReplayQuote) -> bool:
    if quote.market != book.market:
        return False
    if quote.side is Side.BUY:
        return bool(book.asks and book.asks[0].price <= quote.price)
    return bool(book.bids and book.bids[0].price >= quote.price)


def _active_at(state: _QuoteState, timestamp_ms: int) -> bool:
    if timestamp_ms < state.active_ts_ms or state.remaining_size <= 0:
        return False
    return (
        state.cancel_effective_ts_ms is None
        or timestamp_ms <= state.cancel_effective_ts_ms
    )


def _markout(
    fill: SimulatedFill,
    books_by_market: dict[str, tuple[ReplayBook, ...]],
    horizon_ms: int,
    tolerance_ms: int,
) -> Decimal | None:
    target = fill.fill_ts_ms + horizon_ms
    for book in books_by_market.get(fill.market, ()):
        if book.timestamp_ms < target:
            continue
        if book.timestamp_ms > target + tolerance_ms:
            return None
        midpoint = book.midpoint
        if midpoint is None:
            continue
        if fill.side is Side.BUY:
            return (midpoint - fill.price) * fill.size
        return (fill.price - midpoint) * fill.size
    return None


class ReplayEngine:
    def run(
        self,
        *,
        config: ReplayConfig,
        events: tuple[ReplayMarketEvent, ...],
        quotes: tuple[ReplayQuote, ...],
    ) -> ReplayResult:
        replay_use = {
            FillModelKind.PESSIMISTIC: ReplayUse.PESSIMISTIC_FILL_MODEL,
            FillModelKind.CENTRAL: ReplayUse.CENTRAL_FILL_MODEL,
            FillModelKind.OPTIMISTIC_TOUCH: ReplayUse.OPTIMISTIC_TOUCH,
        }[config.model]
        authorization = require_replay_use(replay_use, config.dataset_tiers)
        if len({quote.quote_id for quote in quotes}) != len(quotes):
            raise ReplayDataError("quote IDs must be unique")
        event_keys = [_event_sort_key(event) for event in events]
        if len(set(event_keys)) != len(event_keys):
            raise ReplayDataError("market event ordering keys must be unique")
        ordered_events = tuple(sorted(events, key=_event_sort_key))
        ordered_quotes = tuple(
            sorted(quotes, key=lambda item: (item.submitted_ts_ms, item.quote_id))
        )
        states = [
            _QuoteState(
                quote=quote,
                active_ts_ms=quote.submitted_ts_ms + config.placement_latency_ms,
                cancel_effective_ts_ms=(
                    quote.cancel_requested_ts_ms + config.cancel_latency_ms
                    if quote.cancel_requested_ts_ms is not None
                    else None
                ),
                remaining_size=quote.size,
                queue_ahead=None,
            )
            for quote in ordered_quotes
        ]
        clock = VirtualClock()
        latest_books: dict[str, ReplayBook] = {}
        raw_fills: list[SimulatedFill] = []
        for event in ordered_events:
            clock.advance_to(event.timestamp_ms)
            if isinstance(event, ReplayBook):
                previous_book = latest_books.get(event.market)
                self._update_queues(config, states, event, previous_book)
                latest_books[event.market] = event
                if config.model is FillModelKind.OPTIMISTIC_TOUCH:
                    for state in states:
                        is_active = _active_at(state, event.timestamp_ms)
                        if is_active and _book_crosses_quote(event, state.quote):
                            raw_fills.append(
                                self._fill_state(
                                    config.model,
                                    state,
                                    event.timestamp_ms,
                                    state.remaining_size,
                                )
                            )
                continue
            self._initialize_queues(
                config,
                states,
                latest_books,
                event.timestamp_ms,
                market=event.market,
            )
            if config.model is FillModelKind.OPTIMISTIC_TOUCH:
                for state in states:
                    if _active_at(state, event.timestamp_ms) and _trade_reaches_quote(
                        event, state.quote
                    ):
                        raw_fills.append(
                            self._fill_state(
                                config.model,
                                state,
                                event.timestamp_ms,
                                state.remaining_size,
                            )
                        )
                continue
            available = event.size
            candidates = [
                state
                for state in states
                if _active_at(state, event.timestamp_ms)
                and _trade_reaches_quote(event, state.quote)
            ]
            candidates.sort(key=self._queue_priority)
            for state in candidates:
                if available <= 0:
                    break
                if state.queue_ahead is None:
                    raise ReplayDataError(
                        f"no L2 queue evidence for active quote {state.quote.quote_id}"
                    )
                queue_before = state.queue_ahead
                queue_consumed = min(state.queue_ahead, available)
                state.queue_ahead -= queue_consumed
                available -= queue_consumed
                if available <= 0 or state.queue_ahead > 0:
                    continue
                fill_size = min(state.remaining_size, available)
                raw_fills.append(
                    self._fill_state(
                        config.model,
                        state,
                        event.timestamp_ms,
                        fill_size,
                        queue_ahead_before=queue_before,
                    )
                )
                available -= fill_size

        books_by_market: dict[str, tuple[ReplayBook, ...]] = {}
        for market in {event.market for event in ordered_events}:
            books_by_market[market] = tuple(
                event
                for event in ordered_events
                if isinstance(event, ReplayBook) and event.market == market
            )
        fills = tuple(
            replace(
                fill,
                markout_100ms=_markout(
                    fill, books_by_market, 100, config.markout_tolerance_ms
                ),
                markout_1s=_markout(
                    fill, books_by_market, 1_000, config.markout_tolerance_ms
                ),
                markout_5s=_markout(
                    fill, books_by_market, 5_000, config.markout_tolerance_ms
                ),
                markout_30s=_markout(
                    fill, books_by_market, 30_000, config.markout_tolerance_ms
                ),
            )
            for fill in raw_fills
        )
        fees = sum((fill.fee_usd for fill in fills), Decimal(0))
        gross_markout = sum(
            (fill.markout_30s for fill in fills if fill.markout_30s is not None),
            Decimal(0),
        )
        input_hash = _hash({"events": ordered_events, "quotes": ordered_quotes})
        filled_notional = sum((fill.price * fill.size for fill in fills), Decimal(0))
        fills_missing_markout = sum(fill.markout_30s is None for fill in fills)
        without_hash = {
            "schema_version": REPLAY_SCHEMA_VERSION,
            "run_id": config.run_id,
            "code_version": config.code_version,
            "config_sha256": config.sha256,
            "input_sha256": input_hash,
            "model": config.model,
            "evidence_label": authorization.required_label,
            "fills": fills,
            "filled_notional_usd": filled_notional,
            "fees_usd": fees,
            "gross_markout_30s_usd": gross_markout,
            "economic_pnl_30s_usd": gross_markout - fees,
            "fills_missing_30s_markout": fills_missing_markout,
        }
        return ReplayResult(
            schema_version=REPLAY_SCHEMA_VERSION,
            run_id=config.run_id,
            code_version=config.code_version,
            config_sha256=config.sha256,
            input_sha256=input_hash,
            model=config.model,
            evidence_label=authorization.required_label,
            fills=fills,
            filled_notional_usd=filled_notional,
            fees_usd=fees,
            gross_markout_30s_usd=gross_markout,
            economic_pnl_30s_usd=gross_markout - fees,
            fills_missing_30s_markout=fills_missing_markout,
            result_sha256=_hash(without_hash),
        )

    def run_stress(
        self,
        *,
        config: ReplayConfig,
        events: tuple[ReplayMarketEvent, ...],
        quotes: tuple[ReplayQuote, ...],
    ) -> StressReplayResult:
        base = self.run(config=config, events=events, quotes=quotes)
        latency_config = replace(
            config,
            run_id=f"{config.run_id}-latency-2x",
            placement_latency_ms=config.placement_latency_ms * 2,
            cancel_latency_ms=config.cancel_latency_ms * 2,
        )
        double_latency = self.run(
            config=latency_config,
            events=events,
            quotes=quotes,
        )
        fee_quotes = tuple(
            replace(quote, maker_fee_bps=quote.maker_fee_bps * 2) for quote in quotes
        )
        fee_config = replace(config, run_id=f"{config.run_id}-fees-2x")
        double_fees = self.run(
            config=fee_config,
            events=events,
            quotes=fee_quotes,
        )
        return StressReplayResult(base, double_latency, double_fees)

    def _initialize_queues(
        self,
        config: ReplayConfig,
        states: list[_QuoteState],
        latest_books: dict[str, ReplayBook],
        timestamp_ms: int,
        *,
        market: str,
    ) -> None:
        if config.model is FillModelKind.OPTIMISTIC_TOUCH:
            return
        for state in states:
            if (
                state.quote.market != market
                or not _active_at(state, timestamp_ms)
                or state.queue_ahead is not None
            ):
                continue
            book = latest_books.get(state.quote.market)
            if book is None or book.timestamp_ms > state.active_ts_ms:
                raise ReplayDataError(
                    f"no L2 book at activation for quote {state.quote.quote_id}"
                )
            visible = _visible_size(book, state.quote)
            state.queue_ahead = (
                visible
                if config.model is FillModelKind.PESSIMISTIC
                else visible * config.central_queue_fraction
            )

    def _update_queues(
        self,
        config: ReplayConfig,
        states: list[_QuoteState],
        book: ReplayBook,
        previous_book: ReplayBook | None,
    ) -> None:
        if config.model is FillModelKind.OPTIMISTIC_TOUCH:
            return
        for state in states:
            if state.quote.market == book.market and _active_at(
                state, book.timestamp_ms
            ):
                if state.queue_ahead is None:
                    reference = previous_book
                    if reference is None and book.timestamp_ms == state.active_ts_ms:
                        reference = book
                    if reference is None:
                        raise ReplayDataError(
                            f"no L2 book at activation for quote {state.quote.quote_id}"
                        )
                    visible = _visible_size(reference, state.quote)
                    state.queue_ahead = (
                        visible
                        if config.model is FillModelKind.PESSIMISTIC
                        else visible * config.central_queue_fraction
                    )
                if config.model is not FillModelKind.CENTRAL:
                    continue
                state.queue_ahead = min(
                    state.queue_ahead,
                    _visible_size(book, state.quote),
                )

    def _fill_state(
        self,
        model: FillModelKind,
        state: _QuoteState,
        timestamp_ms: int,
        size: Decimal,
        *,
        queue_ahead_before: Decimal | None = None,
    ) -> SimulatedFill:
        queue_before = (
            queue_ahead_before
            if queue_ahead_before is not None
            else state.queue_ahead or Decimal(0)
        )
        state.remaining_size -= size
        fee = state.quote.price * size * state.quote.maker_fee_bps / Decimal(10_000)
        return SimulatedFill(
            quote_id=state.quote.quote_id,
            market=state.quote.market,
            side=state.quote.side,
            fill_ts_ms=timestamp_ms,
            price=state.quote.price,
            size=size,
            fee_usd=fee,
            queue_ahead_before=queue_before,
            queue_ahead_after=state.queue_ahead or Decimal(0),
            model=model,
            markout_100ms=None,
            markout_1s=None,
            markout_5s=None,
            markout_30s=None,
        )

    def _queue_priority(self, state: _QuoteState) -> tuple[str, Decimal, int, str]:
        price_priority = (
            -state.quote.price if state.quote.side is Side.BUY else state.quote.price
        )
        return (
            state.quote.market,
            price_priority,
            state.active_ts_ms,
            state.quote.quote_id,
        )


def replay_result_payload(result: ReplayResult) -> dict[str, object]:
    encoded = _encode(result)
    if not isinstance(encoded, dict):
        raise TypeError("replay result must encode to an object")
    return cast(dict[str, object], encoded)
