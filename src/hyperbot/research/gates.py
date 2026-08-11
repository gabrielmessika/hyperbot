"""Fail-closed M5 research gates; never a canary authorization."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from hyperbot.research.outcomes import OutcomeResearchResult


@dataclass(frozen=True, slots=True)
class ResearchGateDecision:
    eligible_for_shadow_research: bool
    reasons: tuple[str, ...]
    fold_count: int
    aggregate_profit_factor: Decimal | None
    maximum_underlying_contribution_pct: Decimal


def evaluate_research_gate(
    result: OutcomeResearchResult,
    *,
    allocation_usd: Decimal,
    central_pnl_usd: Decimal,
    pessimistic_pnl_usd: Decimal,
    double_fee_profit_factor: Decimal | None,
    minimum_profit_factor: Decimal = Decimal("1.20"),
    maximum_drawdown_pct: Decimal = Decimal("10"),
    maximum_underlying_contribution_pct: Decimal = Decimal("40"),
) -> ResearchGateDecision:
    if not allocation_usd.is_finite() or allocation_usd <= 0:
        raise ValueError("allocation_usd must be finite and positive")
    reasons: list[str] = []
    if len(result.folds) < 3:
        reasons.append("fewer_than_three_oos_folds")
    if any(fold.maker_pnl_usd <= 0 for fold in result.folds):
        reasons.append("non_positive_oos_fold")
    gains = sum(
        (fold.maker_pnl_usd for fold in result.folds if fold.maker_pnl_usd > 0),
        Decimal(0),
    )
    losses = -sum(
        (fold.maker_pnl_usd for fold in result.folds if fold.maker_pnl_usd < 0),
        Decimal(0),
    )
    aggregate_profit_factor = gains / losses if losses > 0 else None
    if (
        aggregate_profit_factor is not None
        and aggregate_profit_factor <= minimum_profit_factor
    ):
        reasons.append("profit_factor_below_threshold")
    maximum_drawdown = max(
        (fold.max_drawdown_usd for fold in result.folds),
        default=Decimal(0),
    )
    if maximum_drawdown / allocation_usd * 100 >= maximum_drawdown_pct:
        reasons.append("drawdown_above_threshold")
    positive_contributions = [
        pnl for _, pnl in result.pnl_by_underlying if pnl > 0
    ]
    total_positive = sum(positive_contributions, Decimal(0))
    concentration = (
        max(positive_contributions, default=Decimal(0)) / total_positive * 100
        if total_positive > 0
        else Decimal(100)
    )
    if concentration > maximum_underlying_contribution_pct:
        reasons.append("underlying_concentration_above_40pct")
    if central_pnl_usd <= 0:
        reasons.append("central_replay_not_positive")
    if pessimistic_pnl_usd <= -allocation_usd * Decimal("0.10"):
        reasons.append("pessimistic_replay_catastrophic")
    if double_fee_profit_factor is not None and double_fee_profit_factor < 1:
        reasons.append("double_fee_profit_factor_below_one")
    if result.bootstrap.p05_usd <= 0:
        reasons.append("bootstrap_lower_bound_not_positive")
    return ResearchGateDecision(
        eligible_for_shadow_research=not reasons,
        reasons=tuple(reasons),
        fold_count=len(result.folds),
        aggregate_profit_factor=aggregate_profit_factor,
        maximum_underlying_contribution_pct=concentration,
    )
