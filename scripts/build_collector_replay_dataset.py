#!/usr/bin/env python3
"""Build one checksummed M4 dataset from a qualified tier-A collector day."""

from __future__ import annotations

import argparse
import os
from datetime import date
from pathlib import Path

from hyperbot.build_info import exact_code_version
from hyperbot.replay.collector import (
    build_collector_replay_dataset,
    write_collector_replay_dataset,
)
from hyperbot.segmented_store import SegmentedEventStore


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--market", required=True)
    parser.add_argument("--quality-report", type=Path, required=True)
    parser.add_argument(
        "--data-root",
        type=Path,
        default=Path("data/raw/collector"),
    )
    parser.add_argument("--archive-root", type=Path)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("data/replay_datasets"),
    )
    parser.add_argument("--code-version")
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    repository_root = Path(__file__).resolve().parents[1]
    builder_version = args.code_version or exact_code_version(
        os.environ,
        repository_root=repository_root,
    )
    store = SegmentedEventStore(
        args.data_root,
        archive_root=args.archive_root,
        fsync=False,
    )
    dataset = build_collector_replay_dataset(
        store,
        quality_report=args.quality_report,
        report_date=args.date,
        market=args.market,
        builder_code_version=builder_version,
    )
    path = write_collector_replay_dataset(dataset, args.output_root)
    print(f"dataset={path}")
    print(f"dataset_sha256={dataset.dataset_sha256}")
    print(f"books={dataset.book_count}")
    print(f"trades={dataset.trade_count}")
    print(f"maximum_book_gap_ms={dataset.maximum_book_gap_ms}")
    print(f"maximum_receive_latency_ms={dataset.maximum_receive_latency_ms}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
