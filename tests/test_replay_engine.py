from __future__ import annotations

from decimal import Decimal

import pytest

from hyperbot.legacy.policy import LegacyEvidenceError
from hyperbot.models import BookLevel, DatasetTier, Side
from hyperbot.replay import (
    FillModelKind,
    ReplayBook,
    ReplayConfig,
    ReplayEngine,
    ReplayMark,
    ReplayQuote,
    ReplayTrade,
    VirtualClock,
)
from hyperbot.replay.engine import ReplayDataError


def _book(
    timestamp_ms: int,
    sequence: int,
    *,
    bid: str = "100",
    ask: str = "101",
    bid_size: str = "5",
    ask_size: str = "5",
) -> ReplayBook:
    return ReplayBook(
        market="BTC",
        timestamp_ms=timestamp_ms,
        source_sequence=sequence,
        bids=(BookLevel(Decimal(bid), Decimal(bid_size)),),
        asks=(BookLevel(Decimal(ask), Decimal(ask_size)),),
    )


def _trade(timestamp_ms: int, sequence: int, size: str) -> ReplayTrade:
    return ReplayTrade(
        market="BTC",
        timestamp_ms=timestamp_ms,
        source_sequence=sequence,
        aggressor_side=Side.SELL,
        price=Decimal("100"),
        size=Decimal(size),
    )


def _quote(
    *,
    size: str = "2",
    submitted: int = 0,
    cancel: int | None = None,
    fee_bps: str = "1",
) -> ReplayQuote:
    return ReplayQuote(
        quote_id="quote-1",
        market="BTC",
        side=Side.BUY,
        price=Decimal("100"),
        size=Decimal(size),
        submitted_ts_ms=submitted,
        cancel_requested_ts_ms=cancel,
        maker_fee_bps=Decimal(fee_bps),
    )


def _config(
    model: FillModelKind,
    *,
    tier: DatasetTier = DatasetTier.A,
    placement_latency_ms: int = 0,
    cancel_latency_ms: int = 0,
) -> ReplayConfig:
    return ReplayConfig(
        run_id="replay-test",
        code_version="test",
        model=model,
        dataset_tiers=(tier,),
        placement_latency_ms=placement_latency_ms,
        cancel_latency_ms=cancel_latency_ms,
    )


def test_virtual_clock_refuses_backward_time() -> None:
    clock = VirtualClock(10)
    clock.advance_to(11)
    with pytest.raises(ReplayDataError, match="backwards"):
        clock.advance_to(10)


def test_pessimistic_queue_must_be_fully_consumed_and_fills_partially() -> None:
    result = ReplayEngine().run(
        config=_config(FillModelKind.PESSIMISTIC),
        events=(
            _book(0, 0),
            _trade(10, 1, "4"),
            _trade(20, 2, "2"),
            _trade(30, 3, "2"),
        ),
        quotes=(_quote(),),
    )

    assert [fill.size for fill in result.fills] == [Decimal("1"), Decimal("1")]
    assert result.fills[0].queue_ahead_before == Decimal("1")
    assert result.fills[0].fill_ts_ms == 20


def test_central_queue_fraction_allows_measured_partial_fills() -> None:
    result = ReplayEngine().run(
        config=_config(FillModelKind.CENTRAL),
        events=(
            _book(0, 0),
            _trade(10, 1, "4"),
            _trade(20, 2, "1"),
        ),
        quotes=(_quote(),),
    )

    assert [fill.size for fill in result.fills] == [Decimal("1.5"), Decimal("0.5")]
    assert result.fills[0].queue_ahead_before == Decimal("2.5")


def test_cancel_latency_keeps_quote_exposed_until_effective_timestamp() -> None:
    before_cancel = ReplayEngine().run(
        config=_config(FillModelKind.CENTRAL, cancel_latency_ms=10),
        events=(_book(0, 0, bid_size="0.1"), _trade(15, 1, "2")),
        quotes=(_quote(size="1", cancel=10),),
    )
    after_cancel = ReplayEngine().run(
        config=_config(FillModelKind.CENTRAL, cancel_latency_ms=10),
        events=(_book(0, 0, bid_size="0.1"), _trade(21, 1, "2")),
        quotes=(_quote(size="1", cancel=10),),
    )

    assert sum(fill.size for fill in before_cancel.fills) == Decimal("1")
    assert not after_cancel.fills


