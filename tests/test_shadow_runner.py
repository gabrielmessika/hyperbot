from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from hyperbot.event_store import JsonlEventStore
from hyperbot.execution import ShadowExecutionGateway, SimulatedExchangeState
from hyperbot.models import BookLevel, DatasetTier, EventContext, Side, TimeSource
from hyperbot.replay import (
    FillModelKind,
    ReplayBook,
    ReplayConfig,
    ReplayEngine,
    ReplayQuote,
    ReplayResult,
    ReplayTrade,
)
from hyperbot.risk import (
    IntentMetadata,
    OutcomeTokenSide,
    PortfolioState,
    RiskSupervisor,
    StrategyKind,
)
from hyperbot.services.shadow_runner import (
    ShadowGateStage,
    ShadowRunner,
    compare_fill_models,
    evaluate_shadow_qualification,
    write_shadow_daily_report,
)
from hyperbot.strategies import MarketState, OutcomeMakerStrategy


def _context() -> EventContext:
    return EventContext("shadow-test", "test", "2" * 64, TimeSource.EXCHANGE)


def _market_state() -> MarketState:
    return MarketState(
        context=_context(),
        market="outcome:42:0",
        observed_ts_ms=1_000,
        now_ts_ms=1_100,
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
        data_healthy=True,
        expiry_ts_ms=100_000,
    )


def _portfolio() -> PortfolioState:
    return PortfolioState(
        observed_at_ms=2_000,
        equity_usd=Decimal("1000"),
        peak_equity_usd=Decimal("1000"),
        daily_pnl_usd=Decimal("0"),
        unknown_loss_usd=Decimal("0"),
        heartbeat_healthy=True,
        orphan_order_count=0,
        reconciliation_attempted=True,
        local_positions=(),
        exchange_positions=(),
        outcome_exposures=(),
        growth_inventory_usd=(),
    )


def _metadata() -> IntentMetadata:
    return IntentMetadata(
        strategy_kind=StrategyKind.OUTCOME,
        dex="outcome",
        data_age_ms=100,
        order_type="ALO",
        intent_definition_sha256="a" * 64,
        current_definition_sha256="a" * 64,
        tick_size=Decimal("0.01"),
        outcome_market_id="outcome-42",
        outcome_token_side=OutcomeTokenSide.YES,
        correlation_group="btc-expiry",
    )


def _runner(tmp_path: Path) -> ShadowRunner:
    return ShadowRunner(
        strategy=OutcomeMakerStrategy(),
        supervisor=RiskSupervisor(),
        gateway=ShadowExecutionGateway(),
        store=JsonlEventStore(tmp_path, fsync=False),
    )


def _replay_results(quote: ReplayQuote) -> tuple[ReplayResult, ReplayResult]:
    events = (
        ReplayBook(
            market=quote.market,
            timestamp_ms=0,
            source_sequence=0,
            bids=(BookLevel(quote.price, Decimal("0.1")),),
            asks=(BookLevel(quote.price + Decimal("0.02"), Decimal("1")),),
        ),
        ReplayTrade(
            market=quote.market,
            timestamp_ms=100,
            source_sequence=1,
            aggressor_side=Side.SELL,
            price=quote.price,
            size=Decimal("0.075"),
        ),
        ReplayBook(
            market=quote.market,
            timestamp_ms=30_100,
            source_sequence=2,
            bids=(BookLevel(quote.price - Decimal("0.01"), Decimal("1")),),
            asks=(BookLevel(quote.price + Decimal("0.01"), Decimal("1")),),
        ),
    )
    engine = ReplayEngine()
    central = engine.run(
        config=ReplayConfig(
            run_id="shadow-central",
            code_version="test",
            model=FillModelKind.CENTRAL,
            dataset_tiers=(DatasetTier.A,),
            placement_latency_ms=0,
            cancel_latency_ms=0,
        ),
        events=events,
        quotes=(quote,),
    )
    pessimistic = engine.run(
        config=ReplayConfig(
            run_id="shadow-pessimistic",
            code_version="test",
            model=FillModelKind.PESSIMISTIC,
            dataset_tiers=(DatasetTier.A,),
            placement_latency_ms=0,
            cancel_latency_ms=0,
        ),
        events=events,
        quotes=(quote,),
    )
    return central, pessimistic


