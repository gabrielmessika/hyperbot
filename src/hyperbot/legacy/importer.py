"""Checksummed, deterministic import pipeline for legacy HyperBot research data."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tempfile
from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, BinaryIO, cast

from hyperbot.legacy.adapters import (
    AdaptationContext,
    LegacyAdaptationError,
    adapter_for_source,
)
from hyperbot.legacy.policy import ReplayAuthorization, legacy_policy_matrix
from hyperbot.models import (
    DatasetTier,
    DomainEvent,
    EventContext,
    TimeSource,
    event_payload,
    event_type,
)

IMPORT_REPORT_SCHEMA_VERSION = 1
IMPORT_EVENT_SCHEMA_VERSION = 1
IMPORTER_VERSION = "1.0.0"
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")


class LegacyImportError(RuntimeError):
    """The import cannot safely produce a deterministic artifact."""


@dataclass(frozen=True, slots=True)
class InventoryFile:
    source_name: str
    dataset_tier: DatasetTier
    source_path: Path
    source_sha256: str
    file_format: str
    size_bytes: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class CountEntry:
    name: str
    count: int


@dataclass(frozen=True, slots=True)
class SourceCoverage:
    source_name: str
    dataset_tier: str
    known: str
    approximated: str
    absent: str


@dataclass(frozen=True, slots=True)
class FileImportReport:
    source_name: str
    dataset_tier: str
    source_path: str
    source_sha256: str
    adapter_name: str
    adapter_version: str
    accepted_records: int
    rejected_records: int
    emitted_events: int
    event_type_counts: tuple[CountEntry, ...]
    rejection_counts: tuple[CountEntry, ...]
    output_path: str
    output_sha256: str
    output_size_bytes: int
    error_path: str
    error_sha256: str
    error_size_bytes: int


@dataclass(frozen=True, slots=True)
class ImportSummary:
    file_count: int
    accepted_records: int
    rejected_records: int
    emitted_events: int
    output_size_bytes: int
    error_size_bytes: int


@dataclass(frozen=True, slots=True)
class LegacyImportReport:
    schema_version: int
    importer_version: str
    inventory_manifest_path: str
    inventory_manifest_sha256: str
    import_config_hash: str
    run_id: str
    reproducibility_note: str
    summary: ImportSummary
    event_type_counts: tuple[CountEntry, ...]
    rejection_counts: tuple[CountEntry, ...]
    dataset_coverage: tuple[SourceCoverage, ...]
    replay_policy: tuple[ReplayAuthorization, ...]
    files: tuple[FileImportReport, ...]


@dataclass(slots=True)
class _ImportCounters:
    accepted: int
    rejected: int
    emitted: int
    event_types: Counter[str]
    rejection_codes: Counter[str]

    @classmethod
    def empty(cls) -> _ImportCounters:
        return cls(0, 0, 0, Counter(), Counter())


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _count_entries(counter: Counter[str]) -> tuple[CountEntry, ...]:
    return tuple(CountEntry(name, counter[name]) for name in sorted(counter))


def _require_dict(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LegacyImportError(f"{name} must be an object")
    return cast(dict[str, Any], value)


def _require_string(record: dict[str, Any], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value:
        raise LegacyImportError(f"inventory file has invalid {key}")
    return value


def load_inventory_files(
    manifest_path: str | Path,
) -> tuple[str, tuple[InventoryFile, ...], tuple[SourceCoverage, ...]]:
    """Load the M1L.1 manifest and reject incomplete or unsafe inventories."""

    path = Path(manifest_path).resolve()
    raw = path.read_bytes()
    manifest_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        document = _require_dict(json.loads(raw), "inventory manifest")
    except json.JSONDecodeError as exc:
        raise LegacyImportError("inventory manifest is not valid JSON") from exc
    if document.get("schema_version") != 1:
        raise LegacyImportError("unsupported inventory manifest schema")
    summary = _require_dict(document.get("summary"), "inventory summary")
    if summary.get("fatal_issue_count") != 0:
        raise LegacyImportError("inventory contains fatal issues")
    raw_files = document.get("files")
    if not isinstance(raw_files, list) or not raw_files:
        raise LegacyImportError("inventory contains no files")

    files: list[InventoryFile] = []
    for value in raw_files:
        item = _require_dict(value, "inventory file")
        try:
            tier = DatasetTier(_require_string(item, "dataset_tier"))
        except ValueError as exc:
            raise LegacyImportError("inventory has an invalid dataset tier") from exc
        if tier is DatasetTier.A:
            raise LegacyImportError("legacy importer accepts only level B/C sources")
        source_path = Path(_require_string(item, "source_path"))
        source_sha256 = _require_string(item, "sha256")
        if len(source_sha256) != 64:
            raise LegacyImportError("inventory has an invalid source SHA-256")
        file_format = _require_string(item, "file_format")
        if file_format not in {"jsonl", "csv"}:
            raise LegacyImportError(f"unsupported legacy format {file_format}")
        size_bytes = item.get("size_bytes")
        mtime_ns = item.get("mtime_ns")
        if not isinstance(size_bytes, int) or not isinstance(mtime_ns, int):
            raise LegacyImportError("inventory has invalid source stat metadata")
        files.append(
            InventoryFile(
                source_name=_require_string(item, "source_name"),
                dataset_tier=tier,
                source_path=source_path,
                source_sha256=source_sha256,
                file_format=file_format,
                size_bytes=size_bytes,
                mtime_ns=mtime_ns,
            )
        )

    raw_sources = document.get("sources")
    if not isinstance(raw_sources, list) or not raw_sources:
        raise LegacyImportError("inventory contains no source coverage")
    coverage: list[SourceCoverage] = []
    for value in raw_sources:
        source = _require_dict(value, "inventory source")
        coverage.append(
            SourceCoverage(
                source_name=_require_string(source, "name"),
                dataset_tier=_require_string(source, "tier"),
                known=_require_string(source, "known"),
                approximated=_require_string(source, "approximated"),
                absent=_require_string(source, "absent"),
            )
        )
    files.sort(key=lambda item: str(item.source_path))
    coverage.sort(key=lambda item: item.source_name)
    return manifest_sha256, tuple(files), tuple(coverage)


def _import_config_hash(inventory_sha256: str, files: tuple[InventoryFile, ...]) -> str:
    sources = sorted({item.source_name for item in files})
    adapters = []
    for source in sources:
        adapter = adapter_for_source(source)
        adapters.append(
            {"source": source, "adapter": adapter.name, "version": adapter.version}
        )
    payload = {
        "inventory_sha256": inventory_sha256,
        "importer_version": IMPORTER_VERSION,
        "adapters": adapters,
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _write_event(handle: BinaryIO, event: DomainEvent) -> None:
    payload = event_payload(event)
    payload_bytes = _canonical_json(payload)
    envelope = {
        "schema_version": IMPORT_EVENT_SCHEMA_VERSION,
        "event_type": event_type(event),
        "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "payload": payload,
    }
    handle.write(_canonical_json(envelope) + b"\n")


def _write_rejection(
    handle: BinaryIO,
    *,
    source: InventoryFile,
    record_number: int,
    record_sha256: str,
    hash_kind: str,
    code: str,
    message: str,
) -> None:
    rejection = {
        "schema_version": 1,
        "source_name": source.source_name,
        "dataset_tier": source.dataset_tier.value,
        "source_path": str(source.source_path),
        "source_sha256": source.source_sha256,
        "source_record_number": record_number,
        "source_record_sha256": record_sha256,
        "source_record_hash_kind": hash_kind,
        "error_code": code,
        "message": message,
        "legacy_research_only": True,
    }
    handle.write(_canonical_json(rejection) + b"\n")


def _adapt_record(
    record: dict[str, Any],
    *,
    source: InventoryFile,
    record_number: int,
    record_sha256: str,
    hash_kind: str,
    event_context: EventContext,
    output: BinaryIO,
    errors: BinaryIO,
    counters: _ImportCounters,
) -> None:
    adapter = adapter_for_source(source.source_name)
    adaptation_context = AdaptationContext(
        event_context=event_context,
        dataset_tier=source.dataset_tier,
        source_path=str(source.source_path),
        source_sha256=source.source_sha256,
        source_record_number=record_number,
        source_record_sha256=record_sha256,
        source_record_hash_kind=hash_kind,
    )
    try:
        events = adapter.adapt(record, adaptation_context)
    except (LegacyAdaptationError, ValueError) as exc:
        code = exc.code if isinstance(exc, LegacyAdaptationError) else "invalid_event"
        _write_rejection(
            errors,
            source=source,
            record_number=record_number,
            record_sha256=record_sha256,
            hash_kind=hash_kind,
            code=code,
            message=str(exc),
        )
        counters.rejected += 1
        counters.rejection_codes[code] += 1
        return
    counters.accepted += 1
    for event in events:
        _write_event(output, event)
        counters.emitted += 1
        counters.event_types[event_type(event)] += 1


def _import_jsonl(
    source: InventoryFile,
    event_context: EventContext,
    output: BinaryIO,
    errors: BinaryIO,
    counters: _ImportCounters,
) -> None:
    with source.source_path.open("rb") as handle:
        for record_number, line in enumerate(handle, start=1):
            record_sha256 = hashlib.sha256(line).hexdigest()
            try:
                decoded = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                code = "corrupt_json"
                _write_rejection(
                    errors,
                    source=source,
                    record_number=record_number,
                    record_sha256=record_sha256,
                    hash_kind="raw_line_sha256",
                    code=code,
                    message=f"{type(exc).__name__}: {exc}",
                )
                counters.rejected += 1
                counters.rejection_codes[code] += 1
                continue
            if not isinstance(decoded, dict):
                code = "non_object_record"
                _write_rejection(
                    errors,
                    source=source,
                    record_number=record_number,
                    record_sha256=record_sha256,
                    hash_kind="raw_line_sha256",
                    code=code,
                    message="JSONL record must be an object",
                )
                counters.rejected += 1
                counters.rejection_codes[code] += 1
                continue
            _adapt_record(
                cast(dict[str, Any], decoded),
                source=source,
                record_number=record_number,
                record_sha256=record_sha256,
                hash_kind="raw_line_sha256",
                event_context=event_context,
                output=output,
                errors=errors,
                counters=counters,
            )


def _csv_records(source_path: Path) -> Iterator[tuple[int, dict[str, Any], str]]:
    with source_path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise LegacyImportError(f"CSV source has no header: {source_path}")
        for record_number, record in enumerate(reader, start=1):
            normalized = cast(dict[str, Any], record)
            record_sha256 = hashlib.sha256(_canonical_json(normalized)).hexdigest()
            yield record_number, normalized, record_sha256


def _import_csv(
    source: InventoryFile,
    event_context: EventContext,
    output: BinaryIO,
    errors: BinaryIO,
    counters: _ImportCounters,
) -> None:
    try:
        for record_number, record, record_sha256 in _csv_records(source.source_path):
            _adapt_record(
                record,
                source=source,
                record_number=record_number,
                record_sha256=record_sha256,
                hash_kind="canonical_json_sha256",
                event_context=event_context,
                output=output,
                errors=errors,
                counters=counters,
            )
    except (csv.Error, UnicodeDecodeError) as exc:
        raise LegacyImportError(
            f"cannot parse CSV source {source.source_path}"
        ) from exc


def _publish_immutable(temporary_path: Path, destination: Path) -> tuple[str, int]:
    digest = _sha256_file(temporary_path)
    size = temporary_path.stat().st_size
    if destination.exists():
        if destination.stat().st_size != size or _sha256_file(destination) != digest:
            raise LegacyImportError(
                f"immutable derived artifact conflicts with existing {destination}"
            )
        temporary_path.unlink()
    else:
        os.replace(temporary_path, destination)
    return digest, size


def _safe_output_paths(
    source: InventoryFile, output_root: Path, run_id: str
) -> tuple[Path, Path]:
    if not _SAFE_COMPONENT.fullmatch(source.source_name):
        raise LegacyImportError(f"unsafe source name {source.source_name!r}")
    destination = output_root / run_id / source.source_name
    destination.mkdir(parents=True, exist_ok=True)
    stem = f"{source.source_path.name}.{source.source_sha256[:16]}"
    return destination / f"{stem}.jsonl", destination / f"{stem}.errors.jsonl"


def _import_file(
    source: InventoryFile,
    *,
    output_root: Path,
    run_id: str,
    event_context: EventContext,
) -> FileImportReport:
    if not source.source_path.is_file() or source.source_path.is_symlink():
        raise LegacyImportError(
            f"source is not a regular non-symlink file: {source.source_path}"
        )
    before = source.source_path.stat()
    if before.st_size != source.size_bytes or before.st_mtime_ns != source.mtime_ns:
        raise LegacyImportError(
            f"source stat differs from manifest: {source.source_path}"
        )
    if _sha256_file(source.source_path) != source.source_sha256:
        raise LegacyImportError(
            f"source checksum differs from manifest: {source.source_path}"
        )

    output_path, error_path = _safe_output_paths(source, output_root, run_id)
    output_temp: Path | None = None
    error_temp: Path | None = None
    counters = _ImportCounters.empty()
    try:
        with tempfile.NamedTemporaryFile(
            mode="w+b", dir=output_path.parent, delete=False
        ) as output_handle, tempfile.NamedTemporaryFile(
            mode="w+b", dir=error_path.parent, delete=False
        ) as error_handle:
            output_temp = Path(output_handle.name)
            error_temp = Path(error_handle.name)
            output_stream = cast(BinaryIO, output_handle)
            error_stream = cast(BinaryIO, error_handle)
            if source.file_format == "jsonl":
                _import_jsonl(
                    source, event_context, output_stream, error_stream, counters
                )
            elif source.file_format == "csv":
                _import_csv(
                    source, event_context, output_stream, error_stream, counters
                )
            else:
                raise LegacyImportError(f"unsupported format {source.file_format}")
            output_handle.flush()
            error_handle.flush()
            os.fsync(output_handle.fileno())
            os.fsync(error_handle.fileno())

        after = source.source_path.stat()
        if (before.st_size, before.st_mtime_ns, before.st_ino) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ino,
        ):
            raise LegacyImportError(
                f"source changed during import: {source.source_path}"
            )
        output_sha256, output_size = _publish_immutable(output_temp, output_path)
        output_temp = None
        error_sha256, error_size = _publish_immutable(error_temp, error_path)
        error_temp = None
    finally:
        for temporary in (output_temp, error_temp):
            if temporary is not None and temporary.exists():
                temporary.unlink()

    adapter = adapter_for_source(source.source_name)
    return FileImportReport(
        source_name=source.source_name,
        dataset_tier=source.dataset_tier.value,
        source_path=str(source.source_path),
        source_sha256=source.source_sha256,
        adapter_name=adapter.name,
        adapter_version=adapter.version,
        accepted_records=counters.accepted,
        rejected_records=counters.rejected,
        emitted_events=counters.emitted,
        event_type_counts=_count_entries(counters.event_types),
        rejection_counts=_count_entries(counters.rejection_codes),
        output_path=str(output_path),
        output_sha256=output_sha256,
        output_size_bytes=output_size,
        error_path=str(error_path),
        error_sha256=error_sha256,
        error_size_bytes=error_size,
    )


def import_legacy_data(
    inventory_manifest_path: str | Path,
    output_root: str | Path,
) -> LegacyImportReport:
    """Verify every source, adapt all records, and publish immutable artifacts."""

    manifest_path = Path(inventory_manifest_path).resolve()
    output = Path(output_root).resolve()
    inventory_sha256, files, dataset_coverage = load_inventory_files(manifest_path)
    config_hash = _import_config_hash(inventory_sha256, files)
    run_id = f"legacy-import-{config_hash[:16]}"
    event_context = EventContext(
        run_id=run_id,
        code_version=f"legacy-importer-{IMPORTER_VERSION}",
        config_hash=config_hash,
        time_source=TimeSource.REPLAY,
    )
    reports = tuple(
        _import_file(
            source,
            output_root=output,
            run_id=run_id,
            event_context=event_context,
        )
        for source in files
    )
    event_types: Counter[str] = Counter()
    rejection_codes: Counter[str] = Counter()
    for report in reports:
        event_types.update({item.name: item.count for item in report.event_type_counts})
        rejection_codes.update(
            {item.name: item.count for item in report.rejection_counts}
        )
    summary = ImportSummary(
        file_count=len(reports),
        accepted_records=sum(report.accepted_records for report in reports),
        rejected_records=sum(report.rejected_records for report in reports),
        emitted_events=sum(report.emitted_events for report in reports),
        output_size_bytes=sum(report.output_size_bytes for report in reports),
        error_size_bytes=sum(report.error_size_bytes for report in reports),
    )
    return LegacyImportReport(
        schema_version=IMPORT_REPORT_SCHEMA_VERSION,
        importer_version=IMPORTER_VERSION,
        inventory_manifest_path=str(manifest_path),
        inventory_manifest_sha256=inventory_sha256,
        import_config_hash=config_hash,
        run_id=run_id,
        reproducibility_note=(
            "No wall-clock field is stored. Existing derived files are accepted only "
            "when their byte-level checksum matches a fresh import."
        ),
        summary=summary,
        event_type_counts=_count_entries(event_types),
        rejection_counts=_count_entries(rejection_codes),
        dataset_coverage=dataset_coverage,
        replay_policy=legacy_policy_matrix(),
        files=reports,
    )


def import_report_json(report: LegacyImportReport) -> str:
    return json.dumps(
        asdict(report), ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"


def _human_bytes(value: int) -> str:
    size = float(value)
    for unit in ("o", "Kio", "Mio", "Gio", "Tio"):
        if size < 1024 or unit == "Tio":
            return f"{size:.1f} {unit}"
        size /= 1024
    raise AssertionError("unreachable")


def import_report_markdown(report: LegacyImportReport) -> str:
    """Render acceptance/rejection counts and the complete evidence boundary."""

    summary = report.summary
    lines = [
        "# Import et couverture legacy HyperBot",
        "",
        "> Tous les événements de ce rapport restent `legacy_research_only`.",
        "",
        f"- Run : `{report.run_id}`",
        f"- Manifest source : `{report.inventory_manifest_sha256}`",
        f"- Fichiers : {summary.file_count}",
        f"- Records acceptés : {summary.accepted_records}",
        f"- Records rejetés : {summary.rejected_records}",
        f"- Événements émis : {summary.emitted_events}",
        f"- Taille dérivée : {_human_bytes(summary.output_size_bytes)}",
        f"- Flux d'erreurs : {_human_bytes(summary.error_size_bytes)}",
        "",
        "## Événements normalisés",
        "",
        "| Type | Nombre |",
        "|---|---:|",
    ]
    for item in report.event_type_counts:
        lines.append(f"| {item.name} | {item.count} |")
    lines.extend(["", "## Records rejetés", ""])
    if report.rejection_counts:
        lines.extend(["| Motif | Nombre |", "|---|---:|"])
        for item in report.rejection_counts:
            lines.append(f"| {item.name} | {item.count} |")
    else:
        lines.append("Aucun record rejeté.")

    lines.extend(
        [
            "",
            "## Couverture par dataset",
            "",
            "| Dataset | Niveau | Connu | Approximé | Absent |",
            "|---|---:|---|---|---|",
        ]
    )
    for coverage in report.dataset_coverage:
        lines.append(
            f"| {coverage.source_name} | {coverage.dataset_tier} | "
            f"{coverage.known} | {coverage.approximated} | "
            f"{coverage.absent} |"
        )

    lines.extend(
        [
            "",
            "## Politique de preuve M1L.3",
            "",
            "| Usage | Autorisé avec B/C | Label obligatoire | Motif |",
            "|---|---:|---|---|",
        ]
    )
    for decision in report.replay_policy:
        lines.append(
            f"| {decision.replay_use.value} | "
            f"{'oui' if decision.allowed else 'non'} | "
            f"{decision.required_label or '—'} | {decision.reason} |"
        )

    lines.extend(
        [
            "",
            "## Résultats par fichier",
            "",
            "| Source | Niveau | Fichier | Adaptateur | Acceptés | Rejetés | Émis |",
            "|---|---:|---|---|---:|---:|---:|",
        ]
    )
    for file_report in report.files:
        lines.append(
            f"| {file_report.source_name} | {file_report.dataset_tier} | "
            f"`{file_report.source_path}` | "
            f"{file_report.adapter_name}@{file_report.adapter_version} | "
            f"{file_report.accepted_records} | {file_report.rejected_records} | "
            f"{file_report.emitted_events} |"
        )
    lines.extend(
        [
            "",
            "## Limites",
            "",
            "Les événements normalisés conservent une référence vérifiable vers la "
            "source et n'ajoutent aucun champ absent. Les tailles inconnues restent "
            "nulles. Les profondeurs GBOT/HIP-4 sont agrégées et ne reconstruisent "
            "pas la file. Les snapshots TRIDENT restent des features historiques.",
            "",
            "Les données B/C peuvent alimenter fair value, spreads, profondeur "
            "agrégée, markouts, parité, détection stale, reproduction historique et "
            "la borne `optimistic_touch`. Elles ne peuvent valider ni position de "
            "file, ni fills maker partiels, ni modèles central/pessimiste, ni "
            "rentabilité live, ni promotion canary.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_import_report(
    report: LegacyImportReport, output_dir: str | Path
) -> tuple[Path, Path, Path]:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    json_path = destination / "report.json"
    markdown_path = destination / "coverage.md"
    checksums_path = destination / "SHA256SUMS"
    json_bytes = import_report_json(report).encode("utf-8")
    markdown_bytes = import_report_markdown(report).encode("utf-8")
    json_path.write_bytes(json_bytes)
    markdown_path.write_bytes(markdown_bytes)
    checksums_path.write_text(
        f"{hashlib.sha256(json_bytes).hexdigest()}  {json_path.name}\n"
        f"{hashlib.sha256(markdown_bytes).hexdigest()}  {markdown_path.name}\n",
        encoding="utf-8",
    )
    return json_path, markdown_path, checksums_path
