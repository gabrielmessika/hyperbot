from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest

from hyperbot.execution import ShadowExecutionGateway, SimulatedExchangeState
from hyperbot.models import EventContext, QuoteIntent, Side, TimeSource
from hyperbot.risk import (
    IntentMetadata,
    OperatorResetAuthorization,
    OutcomeExposure,
    OutcomeTokenSide,
    PortfolioState,
    RiskAction,
    RiskSupervisor,
    StrategyKind,
)
from hyperbot.strategies import (
    GrowthMakerStrategy,
    MarketState,
    OutcomeMakerStrategy,
)


def _context() -> EventContext:
    return EventContext("risk-test", "test", "1" * 64, TimeSource.EXCHANGE)


def _market_state(*, stale: bool = False, healthy: bool = True) -> MarketState:
    return MarketState(
        context=_context(),
        market="outcome:42:0",
        observed_ts_ms=1_000,
        now_ts_ms=1_600 if stale else 1_100,
        best_bid=Decimal("0.49"),
        best_ask=Decimal("0.51"),
        bid_size=Decimal("100"),
        ask_size=Decimal("100"),
        oracle_px=Decimal("0.50"),
        mark_px=Decimal("0.50"),
        microprice=Decimal("0.50"),
        tick_size=Decimal("0.01"),
        size_increment=Decimal("0.001"),
        maker_fee_bps=Decimal("0.3"),
        inventory_units=Decimal("0"),
        data_healthy=healthy,
        expiry_ts_ms=100_000,
    )


def _intent(
    *,
    side: Side = Side.BUY,
    price: str = "0.5",
    size: str = "20",
    market: str = "outcome:42:0",
) -> QuoteIntent:
    return QuoteIntent(
        context=_context(),
        intent_id=f"intent-{side.value}-{price}-{size}-{market}",
        strategy="test",
        market=market,
        side=side,
        price=Decimal(price),
        size=Decimal(size),
        ttl_ms=1_000,
        fair_value=Decimal("0.5") if Decimal(price) < 1 else Decimal(price),
        min_edge_bps=Decimal("1"),
        inventory_before=Decimal("0"),
        reason_codes=("fixture",),
    )


def _metadata(
    *,
    strategy: StrategyKind = StrategyKind.OUTCOME,
    age: int = 100,
    intent_hash: str = "a" * 64,
    current_hash: str = "a" * 64,
    tick: str = "0.01",
) -> IntentMetadata:
    return IntentMetadata(
        strategy_kind=strategy,
        dex="outcome" if strategy is StrategyKind.OUTCOME else "cash",
        data_age_ms=age,
        order_type="ALO",
        intent_definition_sha256=intent_hash,
        current_definition_sha256=current_hash,
        tick_size=Decimal(tick),
        outcome_market_id="outcome-42" if strategy is StrategyKind.OUTCOME else None,
        outcome_token_side=(
            OutcomeTokenSide.YES if strategy is StrategyKind.OUTCOME else None
        ),
        correlation_group="btc-expiry" if strategy is StrategyKind.OUTCOME else None,
    )


def _portfolio(**changes: object) -> PortfolioState:
    values: dict[str, object] = {
        "observed_at_ms": 2_000,
        "equity_usd": Decimal("1000"),
        "peak_equity_usd": Decimal("1000"),
        "daily_pnl_usd": Decimal("0"),
        "unknown_loss_usd": Decimal("0"),
        "heartbeat_healthy": True,
        "orphan_order_count": 0,
        "reconciliation_attempted": True,
        "local_positions": (),
        "exchange_positions": (),
        "outcome_exposures": (),
        "growth_inventory_usd": (),
    }
    values.update(changes)
    return PortfolioState(**values)  # type: ignore[arg-type]


def test_strategies_emit_only_intents_and_refuse_stale_data() -> None:
    outcome_intents = OutcomeMakerStrategy().on_market_state(_market_state())
    growth_intents = GrowthMakerStrategy().on_market_state(
        replace(_market_state(), market="cash:AMZN", expiry_ts_ms=None)
    )

    assert len(outcome_intents) == 2
    assert len(growth_intents) == 2
    assert all(isinstance(intent, QuoteIntent) for intent in outcome_intents)
    assert all(intent.price * intent.size >= 10 for intent in outcome_intents)
    assert not OutcomeMakerStrategy().on_market_state(_market_state(stale=True))
    assert not GrowthMakerStrategy().on_market_state(
        replace(_market_state(), market="cash:AMZN", data_healthy=False)
    )


def test_supervisor_is_the_only_approval_authority_and_checks_outcome_payoff() -> None:
    supervisor = RiskSupervisor()
    approved = supervisor.evaluate(_intent(), _metadata(), _portfolio())
    oversized_payoff = supervisor.evaluate(
        _intent(side=Side.SELL, price="0.1", size="200"),
        _metadata(),
        _portfolio(),
    )

    assert approved.is_approved
    assert approved.approved is not None
    assert approved.approved.approved_size == Decimal("20")
    assert not oversized_payoff.is_approved
    assert "outcome_settlement_loss_per_market" in (
        oversized_payoff.rejection_reasons
    )