def test_shadow_cycle_persists_intents_risk_and_quotes_without_orders(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    cycle = runner.process_market_state(
        state=_market_state(),
        metadata=_metadata(),
        portfolio=_portfolio(),
    )
    store = JsonlEventStore(tmp_path, fsync=False)

    assert len(cycle.intents) == 2
    assert len(cycle.approvals) == 2
    assert len(cycle.staged_quote_ids) == 2
    assert len(store.read_records("shadow-intents")) == 2
    assert len(store.read_records("shadow-risk-audit")) == 2
    assert len(store.read_records("shadow-quotes")) == 2
    assert not hasattr(runner.gateway, "send_order")


def test_shadow_restart_blocks_until_exchange_authoritative_state_is_clean(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    exchange = SimulatedExchangeState(
        observed_at_ms=2_000,
        positions=(("BTC", Decimal("2")),),
        open_order_ids=("orphan",),
    )
    local = SimulatedExchangeState(
        observed_at_ms=1_900,
        positions=(("BTC", Decimal("1")),),
        open_order_ids=(),
    )
    dirty = runner.reconcile_restart(local_state=local, exchange_state=exchange)
    blocked = runner.process_market_state(
        state=_market_state(),
        metadata=_metadata(),
        portfolio=_portfolio(),
    )

    assert not dirty.clean
    assert dirty.authoritative_state is exchange
    assert blocked.restart_blocked
    assert not blocked.intents

    clean = runner.reconcile_restart(local_state=exchange, exchange_state=exchange)
    assert clean.clean
    assert not runner.restart_blocked


def test_shadow_fill_estimate_is_compared_to_observed_markouts(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    cycle = runner.process_market_state(
        state=_market_state(),
        metadata=_metadata(),
        portfolio=_portfolio(),
    )
    buy = next(
        approval
        for approval in cycle.approvals
        if approval.intent.side is Side.BUY
    )
    quote = ReplayQuote(
        quote_id=buy.intent.intent_id,
        market=buy.intent.market,
        side=buy.intent.side,
        price=buy.intent.price,
        size=buy.approved_size,
        submitted_ts_ms=0,
        cancel_requested_ts_ms=None,
        maker_fee_bps=Decimal("0.3"),
    )
    central_raw, pessimistic_raw = _replay_results(quote)
    events = runner.record_replay_evaluation(
        result=central_raw,
        quote_ids=(quote.quote_id,),
        evaluation_ts_ms=31_000,
        context=_market_state(),
    )
    comparison = compare_fill_models(central_raw, pessimistic_raw)

    assert events[0].predicted_fill
    assert events[0].markout_30s is not None
    assert comparison.central_fill_count == 1
    assert comparison.pessimistic_fill_count == 0
    assert len(
        JsonlEventStore(tmp_path, fsync=False).read_records(
            "shadow-fill-evaluations"
        )
    ) == 1


def test_fourteen_day_gate_is_consecutive_and_never_authorizes_canary(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path)
    state = _market_state()
    base = runner.daily_report(
        report_date=date(2026, 8, 1),
        state=state,
        quality_day_qualified=True,
        replay_compatible=True,
        latency_stress_tolerable=True,
    )
    reports = tuple(
        replace(
            base,
            report_date=(date(2026, 8, 1) + timedelta(days=index)).isoformat(),
        )
        for index in range(14)
    )

    thirteen = evaluate_shadow_qualification(reports[:13])
    fourteen = evaluate_shadow_qualification(reports)

    assert thirteen.stage is ShadowGateStage.INSUFFICIENT_FOURTEEN_DAYS
    assert fourteen.stage is ShadowGateStage.FOURTEEN_DAYS_COMPLETE
    assert fourteen.eligible_for_canary_discussion
    assert not fourteen.canary_authorized

    failed = (*reports[:-1], replace(reports[-1], qualified_day=False))
    assert evaluate_shadow_qualification(failed).consecutive_qualified_days == 0


def test_shadow_daily_report_is_checksummed_and_incidents_disqualify(
    tmp_path: Path,
) -> None:
    runner = _runner(tmp_path / "events")
    mismatch = SimulatedExchangeState(
        observed_at_ms=2_000,
        positions=(("BTC", Decimal("1")),),
        open_order_ids=(),
    )
    empty = SimulatedExchangeState(2_000, (), ())
    runner.reconcile_restart(local_state=empty, exchange_state=mismatch)
    report = runner.daily_report(
        report_date=date(2026, 8, 11),
        state=_market_state(),
        quality_day_qualified=True,
        replay_compatible=True,
        latency_stress_tolerable=True,
    )
    json_path, markdown_path = write_shadow_daily_report(report, tmp_path / "reports")

    assert not report.qualified_day
    assert "risk_violations" in report.qualification_reasons
    assert json_path.with_suffix(".json.sha256").is_file()
    assert "journée qualifiée : `false`" in markdown_path.read_text(
        encoding="utf-8"
    )


def test_batch_risk_accounts_for_prior_hypothetical_approvals() -> None:
    from hyperbot.risk import RiskLimits

    supervisor = RiskSupervisor(
        RiskLimits(max_outcome_inventory_per_market_usd=Decimal("25"))
    )
    state = _market_state()
    intents = OutcomeMakerStrategy().on_market_state(state)
    repeated = (intents[0], intents[0], intents[0])
    decisions = supervisor.evaluate_batch(
        tuple((intent, _metadata()) for intent in repeated),
        _portfolio(),
    )

    assert decisions[0].is_approved
    assert decisions[1].is_approved
    assert not decisions[2].is_approved
    assert "outcome_inventory_per_market" in decisions[2].rejection_reasons
