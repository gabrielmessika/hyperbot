#!/usr/bin/env python3
"""Materialize verified collector manifest snapshots in a fetched payload."""

from __future__ import annotations

import argparse
from pathlib import Path

from hyperbot.ops_export import materialize_export_store_manifests


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("data_root", type=Path)
    args = parser.parse_args()
    paths = materialize_export_store_manifests(args.manifest, args.data_root)
    print(f"materialized_store_manifests={len(paths)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