def test_correlated_yes_no_payoff_cap_is_enforced() -> None:
    existing = OutcomeExposure(
        outcome_market_id="outcome-other",
        correlation_group="btc-expiry",
        pnl_if_yes_usd=Decimal("5"),
        pnl_if_no_usd=Decimal("-25"),
    )
    decision = RiskSupervisor().evaluate(
        _intent(price="0.5", size="20"),
        _metadata(),
        _portfolio(outcome_exposures=(existing,)),
    )

    assert not decision.is_approved
    assert "correlated_outcome_settlement_loss" in decision.rejection_reasons


@pytest.mark.parametrize(
    ("portfolio", "reason"),
    [
        (_portfolio(heartbeat_healthy=False), "heartbeat_lost"),
        (_portfolio(orphan_order_count=1), "orphan_order"),
        (
            _portfolio(
                local_positions=(("BTC", Decimal("1")),),
                exchange_positions=(("BTC", Decimal("0")),),
            ),
            "position_mismatch",
        ),
        (_portfolio(unknown_loss_usd=Decimal("6")), "unknown_operational_loss"),
        (_portfolio(daily_pnl_usd=Decimal("-15")), "daily_loss_stop"),
    ],
)
def test_global_incidents_are_fail_closed(
    portfolio: PortfolioState,
    reason: str,
) -> None:
    decision = RiskSupervisor().evaluate(_intent(), _metadata(), portfolio)

    assert decision.action is RiskAction.CANCEL_ALL
    assert reason in decision.rejection_reasons


def test_stale_and_market_definition_changes_cancel_the_market() -> None:
    stale = RiskSupervisor().evaluate(
        _intent(), _metadata(age=501), _portfolio()
    )
    changed = RiskSupervisor().evaluate(
        _intent(),
        _metadata(intent_hash="a" * 64, current_hash="b" * 64),
        _portfolio(),
    )

    assert stale.action is RiskAction.CANCEL_MARKET
    assert changed.action is RiskAction.CANCEL_MARKET
    assert changed.rejection_reasons == ("market_definition_changed",)


def test_hard_drawdown_latches_until_explicit_operator_reset() -> None:
    supervisor = RiskSupervisor()
    hard = supervisor.evaluate(
        _intent(),
        _metadata(),
        _portfolio(equity_usd=Decimal("880")),
    )
    still_latched = supervisor.evaluate(_intent(), _metadata(), _portfolio())

    assert hard.action is RiskAction.HARD_STOP
    assert still_latched.rejection_reasons == ("hard_stop_latched",)
    with pytest.raises(PermissionError):
        supervisor.operator_reset(
            OperatorResetAuthorization("operator", "reviewed", confirmed=False)
        )
    supervisor.operator_reset(
        OperatorResetAuthorization("operator", "reviewed", confirmed=True)
    )
    assert supervisor.evaluate(_intent(), _metadata(), _portfolio()).is_approved


def test_soft_drawdown_reduces_only_when_minimum_order_survives() -> None:
    supervisor = RiskSupervisor()
    reduced = supervisor.evaluate(
        _intent(price="0.5", size="40"),
        _metadata(),
        _portfolio(equity_usd=Decimal("910")),
    )
    rejected_minimum = supervisor.evaluate(
        _intent(price="0.5", size="20"),
        _metadata(),
        _portfolio(equity_usd=Decimal("910")),
    )

    assert reduced.approved is not None
    assert reduced.approved.reduced
    assert reduced.approved.approved_size == Decimal("20.00000000")
    assert rejected_minimum.rejection_reasons == (
        "soft_drawdown_below_minimum_order",
    )


def test_growth_inventory_caps_are_checked_after_hypothetical_fill() -> None:
    decision = RiskSupervisor().evaluate(
        _intent(price="10", size="2", market="cash:AMZN"),
        _metadata(strategy=StrategyKind.GROWTH, tick="0.1"),
        _portfolio(
            growth_inventory_usd=(("cash:AMZN", Decimal("45")),)
        ),
    )

    assert not decision.is_approved
    assert "growth_inventory_per_symbol" in decision.rejection_reasons

    other_dex = RiskSupervisor().evaluate(
        _intent(price="10", size="1", market="cash:AMZN"),
        _metadata(strategy=StrategyKind.GROWTH, tick="0.1"),
        _portfolio(growth_active_dexes=("xyz",)),
    )
    assert "single_growth_dex_limit" in other_dex.rejection_reasons


def test_shadow_gateway_records_approvals_and_exchange_wins_reconciliation() -> None:
    decision = RiskSupervisor().evaluate(_intent(), _metadata(), _portfolio())
    assert decision.approved is not None
    gateway = ShadowExecutionGateway()
    staged = gateway.stage_approved((decision.approved,), timestamp_ms=2_100)
    local = SimulatedExchangeState(
        observed_at_ms=2_000,
        positions=(("BTC", Decimal("1")),),
        open_order_ids=("local-only",),
    )
    exchange = SimulatedExchangeState(
        observed_at_ms=2_100,
        positions=(("BTC", Decimal("2")),),
        open_order_ids=("exchange-orphan",),
    )
    reconciliation = gateway.reconcile(
        local_state=local,
        exchange_state=exchange,
    )

    assert staged[0].shadow_only
    assert not hasattr(gateway, "send_order")
    assert reconciliation.authoritative_state is exchange
    assert reconciliation.position_mismatches == ("BTC",)
    assert reconciliation.orphan_order_ids == ("exchange-orphan",)
    assert reconciliation.missing_exchange_order_ids == ("local-only",)
