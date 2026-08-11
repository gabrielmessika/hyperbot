"""Deterministic adapters from allow-listed legacy schemas to HyperBot events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Protocol, cast

from hyperbot.models import (
    DatasetTier,
    DomainEvent,
    EventContext,
    LegacyBookObservation,
    LegacyFeatureObservation,
    LegacyProvenance,
    LegacyQuoteObservation,
    LegacySettlementObservation,
    LegacyTradeObservation,
    Side,
)


class LegacyAdaptationError(ValueError):
    """A source record cannot be converted without inventing information."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class AdaptationContext:
    event_context: EventContext
    dataset_tier: DatasetTier
    source_path: str
    source_sha256: str
    source_record_number: int
    source_record_sha256: str
    source_record_hash_kind: str


class LegacyAdapter(Protocol):
    name: str
    version: str

    def adapt(
        self, record: dict[str, Any], context: AdaptationContext
    ) -> tuple[DomainEvent, ...]: ...


def _error(code: str, message: str) -> LegacyAdaptationError:
    return LegacyAdaptationError(code, message)


def _string(
    record: dict[str, Any], key: str, *, required: bool = False
) -> str | None:
    value = record.get(key)
    if value is None:
        if required:
            raise _error("missing_field", f"missing {key}")
        return None
    text = str(value).strip()
    if not text:
        if required:
            raise _error("missing_field", f"empty {key}")
        return None
    return text


def _decimal(
    record: dict[str, Any],
    key: str,
    *,
    required: bool = False,
    positive: bool = False,
    non_negative: bool = False,
    zero_is_missing: bool = False,
) -> Decimal | None:
    value = record.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise _error("missing_field", f"missing {key}")
        return None
    try:
        parsed = Decimal(str(value))
    except InvalidOperation as exc:
        raise _error("invalid_decimal", f"invalid decimal {key}={value!r}") from exc
    if not parsed.is_finite():
        raise _error("invalid_decimal", f"non-finite {key}")
    if zero_is_missing and parsed == 0:
        return None
    if positive and parsed <= 0:
        raise _error("impossible_price", f"{key} must be positive")
    if non_negative and parsed < 0:
        raise _error("invalid_size", f"{key} must be non-negative")
    return parsed


def _outcome_price(record: dict[str, Any], key: str) -> Decimal | None:
    value = _decimal(record, key, positive=True)
    if value is not None and value > 1:
        raise _error("impossible_price", f"{key} must not exceed one")
    return value


def _integer(
    record: dict[str, Any], key: str, *, required: bool = False
) -> int | None:
    value = record.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            raise _error("missing_field", f"missing {key}")
        return None
    if isinstance(value, bool):
        raise _error("invalid_integer", f"invalid integer {key}")
    try:
        parsed = int(str(value))
    except ValueError as exc:
        raise _error("invalid_integer", f"invalid integer {key}={value!r}") from exc
    return parsed


def _boolean(record: dict[str, Any], key: str) -> bool | None:
    value = record.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in {"true", "1"}:
        return True
    if lowered in {"false", "0"}:
        return False
    raise _error("invalid_boolean", f"invalid boolean {key}={value!r}")


def _timestamp_ms(record: dict[str, Any], *keys: str) -> int:
    for key in keys:
        value = record.get(key)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if isinstance(value, bool):
            raise _error("invalid_timestamp", f"invalid timestamp {key}")
        if isinstance(value, int | float | Decimal):
            try:
                numeric = Decimal(str(value))
            except InvalidOperation as exc:
                raise _error("invalid_timestamp", f"invalid timestamp {key}") from exc
            if not numeric.is_finite() or numeric < 0:
                raise _error("invalid_timestamp", f"invalid timestamp {key}")
            if numeric >= Decimal("1e17"):
                milliseconds = numeric / Decimal("1e6")
            elif numeric >= Decimal("1e14"):
                milliseconds = numeric / Decimal("1e3")
            elif numeric >= Decimal("1e11"):
                milliseconds = numeric
            else:
                milliseconds = numeric * Decimal("1e3")
            return int(milliseconds)
        text = str(value).strip()
        try:
            numeric = Decimal(text)
        except InvalidOperation:
            iso_value = text[:-1] + "+00:00" if text.endswith("Z") else text
            try:
                parsed = datetime.fromisoformat(iso_value)
            except ValueError as exc:
                raise _error(
                    "invalid_timestamp", f"invalid timestamp {key}={value!r}"
                ) from exc
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return int(parsed.timestamp() * 1_000)
        else:
            return _timestamp_ms({key: numeric}, key)
    raise _error("missing_timestamp", f"none of timestamp fields {keys} is present")


