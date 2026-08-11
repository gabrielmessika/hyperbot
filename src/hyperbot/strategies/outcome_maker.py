"""Two-sided outcome maker that emits intents only."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from hyperbot.models import QuoteIntent, Side
from hyperbot.strategies.common import (
    MarketState,
    intent_id,
    round_to_tick,
    size_for_minimum_notional,
)


@dataclass(frozen=True, slots=True)
class OutcomeStrategyConfig:
    order_notional_usd: Decimal = Decimal("10")
    adverse_selection_bps: Decimal = Decimal("20")
    inventory_cost_bps: Decimal = Decimal("10")
    model_uncertainty_bps: Decimal = Decimal("20")
    operational_margin_bps: Decimal = Decimal("10")
    inventory_skew_bps_per_unit: Decimal = Decimal("2")
    stale_after_ms: int = 500
    no_new_inventory_before_expiry_ms: int = 60_000
    ttl_ms: int = 1_000

    def __post_init__(self) -> None:
        decimals = (
            self.order_notional_usd,
            self.adverse_selection_bps,
            self.inventory_cost_bps,
            self.model_uncertainty_bps,
            self.operational_margin_bps,
            self.inventory_skew_bps_per_unit,
        )
        if any(not value.is_finite() or value < 0 for value in decimals):
            raise ValueError("outcome strategy values must be finite and non-negative")
        if self.order_notional_usd <= 0:
            raise ValueError("order_notional_usd must be positive")
        if (
            self.stale_after_ms <= 0
            or self.no_new_inventory_before_expiry_ms < 0
            or self.ttl_ms <= 0
        ):
            raise ValueError("outcome strategy timing values are invalid")


class OutcomeMakerStrategy:
    name = "outcome_maker_v1"

    def __init__(self, config: OutcomeStrategyConfig | None = None) -> None:
        self.config = config or OutcomeStrategyConfig()

    def on_market_state(
        self,
        state: MarketState,
        *,
        fair_probability: Decimal | None = None,
    ) -> tuple[QuoteIntent, ...]:
        if not state.data_healthy or state.age_ms > self.config.stale_after_ms:
            return ()
        if state.best_bid >= state.best_ask:
            return ()
        if state.expiry_ts_ms is not None and (
            state.expiry_ts_ms - state.now_ts_ms
            <= self.config.no_new_inventory_before_expiry_ms
        ):
            return ()
        fair = fair_probability if fair_probability is not None else state.mark_px
        if not fair.is_finite() or not Decimal(0) < fair < Decimal(1):
            return ()
        half_spread_bps = (
            state.maker_fee_bps * 2
            + self.config.adverse_selection_bps
            + self.config.inventory_cost_bps
            + self.config.model_uncertainty_bps
            + self.config.operational_margin_bps
        )
        half_spread = fair * half_spread_bps / Decimal(10_000)
        skew = (
            fair
            * self.config.inventory_skew_bps_per_unit
            * state.inventory_units
            / Decimal(10_000)
        )
        raw_bid = min(fair - half_spread - skew, state.best_bid)
        raw_ask = max(fair + half_spread - skew, state.best_ask)
        bid = round_to_tick(raw_bid, state.tick_size, Side.BUY)
        ask = round_to_tick(raw_ask, state.tick_size, Side.SELL)
        if bid <= 0 or ask >= 1 or bid >= ask:
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
                        "two_sided_outcome",
                        "fees_included",
                        "inventory_skewed",
                    ),
                )
            )
        return tuple(intents)
