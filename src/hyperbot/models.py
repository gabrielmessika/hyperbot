"""Immutable domain events shared by collection, replay, and execution."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import Enum, StrEnum
from typing import TypeAlias

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]


class Side(StrEnum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(StrEnum):
    PENDING = "pending"
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCEL_PENDING = "cancel_pending"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class OutcomeResult(StrEnum):
    YES = "yes"
    NO = "no"
    INVALID = "invalid"


class TimeSource(StrEnum):
    EXCHANGE = "exchange"
    LOCAL_MONOTONIC = "local_monotonic"
    REPLAY = "replay"


class DatasetTier(StrEnum):
    """Evidence tier attached to collected and legacy datasets."""

    A = "A"
    B = "B"
    C = "C"


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")


def _require_finite(value: Decimal, field_name: str) -> None:
    if not value.is_finite():
        raise ValueError(f"{field_name} must be finite")


def _require_positive(value: Decimal, field_name: str) -> None:
    _require_finite(value, field_name)
    if value <= 0:
        raise ValueError(f"{field_name} must be positive")


def _require_non_negative(value: Decimal, field_name: str) -> None:
    _require_finite(value, field_name)
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative")


def _require_optional_positive(value: Decimal | None, field_name: str) -> None:
    if value is not None:
        _require_positive(value, field_name)


def _require_optional_non_negative(value: Decimal | None, field_name: str) -> None:
    if value is not None:
        _require_non_negative(value, field_name)


def _require_optional_non_empty(value: str | None, field_name: str) -> None:
    if value is not None:
        _require_non_empty(value, field_name)


@dataclass(frozen=True, slots=True)
class EventContext:
    """Reproducibility metadata carried by every domain event."""

    run_id: str
    code_version: str
    config_hash: str
    time_source: TimeSource

    def __post_init__(self) -> None:
        _require_non_empty(self.run_id, "run_id")
        _require_non_empty(self.code_version, "code_version")
        _require_non_empty(self.config_hash, "config_hash")


@dataclass(frozen=True, slots=True)
class BookLevel:
    price: Decimal
    size: Decimal
    order_count: int | None = None

    def __post_init__(self) -> None:
        _require_positive(self.price, "price")
        _require_positive(self.size, "size")
        if self.order_count is not None and self.order_count < 0:
            raise ValueError("order_count must be non-negative")


@dataclass(frozen=True, slots=True)
class BookEvent:
    context: EventContext
    exchange_ts_ms: int
    receive_ts_ms: int
    dex: str
    asset: str
    sequence: int
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]
    oracle_px: Decimal | None
    mark_px: Decimal | None

    def __post_init__(self) -> None:
        if self.exchange_ts_ms < 0 or self.receive_ts_ms < 0:
            raise ValueError("timestamps must be non-negative")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        _require_non_empty(self.dex, "dex")
        _require_non_empty(self.asset, "asset")
        if self.oracle_px is not None:
            _require_positive(self.oracle_px, "oracle_px")
        if self.mark_px is not None:
            _require_positive(self.mark_px, "mark_px")


@dataclass(frozen=True, slots=True)
class QuoteIntent:
    context: EventContext
    intent_id: str
    strategy: str
    market: str
    side: Side
    price: Decimal
    size: Decimal
    ttl_ms: int
    fair_value: Decimal
    min_edge_bps: Decimal
    inventory_before: Decimal
    reason_codes: tuple[str, ...]

    def __post_init__(self) -> None:
        _require_non_empty(self.intent_id, "intent_id")
        _require_non_empty(self.strategy, "strategy")
        _require_non_empty(self.market, "market")
        _require_positive(self.price, "price")
        _require_positive(self.size, "size")
        _require_positive(self.fair_value, "fair_value")
        _require_non_negative(self.min_edge_bps, "min_edge_bps")
        _require_finite(self.inventory_before, "inventory_before")
        if self.ttl_ms <= 0:
            raise ValueError("ttl_ms must be positive")
        if not self.reason_codes:
            raise ValueError("reason_codes must not be empty")
        for reason_code in self.reason_codes:
            _require_non_empty(reason_code, "reason_code")


@dataclass(frozen=True, slots=True)
class OrderLifecycle:
    context: EventContext
    client_order_id: str
    exchange_order_id: str | None
    intent_id: str
    sent_ts_ms: int
    ack_ts_ms: int | None
    cancel_ts_ms: int | None
    status: OrderStatus
    reject_reason: str | None

    def __post_init__(self) -> None:
        _require_non_empty(self.client_order_id, "client_order_id")
        _require_non_empty(self.intent_id, "intent_id")
        if self.exchange_order_id is not None:
            _require_non_empty(self.exchange_order_id, "exchange_order_id")
        for field_name, value in (
            ("sent_ts_ms", self.sent_ts_ms),
            ("ack_ts_ms", self.ack_ts_ms),
            ("cancel_ts_ms", self.cancel_ts_ms),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{field_name} must be non-negative")
        if self.status is OrderStatus.REJECTED and not self.reject_reason:
            raise ValueError("a rejected order requires reject_reason")


@dataclass(frozen=True, slots=True)
class FillAttribution:
    context: EventContext
    order_id: str
    fill_ts_ms: int
    price: Decimal
    size: Decimal
    fee_usd: Decimal
    estimated_queue_ahead: Decimal
    markout_100ms: Decimal | None
    markout_1s: Decimal | None
    markout_5s: Decimal | None
    markout_30s: Decimal | None

    def __post_init__(self) -> None:
        _require_non_empty(self.order_id, "order_id")
        if self.fill_ts_ms < 0:
            raise ValueError("fill_ts_ms must be non-negative")
        _require_positive(self.price, "price")
        _require_positive(self.size, "size")
        _require_finite(self.fee_usd, "fee_usd")
        _require_non_negative(self.estimated_queue_ahead, "estimated_queue_ahead")
        for field_name, value in (
            ("markout_100ms", self.markout_100ms),
            ("markout_1s", self.markout_1s),
            ("markout_5s", self.markout_5s),
            ("markout_30s", self.markout_30s),
        ):
            if value is not None:
                _require_finite(value, field_name)


@dataclass(frozen=True, slots=True)
class OutcomeSettlement:
    context: EventContext
    market: str
    expiry_ts_ms: int
    strike: Decimal
    result: OutcomeResult
    payout_usd: Decimal
    settlement_fee_usd: Decimal

    def __post_init__(self) -> None:
        _require_non_empty(self.market, "market")
        if self.expiry_ts_ms < 0:
            raise ValueError("expiry_ts_ms must be non-negative")
        _require_positive(self.strike, "strike")
        _require_non_negative(self.payout_usd, "payout_usd")
        _require_non_negative(self.settlement_fee_usd, "settlement_fee_usd")


@dataclass(frozen=True, slots=True)
class LegacyProvenance:
    """Verifiable origin attached to every normalized legacy event."""

    dataset_tier: DatasetTier
    source_path: str
    source_sha256: str
    source_record_number: int
    source_record_sha256: str
    source_record_hash_kind: str
    subrecord_index: int
    adapter_name: str
    adapter_version: str
    quality_flags: tuple[str, ...]
    legacy_research_only: bool

    def __post_init__(self) -> None:
        _require_non_empty(self.source_path, "source_path")
        _require_non_empty(self.adapter_name, "adapter_name")
        _require_non_empty(self.adapter_version, "adapter_version")
        if self.source_record_hash_kind not in {
            "raw_line_sha256",
            "canonical_json_sha256",
        }:
            raise ValueError("unsupported source_record_hash_kind")
        for field_name, value in (
            ("source_sha256", self.source_sha256),
            ("source_record_sha256", self.source_record_sha256),
        ):
            invalid_character = any(
                char not in "0123456789abcdef" for char in value
            )
            if len(value) != 64 or invalid_character:
                raise ValueError(f"{field_name} must be a lowercase SHA-256")
        if self.source_record_number <= 0:
            raise ValueError("source_record_number must be positive")
        if self.subrecord_index < 0:
            raise ValueError("subrecord_index must be non-negative")
        if not self.legacy_research_only:
            raise ValueError("legacy provenance must remain research-only")
        if "legacy_research_only" not in self.quality_flags:
            raise ValueError("quality_flags must include legacy_research_only")
        for flag in self.quality_flags:
            _require_non_empty(flag, "quality_flag")


@dataclass(frozen=True, slots=True)
class LegacyBookObservation:
    """Partial BBO/depth observation that never implies a full L2 book."""

    context: EventContext
    provenance: LegacyProvenance
    exchange_ts_ms: int
    dex: str
    asset: str
    market_id: str | None
    outcome_side: str | None
    best_bid: Decimal | None
    best_ask: Decimal | None
    bid_size: Decimal | None
    ask_size: Decimal | None
    bid_depth: Decimal | None
    ask_depth: Decimal | None

    def __post_init__(self) -> None:
        if self.exchange_ts_ms < 0:
            raise ValueError("exchange_ts_ms must be non-negative")
        _require_non_empty(self.dex, "dex")
        _require_non_empty(self.asset, "asset")
        _require_optional_non_empty(self.market_id, "market_id")
        _require_optional_non_empty(self.outcome_side, "outcome_side")
        _require_optional_positive(self.best_bid, "best_bid")
        _require_optional_positive(self.best_ask, "best_ask")
        for field_name, value in (
            ("bid_size", self.bid_size),
            ("ask_size", self.ask_size),
            ("bid_depth", self.bid_depth),
            ("ask_depth", self.ask_depth),
        ):
            _require_optional_non_negative(value, field_name)
        if (
            self.best_bid is not None
            and self.best_ask is not None
            and self.best_bid > self.best_ask
        ):
            raise ValueError("best_bid cannot exceed best_ask")


@dataclass(frozen=True, slots=True)
class LegacyTradeObservation:
    """Normalized trade with explicit units for every available quantity."""

    context: EventContext
    provenance: LegacyProvenance
    exchange_ts_ms: int
    dex: str
    asset: str
    market_id: str | None
    side: Side | None
    price: Decimal
    base_size: Decimal | None
    token_size: Decimal | None
    notional_usd: Decimal | None

    def __post_init__(self) -> None:
        if self.exchange_ts_ms < 0:
            raise ValueError("exchange_ts_ms must be non-negative")
        _require_non_empty(self.dex, "dex")
        _require_non_empty(self.asset, "asset")
        _require_optional_non_empty(self.market_id, "market_id")
        _require_positive(self.price, "price")
        for field_name, value in (
            ("base_size", self.base_size),
            ("token_size", self.token_size),
            ("notional_usd", self.notional_usd),
        ):
            _require_optional_positive(value, field_name)
        quantities = (self.base_size, self.token_size, self.notional_usd)
        if all(value is None for value in quantities):
            raise ValueError("a trade requires at least one explicit quantity")


@dataclass(frozen=True, slots=True)
class LegacyQuoteObservation:
    """Historical paper/shadow quote observation, never an executable intent."""

    context: EventContext
    provenance: LegacyProvenance
    exchange_ts_ms: int
    market_id: str
    asset: str | None
    side_label: str
    decision_approved: bool | None
    would_quote: bool | None
    bid: Decimal | None
    ask: Decimal | None
    maker_price: Decimal | None
    quote_size_usd: Decimal | None
    model_probability: Decimal | None
    reference_price: Decimal | None
    strike: Decimal | None
    seconds_left: int | None

    def __post_init__(self) -> None:
        if self.exchange_ts_ms < 0:
            raise ValueError("exchange_ts_ms must be non-negative")
        _require_non_empty(self.market_id, "market_id")
        _require_optional_non_empty(self.asset, "asset")
        _require_non_empty(self.side_label, "side_label")
        for field_name, value in (
            ("bid", self.bid),
            ("ask", self.ask),
            ("maker_price", self.maker_price),
            ("quote_size_usd", self.quote_size_usd),
            ("reference_price", self.reference_price),
            ("strike", self.strike),
        ):
            _require_optional_positive(value, field_name)
        if self.bid is not None and self.ask is not None and self.bid > self.ask:
            raise ValueError("bid cannot exceed ask")
        if self.model_probability is not None:
            _require_finite(self.model_probability, "model_probability")
            if not Decimal("0") <= self.model_probability <= Decimal("1"):
                raise ValueError("model_probability must be between zero and one")
        if self.seconds_left is not None and self.seconds_left < 0:
            raise ValueError("seconds_left must be non-negative")


@dataclass(frozen=True, slots=True)
class LegacySettlementObservation:
    """Historical outcome settlement with no reconstructed missing strike."""

    context: EventContext
    provenance: LegacyProvenance
    exchange_ts_ms: int
    market_id: str
    asset: str | None
    side_label: str | None
    result_label: str
    payout_usd: Decimal | None
    fee_usd: Decimal | None
    net_pnl_usd: Decimal | None

    def __post_init__(self) -> None:
        if self.exchange_ts_ms < 0:
            raise ValueError("exchange_ts_ms must be non-negative")
        _require_non_empty(self.market_id, "market_id")
        _require_optional_non_empty(self.asset, "asset")
        _require_optional_non_empty(self.side_label, "side_label")
        _require_non_empty(self.result_label, "result_label")
        _require_optional_non_negative(self.payout_usd, "payout_usd")
        _require_optional_non_negative(self.fee_usd, "fee_usd")
        if self.net_pnl_usd is not None:
            _require_finite(self.net_pnl_usd, "net_pnl_usd")


@dataclass(frozen=True, slots=True)
class LegacyFeatureObservation:
    """One symbol extracted from an aggregated TRIDENT feature snapshot."""

    context: EventContext
    provenance: LegacyProvenance
    exchange_ts_ms: int
    asset: str
    source_label: str | None
    price: Decimal | None
    best_bid: Decimal | None
    best_ask: Decimal | None
    bid_size: Decimal | None
    ask_size: Decimal | None
    bid_depth: Decimal | None
    ask_depth: Decimal | None
    oracle_px: Decimal | None
    mark_px: Decimal | None
    spread_bps: Decimal | None

    def __post_init__(self) -> None:
        if self.exchange_ts_ms < 0:
            raise ValueError("exchange_ts_ms must be non-negative")
        _require_non_empty(self.asset, "asset")
        _require_optional_non_empty(self.source_label, "source_label")
        for field_name, value in (
            ("price", self.price),
            ("best_bid", self.best_bid),
            ("best_ask", self.best_ask),
            ("oracle_px", self.oracle_px),
            ("mark_px", self.mark_px),
        ):
            _require_optional_positive(value, field_name)
        for field_name, value in (
            ("bid_size", self.bid_size),
            ("ask_size", self.ask_size),
            ("bid_depth", self.bid_depth),
            ("ask_depth", self.ask_depth),
            ("spread_bps", self.spread_bps),
        ):
            _require_optional_non_negative(value, field_name)
        if (
            self.best_bid is not None
            and self.best_ask is not None
            and self.best_bid > self.best_ask
        ):
            raise ValueError("best_bid cannot exceed best_ask")


DomainEvent: TypeAlias = (
    BookEvent
    | QuoteIntent
    | OrderLifecycle
    | FillAttribution
    | OutcomeSettlement
    | LegacyBookObservation
    | LegacyTradeObservation
    | LegacyQuoteObservation
    | LegacySettlementObservation
    | LegacyFeatureObservation
)


def _encode_json(value: object) -> JsonValue:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, tuple | list):
        return [_encode_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _encode_json(item) for key, item in value.items()}
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError(f"unsupported event payload value: {type(value).__name__}")


def event_payload(event: DomainEvent) -> dict[str, JsonValue]:
    """Return a deterministic JSON-compatible payload for an event."""

    encoded = _encode_json(asdict(event))
    if not isinstance(encoded, dict):
        raise TypeError("domain event must encode to a JSON object")
    return encoded


def event_type(event: DomainEvent) -> str:
    """Return the stable on-disk event type identifier."""

    return type(event).__name__
