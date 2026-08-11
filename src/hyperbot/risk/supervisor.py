"""Single fail-closed authority for quote-intent approval."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from decimal import ROUND_FLOOR, Decimal
from enum import StrEnum

from hyperbot.models import QuoteIntent, Side


class StrategyKind(StrEnum):
    OUTCOME = "outcome"
    GROWTH = "growth"


class OutcomeTokenSide(StrEnum):
    YES = "yes"
    NO = "no"


class RiskAction(StrEnum):
    NONE = "none"
    CANCEL_MARKET = "cancel_market"
    CANCEL_ALL = "cancel_all"
    HARD_STOP = "hard_stop"


@dataclass(frozen=True, slots=True)
class RiskLimits:
    reference_equity_usd: Decimal = Decimal("1000")
    daily_loss_stop_pct: Decimal = Decimal("1.5")
    soft_drawdown_pct: Decimal = Decimal("8")
    hard_drawdown_pct: Decimal = Decimal("12")
    unknown_loss_stop_usd: Decimal = Decimal("5")
    stale_book_ms: int = 500
    outcome_order_notional_usd: Decimal = Decimal("10")
    max_outcome_inventory_per_market_usd: Decimal = Decimal("50")
    max_outcome_settlement_loss_per_market_usd: Decimal = Decimal("25")
    max_correlated_settlement_loss_usd: Decimal = Decimal("30")
    max_total_settlement_loss_usd: Decimal = Decimal("50")
    max_outcome_markets: int = 4
    growth_min_order_usd: Decimal = Decimal("10")
    growth_max_order_usd: Decimal = Decimal("20")
    max_growth_inventory_per_symbol_usd: Decimal = Decimal("50")
    max_growth_gross_inventory_usd: Decimal = Decimal("150")
    max_growth_net_delta_usd: Decimal = Decimal("75")

    def __post_init__(self) -> None:
        decimal_values = (
            self.reference_equity_usd,
            self.daily_loss_stop_pct,
            self.soft_drawdown_pct,
            self.hard_drawdown_pct,
            self.unknown_loss_stop_usd,
            self.outcome_order_notional_usd,
            self.max_outcome_inventory_per_market_usd,
            self.max_outcome_settlement_loss_per_market_usd,
            self.max_correlated_settlement_loss_usd,
            self.max_total_settlement_loss_usd,
            self.growth_min_order_usd,
            self.growth_max_order_usd,
            self.max_growth_inventory_per_symbol_usd,
            self.max_growth_gross_inventory_usd,
            self.max_growth_net_delta_usd,
        )
        if any(not value.is_finite() or value <= 0 for value in decimal_values):
            raise ValueError("risk limits must be finite and positive")
        if not (
            self.daily_loss_stop_pct
            < self.soft_drawdown_pct
            < self.hard_drawdown_pct
            < 100
        ):
            raise ValueError("drawdown limits are inconsistent")
        if self.stale_book_ms <= 0 or self.max_outcome_markets <= 0:
            raise ValueError("risk counters must be positive")


@dataclass(frozen=True, slots=True)
class OutcomeExposure:
    outcome_market_id: str
    correlation_group: str
    pnl_if_yes_usd: Decimal
    pnl_if_no_usd: Decimal
    gross_inventory_usd: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        if not self.outcome_market_id.strip() or not self.correlation_group.strip():
            raise ValueError("outcome exposure identifiers must not be empty")
        if not self.pnl_if_yes_usd.is_finite() or not self.pnl_if_no_usd.is_finite():
            raise ValueError("outcome exposure PnL must be finite")
        if not self.gross_inventory_usd.is_finite() or self.gross_inventory_usd < 0:
            raise ValueError("gross_inventory_usd must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class PortfolioState:
    observed_at_ms: int
    equity_usd: Decimal
    peak_equity_usd: Decimal
    daily_pnl_usd: Decimal
    unknown_loss_usd: Decimal
    heartbeat_healthy: bool
    orphan_order_count: int
    reconciliation_attempted: bool
    local_positions: tuple[tuple[str, Decimal], ...]
    exchange_positions: tuple[tuple[str, Decimal], ...]
    outcome_exposures: tuple[OutcomeExposure, ...]
    growth_inventory_usd: tuple[tuple[str, Decimal], ...]
    growth_active_dexes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.observed_at_ms < 0 or self.orphan_order_count < 0:
            raise ValueError("portfolio timestamp and counters must be non-negative")
        for name, value in (
            ("equity_usd", self.equity_usd),
            ("peak_equity_usd", self.peak_equity_usd),
            ("daily_pnl_usd", self.daily_pnl_usd),
            ("unknown_loss_usd", self.unknown_loss_usd),
        ):
            if not value.is_finite():
                raise ValueError(f"{name} must be finite")
        if self.equity_usd <= 0 or self.peak_equity_usd <= 0:
            raise ValueError("equity values must be positive")


@dataclass(frozen=True, slots=True)
class IntentMetadata:
    strategy_kind: StrategyKind
    dex: str
    data_age_ms: int
    order_type: str
    intent_definition_sha256: str
    current_definition_sha256: str
    tick_size: Decimal
    outcome_market_id: str | None = None
    outcome_token_side: OutcomeTokenSide | None = None
    correlation_group: str | None = None

    def __post_init__(self) -> None:
        if not self.dex.strip() or self.data_age_ms < 0:
            raise ValueError("intent metadata DEX and age are invalid")
        if self.order_type != "ALO":
            raise ValueError("normal quote intents must be ALO")
        for name, value in (
            ("intent_definition_sha256", self.intent_definition_sha256),
            ("current_definition_sha256", self.current_definition_sha256),
        ):
            if len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value
            ):
                raise ValueError(f"{name} must be a lowercase SHA-256")
        if not self.tick_size.is_finite() or self.tick_size <= 0:
            raise ValueError("tick_size must be finite and positive")
        outcome_fields = (
            self.outcome_market_id,
            self.outcome_token_side,
            self.correlation_group,
        )
        if self.strategy_kind is StrategyKind.OUTCOME and any(
            value is None for value in outcome_fields
        ):
            raise ValueError("outcome metadata is incomplete")


@dataclass(frozen=True, slots=True)
class ApprovedIntent:
    intent: QuoteIntent
    approved_size: Decimal
    decision_id: str
    reduced: bool
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class RiskDecision:
    approved: ApprovedIntent | None
    action: RiskAction
    rejection_reasons: tuple[str, ...]

    @property
    def is_approved(self) -> bool:
        return self.approved is not None


@dataclass(frozen=True, slots=True)
class OperatorResetAuthorization:
    operator_id: str
    reason: str
    confirmed: bool

    def __post_init__(self) -> None:
        if not self.operator_id.strip() or not self.reason.strip():
            raise ValueError("operator reset identity and reason are required")


def _position_map(values: tuple[tuple[str, Decimal], ...]) -> dict[str, Decimal]:
    return dict(values)


def _outcome_quote_impact(
    intent: QuoteIntent,
    token_side: OutcomeTokenSide,
) -> tuple[Decimal, Decimal]:
    units = intent.size
    price = intent.price
    if token_side is OutcomeTokenSide.YES:
        yes, no = (Decimal(1) - price) * units, -price * units
    else:
        yes, no = -price * units, (Decimal(1) - price) * units
    if intent.side is Side.SELL:
        return -yes, -no
    return yes, no


class RiskSupervisor:
    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()
        self._hard_stop_latched = False

    @property
    def hard_stop_latched(self) -> bool:
        return self._hard_stop_latched

    def evaluate(
        self,
        intent: QuoteIntent,
        metadata: IntentMetadata,
        portfolio: PortfolioState,
    ) -> RiskDecision:
        global_reasons, action = self._global_checks(portfolio)
        if global_reasons:
            return RiskDecision(None, action, tuple(global_reasons))
        if metadata.data_age_ms > self.limits.stale_book_ms:
            return RiskDecision(None, RiskAction.CANCEL_MARKET, ("stale_book",))
        if metadata.intent_definition_sha256 != metadata.current_definition_sha256:
            return RiskDecision(
                None,
                RiskAction.CANCEL_MARKET,
                ("market_definition_changed",),
            )
        if intent.price % metadata.tick_size != 0:
            return RiskDecision(None, RiskAction.CANCEL_MARKET, ("price_off_tick",))
        if metadata.strategy_kind is StrategyKind.OUTCOME:
            reasons = self._outcome_checks(intent, metadata, portfolio)
        else:
            reasons = self._growth_checks(intent, metadata, portfolio)
        if reasons:
            return RiskDecision(None, RiskAction.NONE, tuple(reasons))
        approved_size = intent.size
        reduced = False
        drawdown_pct = (
            (portfolio.peak_equity_usd - portfolio.equity_usd)
            / portfolio.peak_equity_usd
            * 100
        )
        reason_codes = ["risk_caps_passed"]
        if drawdown_pct >= self.limits.soft_drawdown_pct:
            approved_size = (intent.size / 2).quantize(
                Decimal("0.00000001"), rounding=ROUND_FLOOR
            )
            minimum = (
                self.limits.outcome_order_notional_usd
                if metadata.strategy_kind is StrategyKind.OUTCOME
                else self.limits.growth_min_order_usd
            )
            if intent.price * approved_size < minimum:
                return RiskDecision(
                    None,
                    RiskAction.NONE,
                    ("soft_drawdown_below_minimum_order",),
                )
            reduced = True
            reason_codes.append("soft_drawdown_size_reduction")
        decision_id = hashlib.sha256(
            f"{intent.intent_id}|{approved_size}|{portfolio.observed_at_ms}".encode()
        ).hexdigest()[:32]
        return RiskDecision(
            ApprovedIntent(
                intent=intent,
                approved_size=approved_size,
                decision_id=decision_id,
                reduced=reduced,
                reason_codes=tuple(reason_codes),
            ),
            RiskAction.NONE,
            (),
        )

    def operator_reset(self, authorization: OperatorResetAuthorization) -> None:
        if not authorization.confirmed:
            raise PermissionError("hard stop reset requires explicit confirmation")
        self._hard_stop_latched = False

    def _global_checks(
        self,
        portfolio: PortfolioState,
    ) -> tuple[list[str], RiskAction]:
        if self._hard_stop_latched:
            return ["hard_stop_latched"], RiskAction.HARD_STOP
        drawdown_pct = (
            (portfolio.peak_equity_usd - portfolio.equity_usd)
            / portfolio.peak_equity_usd
            * 100
        )
        if drawdown_pct >= self.limits.hard_drawdown_pct:
            self._hard_stop_latched = True
            return ["hard_drawdown"], RiskAction.HARD_STOP
        reasons: list[str] = []
        if not portfolio.heartbeat_healthy:
            reasons.append("heartbeat_lost")
        if portfolio.orphan_order_count > 0:
            reasons.append("orphan_order")
        if (
            _position_map(portfolio.local_positions)
            != _position_map(portfolio.exchange_positions)
        ):
            reasons.append("position_mismatch")
            if not portfolio.reconciliation_attempted:
                reasons.append("reconciliation_required")
        if portfolio.unknown_loss_usd > self.limits.unknown_loss_stop_usd:
            reasons.append("unknown_operational_loss")
        daily_loss_limit = (
            self.limits.reference_equity_usd
            * self.limits.daily_loss_stop_pct
            / 100
        )
        if portfolio.daily_pnl_usd <= -daily_loss_limit:
            reasons.append("daily_loss_stop")
        return reasons, RiskAction.CANCEL_ALL if reasons else RiskAction.NONE

    def _outcome_checks(
        self,
        intent: QuoteIntent,
        metadata: IntentMetadata,
        portfolio: PortfolioState,
    ) -> list[str]:
        market_id = metadata.outcome_market_id
        token_side = metadata.outcome_token_side
        correlation_group = metadata.correlation_group
        assert market_id is not None
        assert token_side is not None
        assert correlation_group is not None
        notional = intent.price * intent.size
        reasons: list[str] = []
        if not (
            self.limits.outcome_order_notional_usd
            <= notional
            <= self.limits.outcome_order_notional_usd * 2
        ):
            reasons.append("outcome_order_notional")
        yes_impact, no_impact = _outcome_quote_impact(intent, token_side)
        exposure_by_market: dict[str, OutcomeExposure] = {
            exposure.outcome_market_id: exposure
            for exposure in portfolio.outcome_exposures
        }
        current = exposure_by_market.get(
            market_id,
            OutcomeExposure(market_id, correlation_group, Decimal(0), Decimal(0)),
        )
        exposure_by_market[market_id] = replace(
            current,
            pnl_if_yes_usd=current.pnl_if_yes_usd + yes_impact,
            pnl_if_no_usd=current.pnl_if_no_usd + no_impact,
            gross_inventory_usd=current.gross_inventory_usd + notional,
        )
        if (
            exposure_by_market[market_id].gross_inventory_usd
            > self.limits.max_outcome_inventory_per_market_usd
        ):
            reasons.append("outcome_inventory_per_market")
        updated = tuple(exposure_by_market.values())
        market_loss = -min(
            exposure_by_market[market_id].pnl_if_yes_usd,
            exposure_by_market[market_id].pnl_if_no_usd,
            Decimal(0),
        )
        if market_loss > self.limits.max_outcome_settlement_loss_per_market_usd:
            reasons.append("outcome_settlement_loss_per_market")
        if len(updated) > self.limits.max_outcome_markets:
            reasons.append("too_many_outcome_markets")
        total_loss = sum(
            (
                -min(exposure.pnl_if_yes_usd, exposure.pnl_if_no_usd, Decimal(0))
                for exposure in updated
            ),
            Decimal(0),
        )
        if total_loss > self.limits.max_total_settlement_loss_usd:
            reasons.append("total_outcome_settlement_loss")
        correlated = [
            exposure
            for exposure in updated
            if exposure.correlation_group == correlation_group
        ]
        correlated_loss = max(
            -sum((exposure.pnl_if_yes_usd for exposure in correlated), Decimal(0)),
            -sum((exposure.pnl_if_no_usd for exposure in correlated), Decimal(0)),
            Decimal(0),
        )
        if correlated_loss > self.limits.max_correlated_settlement_loss_usd:
            reasons.append("correlated_outcome_settlement_loss")
        return reasons

    def _growth_checks(
        self,
        intent: QuoteIntent,
        metadata: IntentMetadata,
        portfolio: PortfolioState,
    ) -> list[str]:
        notional = intent.price * intent.size
        reasons: list[str] = []
        if not (
            self.limits.growth_min_order_usd
            <= notional
            <= self.limits.growth_max_order_usd
        ):
            reasons.append("growth_order_notional")
        if portfolio.growth_active_dexes and metadata.dex not in set(
            portfolio.growth_active_dexes
        ):
            reasons.append("single_growth_dex_limit")
        inventory = dict(portfolio.growth_inventory_usd)
        signed_notional = notional if intent.side is Side.BUY else -notional
        inventory[intent.market] = (
            inventory.get(intent.market, Decimal(0)) + signed_notional
        )
        if (
            abs(inventory[intent.market])
            > self.limits.max_growth_inventory_per_symbol_usd
        ):
            reasons.append("growth_inventory_per_symbol")
        gross = sum((abs(value) for value in inventory.values()), Decimal(0))
        net = abs(sum(inventory.values(), Decimal(0)))
        if gross > self.limits.max_growth_gross_inventory_usd:
            reasons.append("growth_gross_inventory")
        if net > self.limits.max_growth_net_delta_usd:
            reasons.append("growth_net_delta")
        return reasons
