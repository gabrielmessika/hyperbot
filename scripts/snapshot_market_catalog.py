#!/usr/bin/env python3
"""Capture one credential-free public market catalog snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid
from pathlib import Path

from hyperbot.market_catalog import (
    CATALOG_VERSION,
    MarketCatalogClient,
    UrllibPublicInfoTransport,
)
from hyperbot.models import EventContext, TimeSource, event_payload
from hyperbot.segmented_store import SegmentedEventStore


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("data/raw/catalog"))
    parser.add_argument("--report-root", type=Path, default=Path("reports/m2"))
    parser.add_argument("--timeout-seconds", type=float, default=15.0)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    run_id = f"catalog-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    observed_at_ms = int(time.time() * 1000)
    config = {
        "catalog_version": CATALOG_VERSION,
        "endpoint": "https://api.hyperliquid.xyz/info",
        "public_only": True,
    }
    config_hash = hashlib.sha256(
        json.dumps(config, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    context = EventContext(run_id, "0.1.0", config_hash, TimeSource.EXCHANGE)
    client = MarketCatalogClient(
        UrllibPublicInfoTransport(timeout_seconds=args.timeout_seconds)
    )
    snapshot = client.snapshot(context, observed_at_ms=observed_at_ms)

    store = SegmentedEventStore(args.data_root)
    for event in snapshot.change_events:
        store.append("market-catalog", event)
    store.close("market-catalog")
    validation = store.validate("market-catalog")

    args.report_root.mkdir(parents=True, exist_ok=True)
    report_json = args.report_root / f"{run_id}.json"
    report_markdown = args.report_root / f"{run_id}.md"
    payload = {
        "run_id": run_id,
        "observed_at_ms": observed_at_ms,
        "config": config,
        "config_sha256": config_hash,
        "public_endpoints_only": True,
        "definition_count": len(snapshot.definitions),
        "change_event_count": len(snapshot.change_events),
        "issues": [
            {"request_type": issue.request_type, "detail": issue.detail}
            for issue in snapshot.issues
        ],
        "definitions": [event_payload(item) for item in snapshot.definitions],
        "store_validation": {
            "record_count": validation.record_count,
            "segment_count": validation.segment_count,
            "last_record_sha256": validation.last_record_sha256,
        },
    }
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    report_json.write_text(encoded, encoding="utf-8")
    checksum = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
    report_json.with_suffix(".json.sha256").write_text(
        f"{checksum}  {report_json.name}\n",
        encoding="ascii",
    )
    kinds: dict[str, int] = {}
    for definition in snapshot.definitions:
        kinds[definition.market_kind.value] = kinds.get(
            definition.market_kind.value, 0
        ) + 1
    lines = [
        f"# Snapshot catalogue public M2 — {run_id}",
        "",
        "- accès : endpoint public `info`, sans clé ni signature ;",
        f"- définitions : {len(snapshot.definitions)} ;",
        f"- événements de révision : {len(snapshot.change_events)} ;",
        f"- problèmes non fatals : {len(snapshot.issues)} ;",
        f"- records validés dans le store : {validation.record_count} ;",
        f"- SHA-256 du rapport JSON : `{checksum}`.",
        "",
        "## Répartition",
        "",
    ]
    lines.extend(f"- `{kind}` : {count}" for kind, count in sorted(kinds.items()))
    if snapshot.issues:
        lines.extend(("", "## Problèmes tolérés", ""))
        lines.extend(
            f"- `{issue.request_type}` : {issue.detail}" for issue in snapshot.issues
        )
    report_markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(report_json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
