"""Oracle-bounded HIP-3 growth maker that emits intents only."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from statistics import median

from hyperbot.models import QuoteIntent, Side
from hyperbot.strategies.common import (
    MarketState,
    intent_id,
    round_to_tick,
    size_for_minimum_notional,
)


@dataclass(frozen=True, slots=True)
class GrowthStrategyConfig:
    order_notional_usd: Decimal = Decimal("10")
    minimum_half_spread_bps: Decimal = Decimal("4")
    adverse_selection_bps: Decimal = Decimal("2")
    inventory_skew_bps_per_unit: Decimal = Decimal("0.5")
    maximum_oracle_deviation_bps: Decimal = Decimal("10")
    stale_after_ms: int = 500
    ttl_ms: int = 1_000

    def __post_init__(self) -> None:
        decimals = (
            self.order_notional_usd,
            self.minimum_half_spread_bps,
            self.adverse_selection_bps,
            self.inventory_skew_bps_per_unit,
            self.maximum_oracle_deviation_bps,
        )
        if any(not value.is_finite() or value < 0 for value in decimals):
            raise ValueError("growth strategy values must be finite and non-negative")
        if self.order_notional_usd <= 0:
            raise ValueError("order_notional_usd must be positive")
        if self.stale_after_ms <= 0 or self.ttl_ms <= 0:
            raise ValueError("growth strategy timing values must be positive")


class GrowthMakerStrategy:
    name = "growth_maker_v1"

    def __init__(self, config: GrowthStrategyConfig | None = None) -> None:
        self.config = config or GrowthStrategyConfig()

    def on_market_state(self, state: MarketState) -> tuple[QuoteIntent, ...]:
        if not state.data_healthy or state.age_ms > self.config.stale_after_ms:
            return ()
        if state.best_bid >= state.best_ask:
            return ()
        robust = Decimal(
            str(median((state.oracle_px, state.mark_px, state.microprice)))
        )
        bound = (
            state.oracle_px
            * self.config.maximum_oracle_deviation_bps
            / Decimal(10_000)
        )
        fair = min(max(robust, state.oracle_px - bound), state.oracle_px + bound)
        half_spread_bps = max(
            self.config.minimum_half_spread_bps,
            state.maker_fee_bps * 2 + self.config.adverse_selection_bps,
        )
        half_spread = fair * half_spread_bps / Decimal(10_000)
        skew = (
            fair
            * self.config.inventory_skew_bps_per_unit
            * state.inventory_units
            / Decimal(10_000)
        )
        bid = round_to_tick(
            min(fair - half_spread - skew, state.best_bid),
            state.tick_size,
            Side.BUY,
        )
        ask = round_to_tick(
            max(fair + half_spread - skew, state.best_ask),
            state.tick_size,
            Side.SELL,
        )
        if bid <= 0 or bid >= ask:
            return ()
        intents: list[QuoteIntent] = []
        for side, price in ((Side.BUY, bid), (Side.SELL, ask)):
            size = size_for_minimum_notional(
                self.config.order_notional_usd,
                price,
                state.size_increment,
            )
            if size <= 0:
                continue
            intents.append(
                QuoteIntent(
                    context=state.context,
                    intent_id=intent_id(
                        strategy=self.name,
                        state=state,
                        side=side,
                        price=price,
                    ),
                    strategy=self.name,
                    market=state.market,
                    side=side,
                    price=price,
                    size=size,
                    ttl_ms=self.config.ttl_ms,
                    fair_value=fair,
                    min_edge_bps=half_spread_bps,
                    inventory_before=state.inventory_units,
                    reason_codes=(
                        "oracle_bounded_fair",
                        "runtime_fees_included",
                        "inventory_skewed",
                    ),
                )
            )
        return tuple(intents)
