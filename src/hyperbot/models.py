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


DomainEvent: TypeAlias = (
    BookEvent | QuoteIntent | OrderLifecycle | FillAttribution | OutcomeSettlement
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
