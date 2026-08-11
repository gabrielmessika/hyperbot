#!/usr/bin/env python3
"""Verify and normalize every file from the M1L.1 inventory."""

from __future__ import annotations

import argparse
from pathlib import Path

from hyperbot.legacy.importer import import_legacy_data, write_import_report


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("reports/legacy_inventory/manifest.json"),
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("data/legacy_imports"),
        help="ignored derived-data destination",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=Path("reports/legacy_import"),
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
    trident_root = Path("/workspaces/trident").resolve()
    data_dir = args.data_dir.resolve()
    report_dir = args.report_dir.resolve()
    if _is_within(data_dir, trident_root) or _is_within(report_dir, trident_root):
        raise SystemExit("import outputs must remain outside the TRIDENT tree")
    report = import_legacy_data(args.manifest, data_dir)
    paths = write_import_report(report, report_dir)
    for path in paths:
        print(path)
    print(f"derived_data={data_dir / report.run_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
