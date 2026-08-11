"""Deterministic, read-only inventory of legacy research datasets."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import statistics
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, cast

from hyperbot.models import DatasetTier

MANIFEST_SCHEMA_VERSION = 1
SCANNER_VERSION = "1.0.0"
GAP_MULTIPLIER = 3
_TIMESTAMP_KEYS = (
    "ts_event",
    "ts",
    "timestamp",
    "time_ms",
    "receive_ts_ms",
    "exchange_ts_ms",
    "created_at",
    "updated_at",
    "datetime",
    "date",
)
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class SourceSpec:
    """An allow-listed legacy source scanned without following symlinks."""

    name: str
    root: Path
    tier: DatasetTier
    patterns: tuple[str, ...]
    known: str
    approximated: str
    absent: str
    detect_repeated_captures: bool = False


@dataclass(frozen=True, slots=True)
class SourceDescription:
    name: str
    root: str
    tier: str
    patterns: tuple[str, ...]
    known: str
    approximated: str
    absent: str


@dataclass(frozen=True, slots=True)
class CadenceSummary:
    method: str
    positive_interval_count: int
    median_interval_ms: float | None
    gap_threshold_ms: float | None
    inferred_gap_count: int
    largest_gap_ms: float | None
    out_of_order_count: int


@dataclass(frozen=True, slots=True)
class FileInventory:
    source_name: str
    dataset_tier: str
    source_path: str
    file_format: str
    size_bytes: int
    mtime_ns: int
    sha256: str
    line_count: int
    record_count: int
    blank_line_count: int
    malformed_record_count: int
    invalid_timestamp_count: int
    top_level_fields: tuple[str, ...]
    schema_variant_count: int
    timestamp_fields: tuple[str, ...]
    first_timestamp_utc: str | None
    last_timestamp_utc: str | None
    cadence: CadenceSummary
    quality_flags: tuple[str, ...]
    duplicate_paths: tuple[str, ...] = ()
    repeated_capture_candidates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InventoryIssue:
    kind: str
    source_name: str
    source_path: str
    message: str
    fatal: bool


@dataclass(frozen=True, slots=True)
class InventorySummary:
    file_count: int
    total_size_bytes: int
    total_line_count: int
    total_record_count: int
    malformed_record_count: int
    exact_duplicate_group_count: int
    repeated_capture_group_count: int
    fatal_issue_count: int
    warning_issue_count: int


@dataclass(frozen=True, slots=True)
class InventoryManifest:
    schema_version: int
    scanner_version: str
    reproducibility_note: str
    gap_policy: str
    sources: tuple[SourceDescription, ...]
    summary: InventorySummary
    files: tuple[FileInventory, ...]
    issues: tuple[InventoryIssue, ...]

    @property
    def has_fatal_issues(self) -> bool:
        return any(issue.fatal for issue in self.issues)


@dataclass(slots=True)
class _RecordStats:
    record_count: int
    malformed_record_count: int
    invalid_timestamp_count: int
    fields: set[str]
    schema_variants: set[tuple[str, ...]]
    timestamp_fields: set[str]
    first_timestamp_us: int | None
    last_timestamp_us: int | None
    previous_timestamp_us: int | None
    positive_intervals_us: list[int]
    out_of_order_count: int

    @classmethod
    def empty(cls) -> _RecordStats:
        return cls(0, 0, 0, set(), set(), set(), None, None, None, [], 0)

    def add_record(self, record: dict[str, Any]) -> None:
        self.record_count += 1
        keys = tuple(sorted(str(key) for key in record))
        self.fields.update(keys)
        self.schema_variants.add(keys)

        timestamp = _record_timestamp(record)
        if timestamp is None:
            if any(key in record for key in _TIMESTAMP_KEYS):
                self.invalid_timestamp_count += 1
            return
        field, timestamp_us = timestamp
        self.timestamp_fields.add(field)
        if self.first_timestamp_us is None or timestamp_us < self.first_timestamp_us:
            self.first_timestamp_us = timestamp_us
        if self.last_timestamp_us is None or timestamp_us > self.last_timestamp_us:
            self.last_timestamp_us = timestamp_us
        if self.previous_timestamp_us is not None:
            delta = timestamp_us - self.previous_timestamp_us
            if delta > 0:
                self.positive_intervals_us.append(delta)
            elif delta < 0:
                self.out_of_order_count += 1
        self.previous_timestamp_us = timestamp_us


def default_source_specs(trident_root: str | Path) -> tuple[SourceSpec, ...]:
    """Return the explicitly allow-listed M1L.1 inventory sources."""

    root = Path(trident_root).resolve()
    hip4_logs = root / "server-data" / "hip4" / "logs"
    return (
        SourceSpec(
            name="hip4_nautilus_books",
            root=hip4_logs / "hip4_nautilus_shadow",
            tier=DatasetTier.B,
            patterns=("book_snapshots.jsonl", "**/book_snapshots.jsonl"),
            known="BBO, profondeur agrégée, marché et timestamps publiés",
            approximated="cadence et trous temporels inférés",
            absent="diffs L2 complets, volume devant une quote et position de file",
            detect_repeated_captures=True,
        ),
        SourceSpec(
            name="hip4_paper",
            root=hip4_logs / "hip4_outcome_mainnet_paper",
            tier=DatasetTier.B,
            patterns=(
                "market_observations.jsonl",
                "shadow_maker_quotes.csv",
                "trades.csv",
                "settlements.csv",
            ),
            known="observations, quotes shadow, trades paper et settlements",
            approximated="markouts et reproduction des décisions historiques",
            absent="ACK/fills maker réels et position de file vérifiable",
        ),
        SourceSpec(
            name="gbot_microstructure",
            root=root / "data" / "gbot_archive",
            tier=DatasetTier.C,
            patterns=("l2/*/*.jsonl", "trades/*/*.jsonl"),
            known="BBO, profondeur agrégée et trades du 1er avril",
            approximated="activité et markouts sur une fenêtre courte",
            absent="carnet L2 complet, continuité longue et fills maker",
        ),
        SourceSpec(
            name="trident_replay_sample",
            root=root / "server-data" / "replay_inputs",
            tier=DatasetTier.C,
            patterns=("special_symbols_hl_15m_30d_20260419.jsonl",),
            known="features agrégées et snapshots directionnels historiques",
            approximated="régimes et compatibilité de schéma",
            absent="microstructure de file et preuve d'exécution maker",
        ),
        SourceSpec(
            name="trident_live_snapshots",
            root=root / "data" / "live_snapshots",
            tier=DatasetTier.C,
            patterns=("*.jsonl",),
            known="petits snapshots dispersés utilisables comme fixtures",
            approximated="compatibilité de schéma uniquement",
            absent="continuité, complétude et preuve d'exécution",
        ),
    )


def _source_description(spec: SourceSpec) -> SourceDescription:
    return SourceDescription(
        name=spec.name,
        root=str(spec.root.absolute()),
        tier=spec.tier.value,
        patterns=spec.patterns,
        known=spec.known,
        approximated=spec.approximated,
        absent=spec.absent,
    )


def _matches(relative_path: Path, patterns: tuple[str, ...]) -> bool:
    return any(relative_path.match(pattern) for pattern in patterns)


def _discover_files(
    spec: SourceSpec,
) -> tuple[list[Path], list[InventoryIssue]]:
    root = spec.root.absolute()
    issues: list[InventoryIssue] = []
    if not root.exists():
        return [], [
            InventoryIssue(
                kind="missing_source",
                source_name=spec.name,
                source_path=str(root),
                message="configured source does not exist",
                fatal=True,
            )
        ]
    if root.is_symlink():
        return [], [
            InventoryIssue(
                kind="symlink_not_followed",
                source_name=spec.name,
                source_path=str(root),
                message="configured source root is a symlink and was not followed",
                fatal=True,
            )
        ]
    if root.is_file():
        return ([root] if _matches(Path(root.name), spec.patterns) else []), issues

    discovered: list[Path] = []
    for directory, directory_names, file_names in os.walk(root, followlinks=False):
        directory_path = Path(directory)
        for name in tuple(directory_names):
            candidate = directory_path / name
            if candidate.is_symlink():
                directory_names.remove(name)
                issues.append(
                    InventoryIssue(
                        kind="symlink_not_followed",
                        source_name=spec.name,
                        source_path=str(candidate),
                        message="symlinked directory was not followed",
                        fatal=False,
                    )
                )
        for name in file_names:
            candidate = directory_path / name
            relative = candidate.relative_to(root)
            matches = _matches(relative, spec.patterns)
            if candidate.is_symlink() and matches:
                issues.append(
                    InventoryIssue(
                        kind="symlink_not_followed",
                        source_name=spec.name,
                        source_path=str(candidate),
                        message="symlinked file was not read",
                        fatal=False,
                    )
                )
            elif matches:
                discovered.append(candidate)
    return sorted(discovered, key=lambda path: str(path)), issues


def _parse_timestamp_us(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int | float | Decimal):
        numeric = Decimal(str(value))
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            numeric = Decimal(stripped)
        except InvalidOperation:
            iso_value = stripped[:-1] + "+00:00" if stripped.endswith("Z") else stripped
            try:
                parsed = datetime.fromisoformat(iso_value)
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            delta = parsed.astimezone(UTC) - _EPOCH
            return (
                (delta.days * 86_400 + delta.seconds) * 1_000_000
                + delta.microseconds
            )
    else:
        return None

    if not numeric.is_finite():
        return None
    absolute = abs(numeric)
    if absolute >= Decimal("1e17"):
        seconds = numeric / Decimal("1e9")
    elif absolute >= Decimal("1e14"):
        seconds = numeric / Decimal("1e6")
    elif absolute >= Decimal("1e11"):
        seconds = numeric / Decimal("1e3")
    else:
        seconds = numeric
    try:
        return int(seconds * Decimal("1000000"))
    except (OverflowError, ValueError):
        return None


def _record_timestamp(record: dict[str, Any]) -> tuple[str, int] | None:
    for key in _TIMESTAMP_KEYS:
        if key not in record:
            continue
        parsed = _parse_timestamp_us(record[key])
        if parsed is not None:
            return key, parsed
    return None


def _format_timestamp(timestamp_us: int | None) -> str | None:
    if timestamp_us is None:
        return None
    try:
        value = _EPOCH + timedelta(microseconds=timestamp_us)
    except OverflowError:
        return None
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _cadence(stats: _RecordStats) -> CadenceSummary:
    intervals = stats.positive_intervals_us
    if not intervals:
        return CadenceSummary(
            method="consecutive positive timestamp deltas; gaps > 3x median",
            positive_interval_count=0,
            median_interval_ms=None,
            gap_threshold_ms=None,
            inferred_gap_count=0,
            largest_gap_ms=None,
            out_of_order_count=stats.out_of_order_count,
        )
    median_us = float(statistics.median(intervals))
    threshold_us = median_us * GAP_MULTIPLIER
    gaps = [interval for interval in intervals if interval > threshold_us]
    return CadenceSummary(
        method="consecutive positive timestamp deltas; gaps > 3x median",
        positive_interval_count=len(intervals),
        median_interval_ms=round(median_us / 1_000, 3),
        gap_threshold_ms=round(threshold_us / 1_000, 3),
        inferred_gap_count=len(gaps),
        largest_gap_ms=round(max(gaps) / 1_000, 3) if gaps else None,
        out_of_order_count=stats.out_of_order_count,
    )


def _hash_and_line_stats(path: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    line_count = 0
    blank_line_count = 0
    with path.open("rb") as handle:
        for line in handle:
            digest.update(line)
            line_count += 1
            if not line.strip():
                blank_line_count += 1
    return digest.hexdigest(), line_count, blank_line_count


def _scan_jsonl(path: Path) -> tuple[str, int, int, _RecordStats]:
    digest = hashlib.sha256()
    line_count = 0
    blank_line_count = 0
    stats = _RecordStats.empty()
    with path.open("rb") as handle:
        for line in handle:
            digest.update(line)
            line_count += 1
            if not line.strip():
                blank_line_count += 1
                continue
            try:
                decoded = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                stats.malformed_record_count += 1
                continue
            if not isinstance(decoded, dict):
                stats.malformed_record_count += 1
                continue
            stats.add_record(cast(dict[str, Any], decoded))
    return digest.hexdigest(), line_count, blank_line_count, stats


def _scan_csv_records(path: Path, stats: _RecordStats) -> None:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            return
        for row in reader:
            stats.add_record(cast(dict[str, Any], row))


def _scan_json(path: Path, stats: _RecordStats) -> None:
    with path.open("r", encoding="utf-8") as handle:
        decoded = json.load(handle)
    if isinstance(decoded, dict):
        stats.add_record(cast(dict[str, Any], decoded))
    elif isinstance(decoded, list):
        for item in decoded:
            if isinstance(item, dict):
                stats.add_record(cast(dict[str, Any], item))
            else:
                stats.malformed_record_count += 1
    else:
        stats.malformed_record_count += 1


def _file_format(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jsonl", ".csv", ".json"}:
        return suffix[1:]
    return "unknown"


def _quality_flags(stats: _RecordStats, cadence: CadenceSummary) -> tuple[str, ...]:
    flags = ["legacy_research_only"]
    if stats.record_count == 0:
        flags.append("no_valid_records")
    if not stats.timestamp_fields:
        flags.append("no_timestamp_detected")
    if stats.malformed_record_count:
        flags.append("malformed_records")
    if stats.invalid_timestamp_count:
        flags.append("invalid_timestamps")
    if cadence.out_of_order_count:
        flags.append("out_of_order_timestamps")
    if cadence.inferred_gap_count:
        flags.append("inferred_time_gaps")
    return tuple(flags)


def _scan_file(spec: SourceSpec, path: Path) -> tuple[FileInventory, bool]:
    before = path.stat()
    file_format = _file_format(path)
    if file_format == "jsonl":
        sha256, line_count, blank_line_count, stats = _scan_jsonl(path)
    else:
        sha256, line_count, blank_line_count = _hash_and_line_stats(path)
        stats = _RecordStats.empty()
        try:
            if file_format == "csv":
                _scan_csv_records(path, stats)
            elif file_format == "json":
                _scan_json(path, stats)
        except (csv.Error, json.JSONDecodeError, UnicodeDecodeError):
            stats.malformed_record_count += 1
    after = path.stat()
    changed = (before.st_size, before.st_mtime_ns, before.st_ino) != (
        after.st_size,
        after.st_mtime_ns,
        after.st_ino,
    )
    cadence = _cadence(stats)
    flags = list(_quality_flags(stats, cadence))
    if changed:
        flags.append("source_changed_during_scan")
    return (
        FileInventory(
            source_name=spec.name,
            dataset_tier=spec.tier.value,
            source_path=str(path.absolute()),
            file_format=file_format,
            size_bytes=after.st_size,
            mtime_ns=after.st_mtime_ns,
            sha256=sha256,
            line_count=line_count,
            record_count=stats.record_count,
            blank_line_count=blank_line_count,
            malformed_record_count=stats.malformed_record_count,
            invalid_timestamp_count=stats.invalid_timestamp_count,
            top_level_fields=tuple(sorted(stats.fields)),
            schema_variant_count=len(stats.schema_variants),
            timestamp_fields=tuple(sorted(stats.timestamp_fields)),
            first_timestamp_utc=_format_timestamp(stats.first_timestamp_us),
            last_timestamp_utc=_format_timestamp(stats.last_timestamp_us),
            cadence=cadence,
            quality_flags=tuple(flags),
        ),
        changed,
    )


def _annotate_related_files(
    files: list[FileInventory], specs: tuple[SourceSpec, ...]
) -> tuple[list[FileInventory], int, int]:
    hashes: dict[str, list[str]] = {}
    for item in files:
        hashes.setdefault(item.sha256, []).append(item.source_path)
    duplicate_groups = {
        digest: tuple(sorted(paths))
        for digest, paths in hashes.items()
        if len(paths) > 1
    }

    repeated_sources = {
        spec.name for spec in specs if spec.detect_repeated_captures
    }
    captures: dict[tuple[str, str], list[str]] = {}
    for item in files:
        if item.source_name in repeated_sources:
            key = (item.source_name, Path(item.source_path).name)
            captures.setdefault(key, []).append(item.source_path)
    capture_groups = {
        key: tuple(sorted(paths))
        for key, paths in captures.items()
        if len(paths) > 1
    }

    annotated: list[FileInventory] = []
    for item in files:
        duplicate_paths = tuple(
            path
            for path in duplicate_groups.get(item.sha256, ())
            if path != item.source_path
        )
        capture_paths = tuple(
            path
            for path in capture_groups.get(
                (item.source_name, Path(item.source_path).name), ()
            )
            if path != item.source_path
        )
        annotated.append(
            replace(
                item,
                duplicate_paths=duplicate_paths,
                repeated_capture_candidates=capture_paths,
            )
        )
    return annotated, len(duplicate_groups), len(capture_groups)


def build_inventory(specs: tuple[SourceSpec, ...]) -> InventoryManifest:
    """Scan allow-listed files and return a stable manifest without wall-clock data."""

    files: list[FileInventory] = []
    issues: list[InventoryIssue] = []
    claimed_paths: dict[Path, str] = {}
    for spec in sorted(specs, key=lambda item: item.name):
        discovered, discovery_issues = _discover_files(spec)
        issues.extend(discovery_issues)
        if not discovered and not any(issue.fatal for issue in discovery_issues):
            issues.append(
                InventoryIssue(
                    kind="no_files_matched",
                    source_name=spec.name,
                    source_path=str(spec.root.absolute()),
                    message=f"no files matched configured patterns {spec.patterns}",
                    fatal=True,
                )
            )
        for path in discovered:
            absolute = path.absolute()
            previous_source = claimed_paths.get(absolute)
            if previous_source is not None:
                issues.append(
                    InventoryIssue(
                        kind="overlapping_source",
                        source_name=spec.name,
                        source_path=str(absolute),
                        message=f"file already claimed by source {previous_source}",
                        fatal=True,
                    )
                )
                continue
            claimed_paths[absolute] = spec.name
            try:
                item, changed = _scan_file(spec, absolute)
            except OSError as exc:
                issues.append(
                    InventoryIssue(
                        kind="read_error",
                        source_name=spec.name,
                        source_path=str(absolute),
                        message=f"{type(exc).__name__}: {exc}",
                        fatal=True,
                    )
                )
                continue
            files.append(item)
            if changed:
                issues.append(
                    InventoryIssue(
                        kind="source_changed_during_scan",
                        source_name=spec.name,
                        source_path=str(absolute),
                        message=(
                            "size, modification time, or inode changed while reading"
                        ),
                        fatal=True,
                    )
                )

    files.sort(key=lambda item: item.source_path)
    files, duplicate_group_count, capture_group_count = _annotate_related_files(
        files, specs
    )
    issues.sort(
        key=lambda item: (item.source_path, item.kind, item.source_name, item.message)
    )
    summary = InventorySummary(
        file_count=len(files),
        total_size_bytes=sum(item.size_bytes for item in files),
        total_line_count=sum(item.line_count for item in files),
        total_record_count=sum(item.record_count for item in files),
        malformed_record_count=sum(item.malformed_record_count for item in files),
        exact_duplicate_group_count=duplicate_group_count,
        repeated_capture_group_count=capture_group_count,
        fatal_issue_count=sum(issue.fatal for issue in issues),
        warning_issue_count=sum(not issue.fatal for issue in issues),
    )
    return InventoryManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        scanner_version=SCANNER_VERSION,
        reproducibility_note=(
            "No generation timestamp is stored; unchanged sources and scanner "
            "produce byte-identical JSON."
        ),
        gap_policy=(
            "Gaps are inferred when a consecutive positive timestamp delta exceeds "
            "three times the file median. They do not prove a collector outage."
        ),
        sources=tuple(
            _source_description(spec)
            for spec in sorted(specs, key=lambda item: item.name)
        ),
        summary=summary,
        files=tuple(files),
        issues=tuple(issues),
    )


def manifest_json(manifest: InventoryManifest) -> str:
    """Serialize an inventory deterministically."""

    return json.dumps(
        asdict(manifest),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"


def _markdown_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("o", "Kio", "Mio", "Gio", "Tio"):
        if size < 1024 or unit == "Tio":
            return f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def manifest_markdown(manifest: InventoryManifest) -> str:
    """Render the manifest as a concise French research report."""

    summary = manifest.summary
    lines = [
        "# Inventaire des données legacy HyperBot",
        "",
        "> Données de recherche uniquement. Les niveaux B/C ne valident ni la",
        "> position de file, ni un fill maker central/pessimiste, ni une promotion.",
        "",
        "Le rapport est déterministe : il n'enregistre pas l'heure d'exécution.",
        "Les trous sont inférés à partir de la cadence médiane et ne prouvent pas,",
        "à eux seuls, une panne de collecte.",
        "",
        "## Résumé",
        "",
        f"- Fichiers : {summary.file_count}",
        f"- Taille : {_human_bytes(summary.total_size_bytes)}",
        f"- Lignes physiques : {summary.total_line_count}",
        f"- Records valides : {summary.total_record_count}",
        f"- Records malformés : {summary.malformed_record_count}",
        f"- Groupes de doublons exacts : {summary.exact_duplicate_group_count}",
        f"- Captures répétées candidates : {summary.repeated_capture_group_count}",
        f"- Erreurs fatales : {summary.fatal_issue_count}",
        f"- Avertissements : {summary.warning_issue_count}",
        "",
        "## Couverture et limites",
        "",
        "| Dataset | Niveau | Connu | Approximé | Absent |",
        "|---|---:|---|---|---|",
    ]
    for source in manifest.sources:
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(value)
                for value in (
                    source.name,
                    source.tier,
                    source.known,
                    source.approximated,
                    source.absent,
                )
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Fichiers",
            "",
            "| Source | Niv. | Chemin | Taille | SHA-256 | Lignes | Période UTC | "
            "Médiane | Trous inférés | Flags |",
            "|---|---:|---|---:|---|---:|---|---:|---:|---|",
        ]
    )
    for item in manifest.files:
        period = f"{item.first_timestamp_utc or '—'} → {item.last_timestamp_utc or '—'}"
        median = (
            f"{item.cadence.median_interval_ms} ms"
            if item.cadence.median_interval_ms is not None
            else "—"
        )
        lines.append(
            "| "
            + " | ".join(
                _markdown_cell(value)
                for value in (
                    item.source_name,
                    item.dataset_tier,
                    item.source_path,
                    _human_bytes(item.size_bytes),
                    item.sha256,
                    item.line_count,
                    period,
                    median,
                    item.cadence.inferred_gap_count,
                    ", ".join(item.quality_flags),
                )
            )
            + " |"
        )

    lines.extend(["", "## Anomalies", ""])
    if manifest.issues:
        for issue in manifest.issues:
            severity = "ERREUR" if issue.fatal else "WARN"
            lines.append(
                f"- [{severity}] `{issue.kind}` — `{issue.source_path}` : "
                f"{issue.message}"
            )
    else:
        lines.append("Aucune erreur de lecture ou symlink détecté.")

    duplicate_files = [item for item in manifest.files if item.duplicate_paths]
    capture_files = [
        item for item in manifest.files if item.repeated_capture_candidates
    ]
    lines.extend(["", "## Doublons et captures répétées", ""])
    if not duplicate_files and not capture_files:
        lines.append("Aucun doublon exact ni capture répétée candidate.")
    else:
        for item in duplicate_files:
            lines.append(
                f"- Doublon SHA-256 de `{item.source_path}` : "
                + ", ".join(f"`{path}`" for path in item.duplicate_paths)
            )
        reported_capture_groups: set[tuple[str, ...]] = set()
        for item in capture_files:
            group = tuple(sorted((item.source_path, *item.repeated_capture_candidates)))
            if group in reported_capture_groups:
                continue
            reported_capture_groups.add(group)
            lines.append(
                f"- Même nom de capture que `{item.source_path}` : "
                + ", ".join(
                    f"`{path}`" for path in item.repeated_capture_candidates
                )
            )
    return "\n".join(lines) + "\n"


def write_inventory(
    manifest: InventoryManifest,
    output_dir: str | Path,
) -> tuple[Path, Path, Path]:
    """Write JSON, Markdown, and independent checksums for both manifests."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "manifest.json"
    markdown_path = destination / "summary.md"
    checksums_path = destination / "SHA256SUMS"
    json_bytes = manifest_json(manifest).encode("utf-8")
    markdown_bytes = manifest_markdown(manifest).encode("utf-8")
    json_path.write_bytes(json_bytes)
    markdown_path.write_bytes(markdown_bytes)
    checksums_path.write_text(
        f"{hashlib.sha256(json_bytes).hexdigest()}  {json_path.name}\n"
        f"{hashlib.sha256(markdown_bytes).hexdigest()}  {markdown_path.name}\n",
        encoding="utf-8",
    )
    return json_path, markdown_path, checksums_path
