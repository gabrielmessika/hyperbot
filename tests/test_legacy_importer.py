import json
from pathlib import Path

import pytest

from hyperbot.legacy.importer import (
    LegacyImportError,
    import_legacy_data,
    import_report_json,
    write_import_report,
)
from hyperbot.legacy.manifest import SourceSpec, build_inventory, manifest_json
from hyperbot.models import DatasetTier


def _write_inventory(source_root: Path, manifest_path: Path) -> None:
    inventory = build_inventory(
        (
            SourceSpec(
                name="gbot_microstructure",
                root=source_root,
                tier=DatasetTier.C,
                patterns=("l2/*/*.jsonl",),
                known="fixture",
                approximated="fixture",
                absent="queue",
            ),
        )
    )
    manifest_path.write_text(manifest_json(inventory), encoding="utf-8")


def test_import_is_deterministic_and_routes_corrupt_records(tmp_path: Path) -> None:
    source_root = tmp_path / "gbot"
    source_file = source_root / "l2" / "BTC" / "events.jsonl"
    source_file.parent.mkdir(parents=True)
    source_file.write_text(
        '{"timestamp":1775037040544,"coin":"BTC","best_bid":68610,'
        '"best_ask":68611,"bid_depth_10bps":10,"ask_depth_10bps":11}\n'
        "{broken\n",
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    _write_inventory(source_root, manifest_path)

    first = import_legacy_data(manifest_path, tmp_path / "derived")
    second = import_legacy_data(manifest_path, tmp_path / "derived")
    file_report = first.files[0]

    assert import_report_json(first) == import_report_json(second)
    assert first.summary.accepted_records == 1
    assert first.summary.rejected_records == 1
    assert first.summary.emitted_events == 1
    output_record = json.loads(
        Path(file_report.output_path).read_text(encoding="utf-8")
    )
    provenance = output_record["payload"]["provenance"]
    assert output_record["event_type"] == "LegacyBookObservation"
    assert provenance["dataset_tier"] == "C"
    assert provenance["legacy_research_only"] is True
    assert provenance["source_record_number"] == 1
    assert provenance["source_record_hash_kind"] == "raw_line_sha256"
    error_record = json.loads(
        Path(file_report.error_path).read_text(encoding="utf-8")
    )
    assert error_record["error_code"] == "corrupt_json"
    assert error_record["source_record_number"] == 2


def test_source_change_after_inventory_fails_closed(tmp_path: Path) -> None:
    source_root = tmp_path / "gbot"
    source_file = source_root / "l2" / "BTC" / "events.jsonl"
    source_file.parent.mkdir(parents=True)
    source_file.write_text(
        '{"timestamp":1775037040544,"coin":"BTC","best_bid":1,'
        '"best_ask":2}\n',
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    _write_inventory(source_root, manifest_path)
    source_file.write_text(source_file.read_text(encoding="utf-8") + "\n")

    with pytest.raises(LegacyImportError, match="source stat differs"):
        import_legacy_data(manifest_path, tmp_path / "derived")


def test_report_contains_coverage_policy_and_checksums(tmp_path: Path) -> None:
    source_root = tmp_path / "gbot"
    source_file = source_root / "l2" / "BTC" / "events.jsonl"
    source_file.parent.mkdir(parents=True)
    source_file.write_text(
        '{"timestamp":1775037040544,"coin":"BTC","best_bid":1,'
        '"best_ask":2}\n',
        encoding="utf-8",
    )
    manifest_path = tmp_path / "manifest.json"
    _write_inventory(source_root, manifest_path)
    report = import_legacy_data(manifest_path, tmp_path / "derived")

    report_path, coverage_path, checksum_path = write_import_report(
        report, tmp_path / "report"
    )

    assert report_path.exists()
    coverage = coverage_path.read_text(encoding="utf-8")
    assert "optimistic_touch" in coverage
    assert "central_fill_model | non" in coverage
    assert "legacy_research_only" in coverage
    assert "report.json" in checksum_path.read_text(encoding="utf-8")
