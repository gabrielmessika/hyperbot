"""Runtime-cost-aware HIP-3 growth market scanner."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from hyperbot.models import MarketDefinition, MarketKind, MarketStatus


@dataclass(frozen=True, slots=True)
class GrowthScannerConfig:
    order_size_usd: Decimal = Decimal("10")
    minimum_median_spread_bps: Decimal = Decimal("8")
    minimum_daily_volume_usd: Decimal = Decimal("250000")
    minimum_depth_multiple: Decimal = Decimal("50")
    maximum_oracle_age_ms: int = 500
    maximum_stale_fraction: Decimal = Decimal("0.01")
    require_growth_mode: bool = True

    def __post_init__(self) -> None:
        for name, value in (
            ("order_size_usd", self.order_size_usd),
            ("minimum_median_spread_bps", self.minimum_median_spread_bps),
            ("minimum_daily_volume_usd", self.minimum_daily_volume_usd),
            ("minimum_depth_multiple", self.minimum_depth_multiple),
        ):
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.maximum_oracle_age_ms <= 0:
            raise ValueError("maximum_oracle_age_ms must be positive")
        if not Decimal(0) <= self.maximum_stale_fraction <= Decimal(1):
            raise ValueError("maximum_stale_fraction must be between zero and one")


@dataclass(frozen=True, slots=True)
class GrowthMarketObservation:
    market_id: str
    observed_at_ms: int
    median_spread_1h_bps: Decimal
    median_spread_24h_bps: Decimal
    spread_p10_1h_bps: Decimal
    spread_p90_1h_bps: Decimal
    depth_5bps_usd: Decimal
    depth_10bps_usd: Decimal
    depth_25bps_usd: Decimal
    daily_volume_usd: Decimal
    aggressive_trade_count_1h: int
    oracle_age_ms: int
    oracle_mark_gap_bps: Decimal
    stale_fraction_1h: Decimal
    deployer_incident: bool

    def __post_init__(self) -> None:
        if not self.market_id.strip() or self.observed_at_ms < 0:
            raise ValueError("market_id and observed_at_ms are invalid")
        for name, value in (
            ("median_spread_1h_bps", self.median_spread_1h_bps),
            ("median_spread_24h_bps", self.median_spread_24h_bps),
            ("spread_p10_1h_bps", self.spread_p10_1h_bps),
            ("spread_p90_1h_bps", self.spread_p90_1h_bps),
            ("depth_5bps_usd", self.depth_5bps_usd),
            ("depth_10bps_usd", self.depth_10bps_usd),
            ("depth_25bps_usd", self.depth_25bps_usd),
            ("daily_volume_usd", self.daily_volume_usd),
            ("oracle_mark_gap_bps", self.oracle_mark_gap_bps),
            ("stale_fraction_1h", self.stale_fraction_1h),
        ):
            if not value.is_finite() or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.aggressive_trade_count_1h < 0 or self.oracle_age_ms < 0:
            raise ValueError("counts and ages must be non-negative")
        if self.stale_fraction_1h > 1:
            raise ValueError("stale_fraction_1h must not exceed one")


@dataclass(frozen=True, slots=True)
class GrowthScanDecision:
    market_id: str
    observed_at_ms: int
    definition_sha256: str | None
    fee_basis: str | None
    maker_fee_bps: Decimal | None
    taker_fee_bps: Decimal | None
    net_spread_after_round_trip_maker_fees_bps: Decimal | None
    qualified: bool
    rejection_reasons: tuple[str, ...]


class GrowthMarketScanner:
    def __init__(self, config: GrowthScannerConfig | None = None) -> None:
        self.config = config or GrowthScannerConfig()

    def scan(
        self,
        *,
        definitions: tuple[MarketDefinition, ...],
        observations: tuple[GrowthMarketObservation, ...],
    ) -> tuple[GrowthScanDecision, ...]:
        definitions_by_id = {
            definition.market_id: definition for definition in definitions
        }
        decisions: list[GrowthScanDecision] = []
        for observation in sorted(observations, key=lambda item: item.market_id):
            definition = definitions_by_id.get(observation.market_id)
            reasons: list[str] = []
            if definition is None:
                reasons.append("unknown_market")
            else:
                if definition.market_kind is not MarketKind.HIP3_PERP:
                    reasons.append("not_hip3_perp")
                if definition.status is not MarketStatus.ACTIVE:
                    reasons.append("market_not_active")
                if (
                    self.config.require_growth_mode
                    and definition.growth_mode != "enabled"
                ):
                    reasons.append("growth_mode_required")
                if definition.maker_fee_bps is None or definition.taker_fee_bps is None:
                    reasons.append("runtime_fees_unknown")
            if observation.median_spread_1h_bps < self.config.minimum_median_spread_bps:
                reasons.append("median_spread_below_threshold")
            if observation.daily_volume_usd < self.config.minimum_daily_volume_usd:
                reasons.append("daily_volume_below_threshold")
            required_depth = (
                self.config.order_size_usd * self.config.minimum_depth_multiple
            )
            if observation.depth_25bps_usd < required_depth:
                reasons.append("depth_below_threshold")
            if observation.oracle_age_ms > self.config.maximum_oracle_age_ms:
                reasons.append("oracle_stale")
            if observation.deployer_incident:
                reasons.append("deployer_incident")
            if observation.stale_fraction_1h > self.config.maximum_stale_fraction:
                reasons.append("stale_book_fraction")
            maker_fee = definition.maker_fee_bps if definition else None
            net_spread = (
                observation.median_spread_1h_bps - maker_fee * 2
                if maker_fee is not None
                else None
            )
            if net_spread is not None and net_spread <= 0:
                reasons.append("spread_does_not_cover_round_trip_maker_fees")
            decisions.append(
                GrowthScanDecision(
                    market_id=observation.market_id,
                    observed_at_ms=observation.observed_at_ms,
                    definition_sha256=(
                        definition.definition_sha256 if definition else None
                    ),
                    fee_basis=definition.fee_basis if definition else None,
                    maker_fee_bps=maker_fee,
                    taker_fee_bps=(definition.taker_fee_bps if definition else None),
                    net_spread_after_round_trip_maker_fees_bps=net_spread,
                    qualified=not reasons,
                    rejection_reasons=tuple(reasons),
                )
            )
        return tuple(
            sorted(
                decisions,
                key=lambda item: (
                    not item.qualified,
                    -(
                        item.net_spread_after_round_trip_maker_fees_bps
                        or Decimal("-Infinity")
                    ),
                    item.market_id,
                ),
            )
        )