def test_markouts_fees_and_result_hash_are_reproducible() -> None:
    events = (
        _book(0, 0, bid_size="0.1"),
        _trade(100, 1, "2"),
        _book(200, 2, bid="101", ask="103"),
        _book(1_100, 3, bid="102", ask="104"),
        _book(5_100, 4, bid="103", ask="105"),
        _book(30_100, 5, bid="102", ask="104"),
    )
    config = _config(FillModelKind.CENTRAL)
    quote = _quote(size="1", fee_bps="1")
    first = ReplayEngine().run(config=config, events=events, quotes=(quote,))
    second = ReplayEngine().run(
        config=config,
        events=tuple(reversed(events)),
        quotes=(quote,),
    )

    assert first == second
    assert first.result_sha256 == second.result_sha256
    assert first.fills[0].markout_100ms == Decimal("2")
    assert first.fills[0].markout_1s == Decimal("3")
    assert first.fills[0].markout_5s == Decimal("4")
    assert first.fills[0].markout_30s == Decimal("3")
    assert first.fees_usd == Decimal("0.01")
    assert first.economic_pnl_30s_usd == Decimal("2.99")


def test_bbo_marks_supply_markouts_without_changing_queue_evidence() -> None:
    events = (
        _book(0, 0, bid_size="0.1"),
        _trade(100, 1, "2"),
        ReplayMark("BTC", 200, 2, Decimal("101"), Decimal("103")),
        ReplayMark("BTC", 1_100, 3, Decimal("102"), Decimal("104")),
        ReplayMark("BTC", 5_100, 4, Decimal("103"), Decimal("105")),
        ReplayMark("BTC", 30_100, 5, Decimal("102"), Decimal("104")),
    )

    result = ReplayEngine().run(
        config=_config(FillModelKind.CENTRAL),
        events=events,
        quotes=(_quote(size="1"),),
    )

    assert result.fills[0].markout_100ms == Decimal("2")
    assert result.fills[0].markout_1s == Decimal("3")
    assert result.fills[0].markout_5s == Decimal("4")
    assert result.fills[0].markout_30s == Decimal("3")


def test_receive_order_prevents_future_book_lookahead() -> None:
    initial_book = ReplayBook(
        market="BTC",
        timestamp_ms=0,
        source_sequence=0,
        bids=(BookLevel(Decimal("100"), Decimal("5")),),
        asks=(BookLevel(Decimal("101"), Decimal("5")),),
        receive_ts_ms=0,
    )
    delayed_book = ReplayBook(
        market="BTC",
        timestamp_ms=200,
        source_sequence=1,
        bids=(BookLevel(Decimal("100"), Decimal("0.1")),),
        asks=(BookLevel(Decimal("101"), Decimal("5")),),
        receive_ts_ms=1_000,
    )
    trade = ReplayTrade(
        market="BTC",
        timestamp_ms=300,
        source_sequence=2,
        aggressor_side=Side.SELL,
        price=Decimal("100"),
        size=Decimal("2"),
        receive_ts_ms=500,
    )

    result = ReplayEngine().run(
        config=_config(FillModelKind.CENTRAL),
        events=(initial_book, delayed_book, trade),
        quotes=(_quote(size="1", submitted=100),),
    )

    assert not result.fills


def test_legacy_only_allows_the_labeled_optimistic_bound() -> None:
    optimistic = ReplayEngine().run(
        config=_config(FillModelKind.OPTIMISTIC_TOUCH, tier=DatasetTier.C),
        events=(_trade(10, 0, "0.01"),),
        quotes=(_quote(size="2"),),
    )

    assert sum(fill.size for fill in optimistic.fills) == Decimal("2")
    assert optimistic.evidence_label == "legacy_research_only_optimistic_touch"
    with pytest.raises(LegacyEvidenceError):
        ReplayEngine().run(
            config=_config(FillModelKind.CENTRAL, tier=DatasetTier.C),
            events=(_book(0, 0), _trade(10, 1, "10")),
            quotes=(_quote(),),
        )


def test_latency_and_fee_stress_are_automatic() -> None:
    stress = ReplayEngine().run_stress(
        config=_config(FillModelKind.CENTRAL, placement_latency_ms=3),
        events=(
            _book(0, 0, bid_size="0.1"),
            _trade(5, 1, "2"),
            _book(30_005, 2, bid="100", ask="102"),
        ),
        quotes=(_quote(size="1"),),
    )

    assert len(stress.base.fills) == 1
    assert len(stress.double_latency.fills) == 0
    assert stress.double_fees.fees_usd == stress.base.fees_usd * 2
    assert (
        stress.double_fees.economic_pnl_30s_usd
        < stress.base.economic_pnl_30s_usd
    )


def test_queue_models_fail_closed_without_a_book_at_activation() -> None:
    with pytest.raises(ReplayDataError, match="no L2 book"):
        ReplayEngine().run(
            config=_config(FillModelKind.PESSIMISTIC),
            events=(_trade(10, 0, "10"),),
            quotes=(_quote(),),
        )