def _provenance(
    context: AdaptationContext,
    *,
    adapter_name: str,
    adapter_version: str,
    subrecord_index: int,
    quality_flags: tuple[str, ...],
) -> LegacyProvenance:
    flags = tuple(dict.fromkeys(("legacy_research_only", *quality_flags)))
    return LegacyProvenance(
        dataset_tier=context.dataset_tier,
        source_path=context.source_path,
        source_sha256=context.source_sha256,
        source_record_number=context.source_record_number,
        source_record_sha256=context.source_record_sha256,
        source_record_hash_kind=context.source_record_hash_kind,
        subrecord_index=subrecord_index,
        adapter_name=adapter_name,
        adapter_version=adapter_version,
        quality_flags=flags,
        legacy_research_only=True,
    )


def _side_from_label(label: str | None) -> Side | None:
    if label is None:
        return None
    normalized = label.strip().upper()
    if normalized.startswith("BUY"):
        return Side.BUY
    if normalized.startswith("SELL"):
        return Side.SELL
    return None


class LegacyGbotAdapter:
    name = "LegacyGbotAdapter"
    version = "1.0.1"

    def adapt(
        self, record: dict[str, Any], context: AdaptationContext
    ) -> tuple[DomainEvent, ...]:
        parts = Path(context.source_path).parts
        if "l2" in parts:
            return (self._book(record, context),)
        if "trades" in parts:
            return (self._trade(record, context),)
        raise _error("unsupported_schema", "GBOT path is neither l2 nor trades")

    def _book(
        self, record: dict[str, Any], context: AdaptationContext
    ) -> LegacyBookObservation:
        best_bid = _decimal(record, "best_bid", required=True, positive=True)
        best_ask = _decimal(record, "best_ask", required=True, positive=True)
        if best_bid is not None and best_ask is not None and best_bid > best_ask:
            raise _error("crossed_book", "best_bid cannot exceed best_ask")
        return LegacyBookObservation(
            context=context.event_context,
            provenance=_provenance(
                context,
                adapter_name=self.name,
                adapter_version=self.version,
                subrecord_index=0,
                quality_flags=("aggregated_depth_only", "queue_position_unknown"),
            ),
            exchange_ts_ms=_timestamp_ms(record, "timestamp"),
            dex="hyperliquid",
            asset=_string(record, "coin", required=True) or "",
            market_id=None,
            outcome_side=None,
            best_bid=best_bid,
            best_ask=best_ask,
            bid_size=None,
            ask_size=None,
            bid_depth=_decimal(record, "bid_depth_10bps", non_negative=True),
            ask_depth=_decimal(record, "ask_depth_10bps", non_negative=True),
        )

    def _trade(
        self, record: dict[str, Any], context: AdaptationContext
    ) -> LegacyTradeObservation:
        is_buy = _boolean(record, "is_buy")
        if is_buy is None:
            raise _error("missing_field", "missing is_buy")
        return LegacyTradeObservation(
            context=context.event_context,
            provenance=_provenance(
                context,
                adapter_name=self.name,
                adapter_version=self.version,
                subrecord_index=0,
                quality_flags=("trade_stream_without_sequence",),
            ),
            exchange_ts_ms=_timestamp_ms(record, "timestamp"),
            dex="hyperliquid",
            asset=_string(record, "coin", required=True) or "",
            market_id=None,
            side=Side.BUY if is_buy else Side.SELL,
            price=_decimal(record, "price", required=True, positive=True) or Decimal(0),
            base_size=_decimal(record, "size", required=True, positive=True),
            token_size=None,
            notional_usd=None,
        )


class LegacyTridentSnapshotAdapter:
    name = "LegacyTridentSnapshotAdapter"
    version = "1.0.0"

    def adapt(
        self, record: dict[str, Any], context: AdaptationContext
    ) -> tuple[DomainEvent, ...]:
        timestamp_ms = _timestamp_ms(record, "timestamp")
        symbols = record.get("symbols")
        if not isinstance(symbols, list):
            raise _error("invalid_schema", "symbols must be a list")
        events: list[DomainEvent] = []
        for index, value in enumerate(symbols):
            if not isinstance(value, dict):
                raise _error("invalid_schema", f"symbols[{index}] must be an object")
            symbol = value
            zero_sentinel = any(
                str(symbol.get(key, "")).strip() in {"0", "0.0"}
                for key in ("price", "best_bid", "best_ask", "oracle_px", "mark_px")
            )
            flags = ["aggregated_feature_snapshot", "queue_position_unknown"]
            if zero_sentinel:
                flags.append("zero_sentinel_mapped_to_missing")
            events.append(
                LegacyFeatureObservation(
                    context=context.event_context,
                    provenance=_provenance(
                        context,
                        adapter_name=self.name,
                        adapter_version=self.version,
                        subrecord_index=index,
                        quality_flags=tuple(flags),
                    ),
                    exchange_ts_ms=timestamp_ms,
                    asset=_string(symbol, "symbol", required=True) or "",
                    source_label=_string(symbol, "source"),
                    price=_decimal(
                        symbol, "price", positive=True, zero_is_missing=True
                    ),
                    best_bid=_decimal(
                        symbol, "best_bid", positive=True, zero_is_missing=True
                    ),
                    best_ask=_decimal(
                        symbol, "best_ask", positive=True, zero_is_missing=True
                    ),
                    bid_size=_decimal(symbol, "best_bid_size", non_negative=True),
                    ask_size=_decimal(symbol, "best_ask_size", non_negative=True),
                    bid_depth=_decimal(symbol, "bid_depth_10bps", non_negative=True),
                    ask_depth=_decimal(symbol, "ask_depth_10bps", non_negative=True),
                    oracle_px=_decimal(
                        symbol, "oracle_px", positive=True, zero_is_missing=True
                    ),
                    mark_px=_decimal(
                        symbol, "mark_px", positive=True, zero_is_missing=True
                    ),
                    spread_bps=_decimal(symbol, "spread_bps", non_negative=True),
                )
            )
        return tuple(events)


