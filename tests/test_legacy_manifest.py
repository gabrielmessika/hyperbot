import hashlib
import json
from pathlib import Path

from hyperbot.legacy.manifest import (
    DatasetTier,
    SourceSpec,
    build_inventory,
    manifest_json,
    write_inventory,
)


def _source(
    root: Path, *, repeated: bool = False, patterns: tuple[str, ...] = ("*.jsonl",)
) -> SourceSpec:
    return SourceSpec(
        name="fixture",
        root=root,
        tier=DatasetTier.C,
        patterns=patterns,
        known="fixture fields",
        approximated="fixture cadence",
        absent="maker fills",
        detect_repeated_captures=repeated,
    )


def test_jsonl_inventory_is_deterministic_and_reports_gaps(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "events.jsonl"
    source.write_text(
        '\n'.join(
            (
                '{"timestamp":"2026-01-01T00:00:00Z","price":1}',
                '{"timestamp":"2026-01-01T00:00:01Z","price":2}',
                '{"timestamp":"2026-01-01T00:00:02Z","price":3,"size":4}',
                '{"timestamp":"2026-01-01T00:00:10Z","price":4}',
                '{broken',
            )
        )
        + "\n",
        encoding="utf-8",
    )

    first = build_inventory((_source(source_root),))
    second = build_inventory((_source(source_root),))
    item = first.files[0]

    assert manifest_json(first) == manifest_json(second)
    assert item.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert item.line_count == 5
    assert item.record_count == 4
    assert item.malformed_record_count == 1
    assert item.top_level_fields == ("price", "size", "timestamp")
    assert item.schema_variant_count == 2
    assert item.first_timestamp_utc == "2026-01-01T00:00:00.000000Z"
    assert item.last_timestamp_utc == "2026-01-01T00:00:10.000000Z"
    assert item.cadence.median_interval_ms == 1000.0
    assert item.cadence.inferred_gap_count == 1
    assert item.cadence.largest_gap_ms == 8000.0
    assert "legacy_research_only" in item.quality_flags
    assert "malformed_records" in item.quality_flags


def test_csv_schema_and_numeric_millisecond_timestamps(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    source = source_root / "trades.csv"
    source.write_text(
        "timestamp,price,size\n1775037039202,68600,0.1\n"
        "1775037040202,68601,0.2\n",
        encoding="utf-8",
    )

    manifest = build_inventory((_source(source_root, patterns=("*.csv",)),))
    item = manifest.files[0]

    assert item.file_format == "csv"
    assert item.line_count == 3
    assert item.record_count == 2
    assert item.top_level_fields == ("price", "size", "timestamp")
    assert item.first_timestamp_utc == "2026-04-01T09:50:39.202000Z"
    assert item.cadence.median_interval_ms == 1000.0


def test_duplicates_repeated_captures_and_symlinks_are_explicit(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "source"
    archive = source_root / "archive"
    archive.mkdir(parents=True)
    payload = '{"ts":"2026-01-01T00:00:00Z"}\n'
    (source_root / "books.jsonl").write_text(payload, encoding="utf-8")
    (archive / "books.jsonl").write_text(payload, encoding="utf-8")
    (source_root / "linked.jsonl").symlink_to(source_root / "books.jsonl")

    manifest = build_inventory(
        (_source(source_root, repeated=True, patterns=("**/*.jsonl", "*.jsonl")),)
    )

    assert manifest.summary.file_count == 2
    assert manifest.summary.exact_duplicate_group_count == 1
    assert manifest.summary.repeated_capture_group_count == 1
    assert all(item.duplicate_paths for item in manifest.files)
    assert all(item.repeated_capture_candidates for item in manifest.files)
    assert any(issue.kind == "symlink_not_followed" for issue in manifest.issues)
    assert manifest.has_fatal_issues is False


def test_missing_source_is_a_fatal_issue(tmp_path: Path) -> None:
    manifest = build_inventory((_source(tmp_path / "missing"),))

    assert manifest.has_fatal_issues is True
    assert manifest.summary.fatal_issue_count == 1
    assert manifest.issues[0].kind == "missing_source"


def test_existing_source_without_matches_is_a_fatal_issue(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()

    manifest = build_inventory((_source(source_root),))

    assert manifest.has_fatal_issues is True
    assert manifest.issues[0].kind == "no_files_matched"


def test_written_manifests_have_independent_checksums(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "events.jsonl").write_text(
        '{"ts":"2026-01-01T00:00:00Z"}\n', encoding="utf-8"
    )
    manifest = build_inventory((_source(source_root),))

    json_path, markdown_path, checksums_path = write_inventory(
        manifest, tmp_path / "output"
    )
    checksum_lines = checksums_path.read_text(encoding="utf-8").splitlines()

    assert json.loads(json_path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert "Données de recherche uniquement" in markdown_path.read_text(
        encoding="utf-8"
    )
    assert checksum_lines == [
        f"{hashlib.sha256(json_path.read_bytes()).hexdigest()}  manifest.json",
        f"{hashlib.sha256(markdown_path.read_bytes()).hexdigest()}  summary.md",
    ]
