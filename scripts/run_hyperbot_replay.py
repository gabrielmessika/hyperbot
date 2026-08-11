#!/usr/bin/env python3
"""Run a deterministic HyperBot replay from an explicit JSON fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import cast

from hyperbot.models import BookLevel, DatasetTier, Side
from hyperbot.replay import (
    FillModelKind,
    ReplayBook,
    ReplayConfig,
    ReplayEngine,
    ReplayQuote,
    ReplayTrade,
)
from hyperbot.replay.engine import ReplayMarketEvent, replay_result_payload


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-root", type=Path, default=Path("data/replay_reports"))
    parser.add_argument("--stress", action="store_true")
    return parser.parse_args()


def _level(value: object) -> BookLevel:
    if not isinstance(value, dict):
        raise ValueError("book level must be an object")
    level = cast(dict[str, object], value)
    count = level.get("order_count")
    return BookLevel(
        price=Decimal(str(level["price"])),
        size=Decimal(str(level["size"])),
        order_count=int(cast(int, count)) if count is not None else None,
    )


def _event(value: object) -> ReplayMarketEvent:
    if not isinstance(value, dict):
        raise ValueError("replay event must be an object")
    item = cast(dict[str, object], value)
    event_type = item.get("type")
    if event_type == "book":
        return ReplayBook(
            market=str(item["market"]),
            timestamp_ms=int(cast(int, item["timestamp_ms"])),
            source_sequence=int(cast(int, item["source_sequence"])),
            bids=tuple(_level(level) for level in cast(list[object], item["bids"])),
            asks=tuple(_level(level) for level in cast(list[object], item["asks"])),
        )
    if event_type == "trade":
        return ReplayTrade(
            market=str(item["market"]),
            timestamp_ms=int(cast(int, item["timestamp_ms"])),
            source_sequence=int(cast(int, item["source_sequence"])),
            aggressor_side=Side(str(item["aggressor_side"])),
            price=Decimal(str(item["price"])),
            size=Decimal(str(item["size"])),
        )
    raise ValueError(f"unsupported replay event type: {event_type!r}")


def _quote(value: object) -> ReplayQuote:
    if not isinstance(value, dict):
        raise ValueError("replay quote must be an object")
    item = cast(dict[str, object], value)
    cancel = item.get("cancel_requested_ts_ms")
    return ReplayQuote(
        quote_id=str(item["quote_id"]),
        market=str(item["market"]),
        side=Side(str(item["side"])),
        price=Decimal(str(item["price"])),
        size=Decimal(str(item["size"])),
        submitted_ts_ms=int(cast(int, item["submitted_ts_ms"])),
        cancel_requested_ts_ms=int(cast(int, cancel)) if cancel is not None else None,
        maker_fee_bps=Decimal(str(item["maker_fee_bps"])),
    )


def main() -> int:
    args = _arguments()
    raw = args.input.read_bytes()
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("replay input must be a JSON object")
    document = cast(dict[str, object], decoded)
    config = ReplayConfig(
        run_id=str(document["run_id"]),
        code_version=str(document["code_version"]),
        model=FillModelKind(str(document["model"])),
        dataset_tiers=tuple(
            DatasetTier(str(item))
            for item in cast(list[object], document["dataset_tiers"])
        ),
        placement_latency_ms=int(cast(int, document["placement_latency_ms"])),
        cancel_latency_ms=int(cast(int, document["cancel_latency_ms"])),
        central_queue_fraction=Decimal(
            str(document.get("central_queue_fraction", "0.5"))
        ),
        markout_tolerance_ms=int(
            cast(int, document.get("markout_tolerance_ms", 250))
        ),
    )
    events = tuple(_event(item) for item in cast(list[object], document["events"]))
    quotes = tuple(_quote(item) for item in cast(list[object], document["quotes"]))
    engine = ReplayEngine()
    base = engine.run(config=config, events=events, quotes=quotes)
    payload: dict[str, object] = {
        "input_file_sha256": hashlib.sha256(raw).hexdigest(),
        "base": replay_result_payload(base),
    }
    if args.stress:
        stress = engine.run_stress(config=config, events=events, quotes=quotes)
        payload["double_latency"] = replay_result_payload(stress.double_latency)
        payload["double_fees"] = replay_result_payload(stress.double_fees)
    args.output_root.mkdir(parents=True, exist_ok=True)
    output = args.output_root / f"{config.run_id}.json"
    if output.exists():
        raise FileExistsError(f"replay report already exists: {output}")
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    output.write_bytes(encoded)
    output.with_suffix(".json.sha256").write_text(
        f"{hashlib.sha256(encoded).hexdigest()}  {output.name}\n",
        encoding="ascii",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