class LegacyHip4Adapter:
    name = "LegacyHip4Adapter"
    version = "1.0.1"

    def adapt(
        self, record: dict[str, Any], context: AdaptationContext
    ) -> tuple[DomainEvent, ...]:
        filename = Path(context.source_path).name
        if filename == "book_snapshots.jsonl":
            return (self._nautilus_book(record, context),)
        if filename == "market_observations.jsonl":
            return self._market_observation(record, context)
        if filename == "shadow_maker_quotes.csv":
            return (self._quote(record, context),)
        if filename == "trades.csv":
            return (self._trade(record, context),)
        if filename == "settlements.csv":
            return (self._settlement(record, context),)
        raise _error("unsupported_schema", f"unsupported HIP-4 file {filename}")

    def _nautilus_book(
        self, record: dict[str, Any], context: AdaptationContext
    ) -> LegacyBookObservation:
        return LegacyBookObservation(
            context=context.event_context,
            provenance=_provenance(
                context,
                adapter_name=self.name,
                adapter_version=self.version,
                subrecord_index=0,
                quality_flags=("aggregated_depth_only", "queue_position_unknown"),
            ),
            exchange_ts_ms=_timestamp_ms(record, "ts_event"),
            dex="hyperliquid_outcomes",
            asset=_string(record, "coin", required=True) or "",
            market_id=_string(record, "market_id"),
            outcome_side=_string(record, "side_name"),
            best_bid=_outcome_price(record, "best_bid"),
            best_ask=_outcome_price(record, "best_ask"),
            bid_size=_decimal(record, "bid_size", non_negative=True),
            ask_size=_decimal(record, "ask_size", non_negative=True),
            bid_depth=_decimal(record, "bid_depth_10", non_negative=True),
            ask_depth=_decimal(record, "ask_depth_10", non_negative=True),
        )

    def _market_observation(
        self, record: dict[str, Any], context: AdaptationContext
    ) -> tuple[DomainEvent, ...]:
        timestamp_ms = _timestamp_ms(record, "ts")
        books = record.get("books")
        if not isinstance(books, dict):
            raise _error("invalid_schema", "books must be an object")
        raw_coins = record.get("coins")
        coins = raw_coins if isinstance(raw_coins, list) else []
        raw_side_names = record.get("side_names")
        side_names = raw_side_names if isinstance(raw_side_names, list) else []
        events: list[DomainEvent] = []
        for index, side in enumerate(("yes", "no")):
            value = books.get(side)
            if value is None:
                book: dict[str, Any] = {}
            elif isinstance(value, dict):
                book = cast(dict[str, Any], value)
            else:
                raise _error("invalid_schema", f"books.{side} must be an object")
            fallback_coin = coins[index] if index < len(coins) else None
            coin = _string(book, "coin") or (
                str(fallback_coin).strip() if fallback_coin is not None else ""
            )
            if not coin:
                raise _error("missing_field", f"missing coin for books.{side}")
            fallback_side = side.upper()
            if index < len(side_names) and str(side_names[index]).strip():
                fallback_side = str(side_names[index]).strip()
            flags = ["aggregated_depth_only", "queue_position_unknown"]
            if book.get("bid") is None and book.get("ask") is None:
                flags.append("empty_book")
            if not book:
                flags.append("book_payload_absent")
            support_status = _string(record, "support_status")
            if support_status and support_status != "supported":
                flags.append(support_status)
            events.append(
                LegacyBookObservation(
                    context=context.event_context,
                    provenance=_provenance(
                        context,
                        adapter_name=self.name,
                        adapter_version=self.version,
                        subrecord_index=index,
                        quality_flags=tuple(flags),
                    ),
                    exchange_ts_ms=timestamp_ms,
                    dex="hyperliquid_outcomes",
                    asset=coin,
                    market_id=_string(record, "market_id"),
                    outcome_side=fallback_side,
                    best_bid=_outcome_price(book, "bid"),
                    best_ask=_outcome_price(book, "ask"),
                    bid_size=_decimal(book, "bid_size", non_negative=True),
                    ask_size=_decimal(book, "ask_size", non_negative=True),
                    bid_depth=_decimal(book, "bid_depth_usdc", non_negative=True),
                    ask_depth=_decimal(book, "ask_depth_usdc", non_negative=True),
                )
            )
        return tuple(events)

    def _quote(
        self, record: dict[str, Any], context: AdaptationContext
    ) -> LegacyQuoteObservation:
        seconds_left = _integer(record, "seconds_left")
        if seconds_left is not None and seconds_left < 0:
            raise _error("invalid_timestamp", "seconds_left must be non-negative")
        probability = _decimal(record, "win_probability", non_negative=True)
        return LegacyQuoteObservation(
            context=context.event_context,
            provenance=_provenance(
                context,
                adapter_name=self.name,
                adapter_version=self.version,
                subrecord_index=0,
                quality_flags=("paper_quote", "not_exchange_acknowledged"),
            ),
            exchange_ts_ms=_timestamp_ms(record, "ts"),
            market_id=_string(record, "market_id", required=True) or "",
            asset=_string(record, "underlying"),
            side_label=_string(record, "side", required=True) or "",
            decision_approved=_boolean(record, "decision_approved"),
            would_quote=_boolean(record, "would_quote"),
            bid=_outcome_price(record, "bid"),
            ask=_outcome_price(record, "ask"),
            maker_price=_outcome_price(record, "maker_price"),
            quote_size_usd=_decimal(record, "quote_size_usdc", positive=True),
            model_probability=probability,
            reference_price=_decimal(record, "reference_price", positive=True),
            strike=_decimal(record, "strike", positive=True),
            seconds_left=seconds_left,
        )

    def _trade(
        self, record: dict[str, Any], context: AdaptationContext
    ) -> LegacyTradeObservation:
        label = _string(record, "side")
        return LegacyTradeObservation(
            context=context.event_context,
            provenance=_provenance(
                context,
                adapter_name=self.name,
                adapter_version=self.version,
                subrecord_index=0,
                quality_flags=("paper_trade", "fill_not_exchange_verified"),
            ),
            exchange_ts_ms=_timestamp_ms(record, "ts"),
            dex="hyperliquid_outcomes",
            asset=(
                _string(record, "coin")
                or _string(record, "underlying", required=True)
                or ""
            ),
            market_id=_string(record, "market_id"),
            side=_side_from_label(label),
            price=_decimal(record, "price", required=True, positive=True) or Decimal(0),
            base_size=None,
            token_size=_decimal(record, "token_qty", positive=True),
            notional_usd=_decimal(record, "size_usdc", positive=True),
        )

    def _settlement(
        self, record: dict[str, Any], context: AdaptationContext
    ) -> LegacySettlementObservation:
        return LegacySettlementObservation(
            context=context.event_context,
            provenance=_provenance(
                context,
                adapter_name=self.name,
                adapter_version=self.version,
                subrecord_index=0,
                quality_flags=("paper_settlement",),
            ),
            exchange_ts_ms=_timestamp_ms(record, "ts"),
            market_id=_string(record, "market_id", required=True) or "",
            asset=_string(record, "underlying"),
            side_label=_string(record, "side"),
            result_label=_string(record, "result", required=True) or "",
            payout_usd=_decimal(record, "payout_usdc", non_negative=True),
            fee_usd=_decimal(record, "fee_usdc", non_negative=True),
            net_pnl_usd=(
                _decimal(record, "net_pnl_usdc")
                if _string(record, "net_pnl_usdc") is not None
                else _decimal(record, "pnl_usdc")
            ),
        )


ADAPTERS_BY_SOURCE: dict[str, LegacyAdapter] = {
    "gbot_microstructure": LegacyGbotAdapter(),
    "hip4_nautilus_books": LegacyHip4Adapter(),
    "hip4_paper": LegacyHip4Adapter(),
    "trident_live_snapshots": LegacyTridentSnapshotAdapter(),
    "trident_replay_sample": LegacyTridentSnapshotAdapter(),
}


def adapter_for_source(source_name: str) -> LegacyAdapter:
    """Return the explicit adapter for an inventory source."""

    try:
        return ADAPTERS_BY_SOURCE[source_name]
    except KeyError as exc:
        raise LegacyAdaptationError(
            "unsupported_source", f"no adapter registered for {source_name}"
        ) from exc
