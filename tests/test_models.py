from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from hyperbot.models import (
    BookEvent,
    BookLevel,
    EventContext,
    Side,
    TimeSource,
    event_payload,
)


def _context() -> EventContext:
    return EventContext(
        run_id="run-001",
        code_version="deadbeef",
        config_hash="a" * 64,
        time_source=TimeSource.EXCHANGE,
    )


def test_book_event_is_immutable_and_json_safe() -> None:
    event = BookEvent(
        context=_context(),
        exchange_ts_ms=1_000,
        receive_ts_ms=1_005,
        dex="core",
        asset="BTC",
        sequence=42,
        bids=(BookLevel(Decimal("59999.5"), Decimal("0.25"), 2),),
        asks=(BookLevel(Decimal("60000.0"), Decimal("0.50"), 3),),
        oracle_px=Decimal("59999.8"),
        mark_px=Decimal("59999.9"),
    )

    payload = event_payload(event)

    assert payload["oracle_px"] == "59999.8"
    assert payload["context"]["time_source"] == "exchange"  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        event.asset = "ETH"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("price", "size"),
    [(Decimal("0"), Decimal("1")), (Decimal("1"), Decimal("-1"))],
)
def test_book_level_rejects_non_positive_values(
    price: Decimal, size: Decimal
) -> None:
    with pytest.raises(ValueError):
        BookLevel(price=price, size=size)


def test_enum_values_are_stable() -> None:
    assert Side.BUY.value == "buy"
