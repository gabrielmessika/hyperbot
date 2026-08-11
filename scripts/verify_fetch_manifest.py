#!/usr/bin/env python3
"""Verify files fetched from a HyperBot export manifest."""

from __future__ import annotations

import argparse
from pathlib import Path

from hyperbot.ops_export import verify_export_manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("data_root", type=Path)
    args = parser.parse_args()
    verified = verify_export_manifest(args.manifest, args.data_root)
    print(f"verified_files={len(verified)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
