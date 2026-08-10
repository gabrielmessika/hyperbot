import json
from decimal import Decimal
from pathlib import Path

import pytest

from hyperbot.event_store import EventIntegrityError, EventStoreError, JsonlEventStore
from hyperbot.models import BookEvent, BookLevel, EventContext, TimeSource


def _event(sequence: int = 1) -> BookEvent:
    return BookEvent(
        context=EventContext(
            run_id="run-store",
            code_version="deadbeef",
            config_hash="b" * 64,
            time_source=TimeSource.EXCHANGE,
        ),
        exchange_ts_ms=1_000,
        receive_ts_ms=1_001,
        dex="core",
        asset="BTC",
        sequence=sequence,
        bids=(BookLevel(Decimal("99"), Decimal("2")),),
        asks=(BookLevel(Decimal("101"), Decimal("3")),),
        oracle_px=Decimal("100"),
        mark_px=Decimal("100.1"),
    )


def test_append_is_ordered_and_integrity_checked(tmp_path: Path) -> None:
    store = JsonlEventStore(tmp_path, fsync=False)

    first = store.append("books.core.btc", _event(1))
    second = store.append("books.core.btc", _event(2))
    records = store.read_records("books.core.btc")

    assert first.byte_offset == 0
    assert second.byte_offset == first.bytes_written
    assert len(records) == 2
    assert records[0]["event_type"] == "BookEvent"
    first_payload = records[0]["payload"]
    assert isinstance(first_payload, dict)
    assert first_payload["sequence"] == 1


def test_tampered_payload_is_rejected(tmp_path: Path) -> None:
    store = JsonlEventStore(tmp_path, fsync=False)
    result = store.append("books.core.btc", _event())
    record = json.loads(result.path.read_text(encoding="utf-8"))
    record["payload"]["asset"] = "ETH"
    result.path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(EventIntegrityError, match="hash mismatch"):
        store.read_records("books.core.btc")


def test_stream_name_cannot_escape_store_root(tmp_path: Path) -> None:
    store = JsonlEventStore(tmp_path)

    with pytest.raises(EventStoreError, match="invalid stream"):
        store.append("../outside", _event())
