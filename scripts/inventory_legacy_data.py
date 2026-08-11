#!/usr/bin/env python3
"""Build the deterministic M1L.1 inventory without modifying legacy data."""

from __future__ import annotations

import argparse
from pathlib import Path

from hyperbot.legacy.manifest import (
    build_inventory,
    default_source_specs,
    write_inventory,
)


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trident-root",
        type=Path,
        default=Path("/workspaces/trident"),
        help="read-only root containing the allow-listed legacy archives",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("reports/legacy_inventory"),
        help="destination outside the TRIDENT source tree",
    )
    return parser.parse_args()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def main() -> int:
    args = _arguments()
    source_root = args.trident_root.resolve()
    output_dir = args.output_dir.resolve()
    if _is_within(output_dir, source_root):
        raise SystemExit("output directory must be outside the TRIDENT source tree")

    manifest = build_inventory(default_source_specs(source_root))
    paths = write_inventory(manifest, output_dir)
    for path in paths:
        print(path)
    return 2 if manifest.has_fatal_issues else 0


if __name__ == "__main__":
    raise SystemExit(main())
