#!/usr/bin/env python3
"""Build a secret-free manifest for immutable HyperBot server data."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from hyperbot.ops_export import build_export_bundle, completed_utc_dates


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--days", type=int, default=3)
    parser.add_argument("--date", action="append", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--output-root", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _arguments()
    now = datetime.now(tz=UTC)
    values = tuple(args.date) or completed_utc_dates(today=now.date(), days=args.days)
    bundle = build_export_bundle(
        args.root,
        dates=values,
        include_all=args.all,
        generated_at=now,
        output_root=args.output_root,
    )
    print(f"HYPERBOT_EXPORT_DIR={bundle.directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
