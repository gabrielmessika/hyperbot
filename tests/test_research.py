from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from hyperbot.event_store import EventIntegrityError
from hyperbot.models import (
    DatasetTier,
    EventContext,
    MarketDefinition,
    MarketKind,
    MarketStatus,
    TimeSource,
)
from hyperbot.research import (
    DigitalOutcomeBenchmark,
    FoldWindow,
    GrowthMarketObservation,
    GrowthMarketScanner,
    IsotonicCalibrator,
    OutcomeResearchEvaluator,
    OutcomeSample,
    empirical_volatility,
    evaluate_research_gate,
)
from hyperbot.research.journal import ResearchVariant, VariantJournal


def _sample(
    sample_id: str,
    observed: int,
    settlement: int,
    outcome: int,
    *,
    underlying: str = "BTC",
    pnl: str = "1",
) -> OutcomeSample:
    return OutcomeSample(
        sample_id=sample_id,
        observed_ts_ms=observed,
        settlement_ts_ms=settlement,
        market=f"outcome:{sample_id}",
        underlying=underlying,
        spot=Decimal("100" if outcome else "90"),
        strike=Decimal("95"),
        realized_volatility_annualized=Decimal("0.5"),
        outcome=outcome,
        maker_pnl_usd=Decimal(pnl),
        legacy_bot_pnl_usd=Decimal("0.1"),
        volatility_regime="medium",
        expiry_bucket="under_1h",
    )


def _research_fixture() -> tuple[tuple[OutcomeSample, ...], tuple[FoldWindow, ...]]:
    samples: list[OutcomeSample] = []
    windows: list[FoldWindow] = []
    for fold_index, offset in enumerate((0, 1_000, 2_000), start=1):
        samples.extend(
            [
                _sample(f"train-{fold_index}", offset + 10, offset + 20, 1),
                _sample(f"cal-a-{fold_index}", offset + 120, offset + 200, 1),
                _sample(f"cal-b-{fold_index}", offset + 140, offset + 220, 0),
                _sample(f"purged-{fold_index}", offset + 180, offset + 280, 1),
                _sample(f"test-btc-{fold_index}", offset + 320, offset + 350, 1),
                _sample(
                    f"test-eth-{fold_index}",
                    offset + 330,
                    offset + 360,
                    0,
                    underlying="ETH",
                    pnl="-0.2",
                ),
            ]
        )
        windows.append(
            FoldWindow(
                fold_id=f"fold-{fold_index}",
                train_begin_ms=offset,
                train_end_ms=offset + 100,
                calibration_begin_ms=offset + 100,
                calibration_end_ms=offset + 200,
                test_begin_ms=offset + 300,
                test_end_ms=offset + 400,
                purge_ms=50,
            )
        )
    return tuple(samples), tuple(windows)


def test_digital_benchmark_volatility_and_isotonic_calibration() -> None:
    probability = DigitalOutcomeBenchmark().probability(
        _sample("atm", 0, 86_400_000, 1)
    )
    volatility = empirical_volatility(
        (Decimal("100"), Decimal("101"), Decimal("99"), Decimal("102")),
        periods_per_year=365,
    )
    calibrator = IsotonicCalibrator.fit(
        (
            (Decimal("0.1"), 0),
            (Decimal("0.4"), 1),
            (Decimal("0.5"), 0),
            (Decimal("0.9"), 1),
        )
    )
    predictions = tuple(
        calibrator.predict(value)
        for value in (Decimal("0.1"), Decimal("0.4"), Decimal("0.5"), Decimal("0.9"))
    )

    assert Decimal("0") < probability < Decimal("1")
    assert volatility > 0
    assert predictions == tuple(sorted(predictions))


def test_purged_walk_forward_oos_and_cluster_bootstrap_are_reproducible() -> None:
    samples, windows = _research_fixture()
    evaluator = OutcomeResearchEvaluator()
    first = evaluator.evaluate(
        samples=samples,
        windows=windows,
        dataset_tiers=(DatasetTier.B,),
        bootstrap_iterations=200,
        bootstrap_seed=7,
    )
    second = evaluator.evaluate(
        samples=tuple(reversed(samples)),
        windows=windows,
        dataset_tiers=(DatasetTier.B,),
        bootstrap_iterations=200,
        bootstrap_seed=7,
    )

    assert first == second
    assert first.evidence_label == "legacy_research_only"
    assert len(first.folds) == 3
    assert all(fold.purged_calibration_count == 1 for fold in first.folds)
    assert all(fold.maker_pnl_usd == Decimal("0.8") for fold in first.folds)
    assert all(fold.do_nothing_pnl_usd == 0 for fold in first.folds)


