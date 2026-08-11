import json
from decimal import Decimal
from pathlib import Path

import pytest

from hyperbot.legacy.adapters import (
    AdaptationContext,
    LegacyAdaptationError,
    LegacyGbotAdapter,
    LegacyHip4Adapter,
    LegacyTridentSnapshotAdapter,
)
from hyperbot.models import (
    DatasetTier,
    EventContext,
    LegacyBookObservation,
    LegacyFeatureObservation,
    LegacyQuoteObservation,
    LegacySettlementObservation,
    LegacyTradeObservation,
    Side,
    TimeSource,
)

FIXTURES = Path(__file__).parent / "fixtures" / "legacy"


def _fixture(name: str) -> dict[str, object]:
    decoded = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    assert isinstance(decoded, dict)
    return decoded


def _context(path: str) -> AdaptationContext:
    return AdaptationContext(
        event_context=EventContext(
            run_id="legacy-test",
            code_version="adapter-test",
            config_hash="c" * 64,
            time_source=TimeSource.REPLAY,
        ),
        dataset_tier=DatasetTier.C,
        source_path=path,
        source_sha256="a" * 64,
        source_record_number=7,
        source_record_sha256="b" * 64,
        source_record_hash_kind="raw_line_sha256",
    )


def test_gbot_book_and_trade_preserve_available_units() -> None:
    adapter = LegacyGbotAdapter()
    book = adapter.adapt(
        _fixture("gbot_l2.json"),
        _context("/archive/l2/BTC/2026-04-01.jsonl"),
    )[0]
    trade = adapter.adapt(
        {
            "timestamp": 1_775_037_039_202,
            "coin": "BTC",
            "price": 68_600,
            "size": 0.00366,
            "is_buy": True,
        },
        _context("/archive/trades/BTC/2026-04-01.jsonl"),
    )[0]

    assert isinstance(book, LegacyBookObservation)
    assert book.bid_size is None
    assert book.bid_depth == Decimal("3043053.1")
    assert "queue_position_unknown" in book.provenance.quality_flags
    assert isinstance(trade, LegacyTradeObservation)
    assert trade.side is Side.BUY
    assert trade.base_size == Decimal("0.00366")
    assert trade.notional_usd is None


def test_trident_snapshot_maps_zero_sentinels_to_missing() -> None:
    adapter = LegacyTridentSnapshotAdapter()
    events = adapter.adapt(
        _fixture("trident_snapshot.json"),
        _context("/archive/live_snapshots/2026-06-24.jsonl"),
    )

    event = events[0]
    assert isinstance(event, LegacyFeatureObservation)
    assert event.price == Decimal("5140")
    assert event.best_bid is None
    assert event.best_ask is None
    assert event.bid_depth == Decimal("0")
    assert "zero_sentinel_mapped_to_missing" in event.provenance.quality_flags


def test_hip4_market_observation_emits_yes_and_no_books() -> None:
    adapter = LegacyHip4Adapter()
    events = adapter.adapt(
        _fixture("hip4_market_observation.json"),
        _context("/archive/market_observations.jsonl"),
    )

    assert len(events) == 2
    assert all(isinstance(event, LegacyBookObservation) for event in events)
    yes = events[0]
    assert isinstance(yes, LegacyBookObservation)
    assert yes.outcome_side == "YES"
    assert yes.best_bid == Decimal("0.4")
    assert yes.provenance.subrecord_index == 0
    no = events[1]
    assert isinstance(no, LegacyBookObservation)
    assert no.provenance.subrecord_index == 1


def test_hip4_empty_book_is_preserved_as_missing_data() -> None:
    events = LegacyHip4Adapter().adapt(
        {
            "ts": "2026-07-05T17:58:33.297711Z",
            "market_id": "BTC_GT_62731_20260706_0600",
            "support_status": "trading_supported",
            "coins": ["#7530", "#7531"],
            "side_names": ["Yes", "No"],
            "books": {},
        },
        _context("/archive/market_observations.jsonl"),
    )

    assert len(events) == 2
    first = events[0]
    assert isinstance(first, LegacyBookObservation)
    assert first.asset == "#7530"
    assert first.outcome_side == "Yes"
    assert first.best_bid is None
    assert "book_payload_absent" in first.provenance.quality_flags


def test_hip4_quote_trade_and_settlement_are_not_executable_events() -> None:
    adapter = LegacyHip4Adapter()
    quote = adapter.adapt(
        {
            "ts": "2026-05-24T15:28:06Z",
            "market_id": "BTC_GT_100",
            "underlying": "BTC",
            "side": "BUY_YES",
            "decision_approved": "True",
            "would_quote": "True",
            "bid": "0.39",
            "ask": "0.40",
            "maker_price": "0.395",
            "quote_size_usdc": "10",
            "win_probability": "0.43",
            "reference_price": "99",
            "strike": "100",
            "seconds_left": "60",
        },
        _context("/archive/shadow_maker_quotes.csv"),
    )[0]
    trade = adapter.adapt(
        {
            "ts": "2026-05-24T15:28:07Z",
            "market_id": "BTC_GT_100",
            "underlying": "BTC",
            "coin": "#1",
            "side": "BUY_YES",
            "price": "0.4",
            "size_usdc": "10",
            "token_qty": "25",
        },
        _context("/archive/trades.csv"),
    )[0]
    settlement = adapter.adapt(
        {
            "ts": "2026-05-25T06:05:45Z",
            "market_id": "BTC_GT_100",
            "underlying": "BTC",
            "side": "YES",
            "result": "win",
            "payout_usdc": "25",
            "fee_usdc": "0.1",
            "net_pnl_usdc": "14.9",
        },
        _context("/archive/settlements.csv"),
    )[0]

    assert isinstance(quote, LegacyQuoteObservation)
    assert quote.decision_approved is True
    assert quote.model_probability == Decimal("0.43")
    assert isinstance(trade, LegacyTradeObservation)
    assert trade.side is Side.BUY
    assert trade.token_size == Decimal("25")
    assert isinstance(settlement, LegacySettlementObservation)
    assert settlement.net_pnl_usd == Decimal("14.9")


@pytest.mark.parametrize(
    "record",
    [
        {
            "timestamp": "not-a-date",
            "coin": "BTC",
            "best_bid": 1,
            "best_ask": 2,
        },
        {
            "timestamp": 1_775_037_040_544,
            "coin": "BTC",
            "best_bid": -1,
            "best_ask": 2,
        },
    ],
)
def test_invalid_timestamp_and_impossible_price_are_rejected(
    record: dict[str, object],
) -> None:
    with pytest.raises(LegacyAdaptationError):
        LegacyGbotAdapter().adapt(
            record, _context("/archive/l2/BTC/2026-04-01.jsonl")
        )
