#!/usr/bin/env python3
"""Run a deterministic HyperBot replay from an explicit JSON fixture."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import cast

from hyperbot.build_info import exact_code_version
from hyperbot.models import BookLevel, DatasetTier, Side
from hyperbot.replay import (
    CollectorReplayDataset,
    FillModelKind,
    ReplayBook,
    ReplayConfig,
    ReplayEngine,
    ReplayQuote,
    ReplayTrade,
    generate_top_of_book_probes,
    read_collector_replay_dataset,
)
from hyperbot.replay.engine import ReplayMarketEvent, replay_result_payload


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output-root", type=Path, default=Path("data/replay_reports"))
    parser.add_argument("--stress", action="store_true")
    parser.add_argument("--model", choices=tuple(FillModelKind))
    parser.add_argument("--placement-latency-ms", type=int)
    parser.add_argument("--cancel-latency-ms", type=int)
    parser.add_argument(
        "--central-queue-fraction", type=Decimal, default=Decimal("0.5")
    )
    parser.add_argument("--markout-tolerance-ms", type=int, default=250)
    parser.add_argument("--maker-fee-bps", type=Decimal)
    parser.add_argument("--probe-interval-ms", type=int, default=300_000)
    parser.add_argument("--probe-ttl-ms", type=int, default=1_000)
    parser.add_argument("--probe-notional-usd", type=Decimal, default=Decimal("10"))
    parser.add_argument("--probe-max-book-age-ms", type=int, default=500)
    parser.add_argument("--run-id")
    parser.add_argument("--code-version")
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
            receive_ts_ms=(
                int(cast(int, item["receive_ts_ms"]))
                if item.get("receive_ts_ms") is not None
                else None
            ),
        )
    if event_type == "trade":
        return ReplayTrade(
            market=str(item["market"]),
            timestamp_ms=int(cast(int, item["timestamp_ms"])),
            source_sequence=int(cast(int, item["source_sequence"])),
            aggressor_side=Side(str(item["aggressor_side"])),
            price=Decimal(str(item["price"])),
            size=Decimal(str(item["size"])),
            receive_ts_ms=(
                int(cast(int, item["receive_ts_ms"]))
                if item.get("receive_ts_ms") is not None
                else None
            ),
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


def _fixture_document(raw: bytes) -> dict[str, object]:
    decoded = json.loads(raw)
    if not isinstance(decoded, dict):
        raise ValueError("replay input must be a JSON object")
    return cast(dict[str, object], decoded)


def _fixture_inputs(
    document: dict[str, object],
) -> tuple[ReplayConfig, tuple[ReplayMarketEvent, ...], tuple[ReplayQuote, ...]]:
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
        markout_tolerance_ms=int(cast(int, document.get("markout_tolerance_ms", 250))),
    )
    events = tuple(_event(item) for item in cast(list[object], document["events"]))
    quotes = tuple(_quote(item) for item in cast(list[object], document["quotes"]))
    return config, events, quotes


def _looks_like_collector_dataset(path: Path) -> bool:
    with path.open("rb") as handle:
        prefix = handle.read(4_096)
    return b'"kind": "hyperbot_collector_replay_dataset"' in prefix


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _required_dataset_argument(value: object, name: str) -> object:
    if value is None:
        raise ValueError(f"{name} is required for a collector replay dataset")
    return value


def _collector_inputs(
    args: argparse.Namespace,
    dataset: CollectorReplayDataset,
) -> tuple[
    ReplayConfig,
    tuple[ReplayMarketEvent, ...],
    tuple[ReplayQuote, ...],
    dict[str, object],
]:
    model = FillModelKind(str(_required_dataset_argument(args.model, "--model")))
    placement_latency_ms = int(
        cast(
            int,
            _required_dataset_argument(
                args.placement_latency_ms,
                "--placement-latency-ms",
            ),
        )
    )
    cancel_latency_ms = int(
        cast(
            int,
            _required_dataset_argument(
                args.cancel_latency_ms,
                "--cancel-latency-ms",
            ),
        )
    )
    maker_fee_bps = cast(
        Decimal,
        _required_dataset_argument(args.maker_fee_bps, "--maker-fee-bps"),
    )
    repository_root = Path(__file__).resolve().parents[1]
    replay_code_version = args.code_version or exact_code_version(
        os.environ,
        repository_root=repository_root,
    )
    quote_plan = {
        "kind": "top_of_book_execution_probe_v1",
        "interval_ms": args.probe_interval_ms,
        "ttl_ms": args.probe_ttl_ms,
        "notional_usd": str(args.probe_notional_usd),
        "maker_fee_bps": str(maker_fee_bps),
        "maximum_book_age_ms": args.probe_max_book_age_ms,
    }
    assumptions = {
        "dataset_sha256": dataset.dataset_sha256,
        "model": model.value,
        "placement_latency_ms": placement_latency_ms,
        "cancel_latency_ms": cancel_latency_ms,
        "central_queue_fraction": str(args.central_queue_fraction),
        "markout_tolerance_ms": args.markout_tolerance_ms,
        "quote_plan": quote_plan,
        "replay_code_version": replay_code_version,
    }
    assumptions_hash = hashlib.sha256(
        json.dumps(assumptions, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    run_id = args.run_id or (
        f"{dataset.dataset_id}-{model.value}-{assumptions_hash[:12]}"
    )
    config = ReplayConfig(
        run_id=run_id,
        code_version=replay_code_version,
        model=model,
        dataset_tiers=(DatasetTier.A,),
        placement_latency_ms=placement_latency_ms,
        cancel_latency_ms=cancel_latency_ms,
        central_queue_fraction=args.central_queue_fraction,
        markout_tolerance_ms=args.markout_tolerance_ms,
    )
    quotes = generate_top_of_book_probes(
        dataset,
        interval_ms=args.probe_interval_ms,
        ttl_ms=args.probe_ttl_ms,
        notional_usd=args.probe_notional_usd,
        maker_fee_bps=maker_fee_bps,
        maximum_book_age_ms=args.probe_max_book_age_ms,
    )
    return (
        config,
        dataset.events,
        quotes,
        {
            "dataset_id": dataset.dataset_id,
            "dataset_sha256": dataset.dataset_sha256,
            "quality_report_sha256": dataset.quality_report_sha256,
            "source_manifest_sha256": dataset.source_manifest_sha256,
            "source_config_sha256": dataset.source_config_sha256,
            "source_code_version": dataset.source_code_version,
            "builder_code_version": dataset.builder_code_version,
            "assumptions_sha256": assumptions_hash,
            "quote_plan": quote_plan,
            "quote_count": len(quotes),
        },
    )


def main() -> int:
    args = _arguments()
    collector_dataset = _looks_like_collector_dataset(args.input)
    provenance: dict[str, object] | None = None
    if collector_dataset:
        dataset = read_collector_replay_dataset(args.input)
        config, events, quotes, provenance = _collector_inputs(args, dataset)
        input_sha256 = _file_sha256(args.input)
    else:
        raw = args.input.read_bytes()
        document = _fixture_document(raw)
        config, events, quotes = _fixture_inputs(document)
        input_sha256 = hashlib.sha256(raw).hexdigest()
    engine = ReplayEngine()
    base = engine.run(config=config, events=events, quotes=quotes)
    payload: dict[str, object] = {
        "input_file_sha256": input_sha256,
        "base": replay_result_payload(base),
    }
    if provenance is not None:
        payload["collector_dataset"] = provenance
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