def test_research_gate_blocks_concentration_and_can_pass_diversified_fixture() -> None:
    samples, windows = _research_fixture()
    result = OutcomeResearchEvaluator().evaluate(
        samples=samples,
        windows=windows,
        dataset_tiers=(DatasetTier.A,),
        bootstrap_iterations=200,
        bootstrap_seed=7,
    )
    concentrated = evaluate_research_gate(
        result,
        allocation_usd=Decimal("100"),
        central_pnl_usd=Decimal("3"),
        pessimistic_pnl_usd=Decimal("-1"),
        double_fee_profit_factor=None,
    )
    diversified = evaluate_research_gate(
        replace(
            result,
            bootstrap=replace(result.bootstrap, p05_usd=Decimal("1")),
            pnl_by_underlying=(
                ("BTC", Decimal("1")),
                ("ETH", Decimal("1")),
                ("SOL", Decimal("1")),
            ),
        ),
        allocation_usd=Decimal("100"),
        central_pnl_usd=Decimal("3"),
        pessimistic_pnl_usd=Decimal("-1"),
        double_fee_profit_factor=None,
    )

    assert not concentrated.eligible_for_shadow_research
    assert "underlying_concentration_above_40pct" in concentrated.reasons
    assert diversified.eligible_for_shadow_research


def _definition(*, maker_fee_bps: str = "0.3") -> MarketDefinition:
    return MarketDefinition(
        context=EventContext("growth-test", "test", "e" * 64, TimeSource.EXCHANGE),
        observed_at_ms=100,
        catalog_version=1,
        definition_version=1,
        market_id="cash:cash:AMZN",
        market_kind=MarketKind.HIP3_PERP,
        dex="cash",
        coin="cash:AMZN",
        display_name="cash:AMZN",
        asset_id=160006,
        sz_decimals=3,
        size_increment=Decimal("0.001"),
        max_price_decimals=3,
        max_significant_figures=5,
        tick_size_at_reference=Decimal("0.01"),
        minimum_order_notional_usd=Decimal("10"),
        growth_mode="enabled",
        deployer_fee_scale=Decimal("1"),
        maker_fee_bps=Decimal(maker_fee_bps),
        taker_fee_bps=Decimal("0.9"),
        fee_basis="tier0_hip3_before_account_discounts",
        oracle_px=Decimal("200"),
        mark_px=Decimal("200"),
        status=MarketStatus.ACTIVE,
        definition_sha256="f" * 64,
        previous_definition_sha256=None,
        quality_flags=(),
    )


def _growth_observation() -> GrowthMarketObservation:
    return GrowthMarketObservation(
        market_id="cash:cash:AMZN",
        observed_at_ms=100,
        median_spread_1h_bps=Decimal("10"),
        median_spread_24h_bps=Decimal("9"),
        spread_p10_1h_bps=Decimal("8"),
        spread_p90_1h_bps=Decimal("15"),
        depth_5bps_usd=Decimal("100"),
        depth_10bps_usd=Decimal("300"),
        depth_25bps_usd=Decimal("600"),
        daily_volume_usd=Decimal("300000"),
        aggressive_trade_count_1h=100,
        oracle_age_ms=100,
        oracle_mark_gap_bps=Decimal("1"),
        stale_fraction_1h=Decimal("0.001"),
        deployer_incident=False,
    )


def test_growth_scanner_uses_runtime_catalog_fees_and_fails_closed() -> None:
    scanner = GrowthMarketScanner()
    qualified = scanner.scan(
        definitions=(_definition(),), observations=(_growth_observation(),)
    )[0]
    expensive = scanner.scan(
        definitions=(_definition(maker_fee_bps="6"),),
        observations=(_growth_observation(),),
    )[0]
    unknown = scanner.scan(definitions=(), observations=(_growth_observation(),))[0]

    assert qualified.qualified
    assert qualified.net_spread_after_round_trip_maker_fees_bps == Decimal("9.4")
    assert qualified.definition_sha256 == "f" * 64
    assert not expensive.qualified
    assert "spread_does_not_cover_round_trip_maker_fees" in expensive.rejection_reasons
    assert unknown.rejection_reasons[0] == "unknown_market"


def test_variant_journal_is_append_only_unique_and_hash_chained(tmp_path: Path) -> None:
    journal = VariantJournal(tmp_path / "variants.jsonl", fsync=False)
    variant = ResearchVariant(
        variant_id="digital-v1",
        created_at_ms=100,
        hypothesis="Digital probability calibrated on prior folds",
        code_version="test",
        config_sha256="a" * 64,
        train_period="1/10",
        calibration_period="11/20",
        test_period="21/30",
        metrics_json=json.dumps({"brier": "0.2"}, sort_keys=True),
    )
    journal.append(variant)
    journal.append(replace(variant, variant_id="digital-v2"))

    assert journal.validate() == 2
    with pytest.raises(ValueError, match="already exists"):
        journal.append(variant)

    content = bytearray((tmp_path / "variants.jsonl").read_bytes())
    content[len(content) // 2] ^= 1
    (tmp_path / "variants.jsonl").write_bytes(content)
    with pytest.raises(EventIntegrityError):
        journal.validate()
