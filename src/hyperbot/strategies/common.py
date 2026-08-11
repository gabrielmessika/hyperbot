"""Shared immutable state and strategy contract."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Protocol

from hyperbot.models import EventContext, QuoteIntent, Side


@dataclass(frozen=True, slots=True)
class MarketState:
    context: EventContext
    market: str
    observed_ts_ms: int
    now_ts_ms: int
    best_bid: Decimal
    best_ask: Decimal
    bid_size: Decimal
    ask_size: Decimal
    oracle_px: Decimal
    mark_px: Decimal
    microprice: Decimal
    tick_size: Decimal
    size_increment: Decimal
    maker_fee_bps: Decimal
    inventory_units: Decimal
    data_healthy: bool
    expiry_ts_ms: int | None = None

    def __post_init__(self) -> None:
        if not self.market.strip():
            raise ValueError("market must not be empty")
        if self.observed_ts_ms < 0 or self.now_ts_ms < self.observed_ts_ms:
            raise ValueError("market-state timestamps are invalid")
        for name, value in (
            ("best_bid", self.best_bid),
            ("best_ask", self.best_ask),
            ("bid_size", self.bid_size),
            ("ask_size", self.ask_size),
            ("oracle_px", self.oracle_px),
            ("mark_px", self.mark_px),
            ("microprice", self.microprice),
            ("tick_size", self.tick_size),
            ("size_increment", self.size_increment),
        ):
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.best_bid > self.best_ask:
            raise ValueError("market state must not be crossed")
        if not self.maker_fee_bps.is_finite() or self.maker_fee_bps < 0:
            raise ValueError("maker_fee_bps must be finite and non-negative")
        if not self.inventory_units.is_finite():
            raise ValueError("inventory_units must be finite")
        if self.expiry_ts_ms is not None and self.expiry_ts_ms <= self.observed_ts_ms:
            raise ValueError("expiry must be after the observation")

    @property
    def age_ms(self) -> int:
        return self.now_ts_ms - self.observed_ts_ms


class Strategy(Protocol):
    def on_market_state(self, state: MarketState) -> tuple[QuoteIntent, ...]: ...


def round_to_tick(value: Decimal, tick: Decimal, side: Side) -> Decimal:
    rounding = ROUND_FLOOR if side is Side.BUY else ROUND_CEILING
    return (value / tick).to_integral_value(rounding=rounding) * tick


def round_size(value: Decimal, increment: Decimal) -> Decimal:
    return (value / increment).to_integral_value(rounding=ROUND_FLOOR) * increment


def size_for_minimum_notional(
    notional: Decimal,
    price: Decimal,
    increment: Decimal,
) -> Decimal:
    raw = notional / price
    return (raw / increment).to_integral_value(rounding=ROUND_CEILING) * increment


def intent_id(
    *,
    strategy: str,
    state: MarketState,
    side: Side,
    price: Decimal,
) -> str:
    payload = "|".join(
        (
            strategy,
            state.context.run_id,
            state.market,
            str(state.observed_ts_ms),
            side.value,
            str(price),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
