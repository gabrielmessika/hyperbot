"""Immutable, secret-free export manifests for remote HyperBot data fetches."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

from hyperbot.ops import OPS_SCHEMA_VERSION, atomic_write_json

_CLOSED_SEGMENT = re.compile(r"^(\d{4}-\d{2}-\d{2})-\d{6}\.jsonl(?:\.gz)?$")
_CHECKSUM = re.compile(r"^[0-9a-f]{64}$")
_STREAM = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class ExportError(RuntimeError):
    """Raised when an export could escape its allow-listed roots."""


@dataclass(frozen=True, slots=True)
class ExportFile:
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ExportBundle:
    directory: Path
    manifest_path: Path
    checksum_path: Path
    files_path: Path
    files: tuple[ExportFile, ...]


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
    )


def completed_utc_dates(*, today: date, days: int) -> tuple[str, ...]:
    if days <= 0:
        raise ValueError("days must be positive")
    return tuple(
        (today - timedelta(days=offset)).isoformat() for offset in range(1, days + 1)
    )


def _safe_relative(path: Path, root: Path) -> str:
    if path.is_symlink():
        raise ExportError(f"symlinks are forbidden in exports: {path}")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ExportError(f"export path escapes root: {path}") from exc
    if not relative.parts or ".." in relative.parts:
        raise ExportError(f"unsafe export path: {relative}")
    return relative.as_posix()


def _selected_files(
    root: Path,
    *,
    dates: frozenset[str],
    include_all: bool,
) -> tuple[Path, ...]:
    candidates: set[Path] = set()
    collector_roots = (
        root / "data" / "raw" / "collector",
        root / "archive" / "collector",
    )
    for collector_root in collector_roots:
        if not collector_root.is_dir():
            continue
        for path in collector_root.glob("*/*"):
            if path.is_symlink():
                raise ExportError(f"symlinks are forbidden in exports: {path}")
            if not path.is_file():
                continue
            match = _CLOSED_SEGMENT.fullmatch(path.name)
            if match and (include_all or match.group(1) in dates):
                candidates.add(path)
    review_root = root / "data" / "reviews"
    if review_root.is_dir():
        for path in review_root.rglob("*"):
            if path.is_symlink():
                raise ExportError(f"symlinks are forbidden in exports: {path}")
            if not path.is_file():
                continue
            if include_all or any(current in path.name for current in dates):
                candidates.add(path)
    return tuple(sorted(candidates))


def _store_manifest_snapshots(
    root: Path,
    selected: tuple[Path, ...],
) -> list[dict[str, object]]:
    streams: set[str] = set()
    for path in selected:
        relative = path.relative_to(root).parts
        if len(relative) >= 5 and relative[:3] == ("data", "raw", "collector"):
            streams.add(relative[3])
        elif len(relative) >= 4 and relative[:2] == ("archive", "collector"):
            streams.add(relative[2])
    snapshots: list[dict[str, object]] = []
    for stream in sorted(streams):
        if _STREAM.fullmatch(stream) is None:
            raise ExportError(f"invalid collector stream: {stream}")
        manifest_path = root / "data" / "raw" / "collector" / stream / "manifest.json"
        checksum_path = manifest_path.with_suffix(".sha256")
        if not manifest_path.exists() and not checksum_path.exists():
            continue
        if (
            manifest_path.is_symlink()
            or checksum_path.is_symlink()
            or not manifest_path.is_file()
            or not checksum_path.is_file()
        ):
            raise ExportError(f"unsafe collector manifest: {manifest_path}")
        raw = manifest_path.read_bytes()
        actual = hashlib.sha256(raw).hexdigest()
        if checksum_path.read_text(encoding="ascii").strip() != actual:
            raise ExportError(f"collector manifest checksum mismatch: {manifest_path}")
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ExportError(f"invalid collector manifest: {manifest_path}") from exc
        if (
            not isinstance(decoded, dict)
            or decoded.get("stream") != stream
            or not isinstance(decoded.get("segments"), list)
        ):
            raise ExportError(f"invalid collector manifest: {manifest_path}")
        snapshots.append(
            {
                "root": "data/raw/collector",
                "archive_root": "archive/collector",
                "stream": stream,
                "manifest_sha256": actual,
                "manifest": decoded,
            }
        )
    return snapshots


def build_export_bundle(
    root: Path,
    *,
    dates: tuple[str, ...],
    include_all: bool,
    generated_at: datetime,
    output_root: Path | None = None,
) -> ExportBundle:
    """Build metadata for immutable data files; never export env or open segments."""

    root = root.resolve()
    if root == Path("/") or not root.is_dir():
        raise ExportError("export root must be an existing narrow directory")
    parsed_dates: list[str] = []
    for value in dates:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ExportError(f"invalid export date: {value}")
        parsed_dates.append(value)
    if not include_all and not parsed_dates:
        raise ExportError("at least one export date is required")
    normalized_time = generated_at.astimezone(UTC)
    run_id = normalized_time.strftime("fetch-%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:8]
    destination_root = (
        output_root.resolve()
        if output_root is not None
        else root / "runtime" / "fetch_exports"
    )
    destination = destination_root / run_id
    if destination.exists():
        raise FileExistsError(f"export bundle already exists: {destination}")
    destination.mkdir(parents=True, mode=0o750)

    selected = _selected_files(
        root,
        dates=frozenset(parsed_dates),
        include_all=include_all,
    )
    files: list[ExportFile] = []
    for path in selected:
        relative = _safe_relative(path, root)
        content = path.read_bytes()
        files.append(
            ExportFile(
                path=relative,
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
            )
        )
    store_manifests = _store_manifest_snapshots(root, selected)

    def sanitize(value: object) -> object:
        if isinstance(value, dict):
            return {
                str(key): sanitize(item)
                for key, item in cast(dict[object, object], value).items()
                if "secret" not in str(key).lower()
                and "private" not in str(key).lower()
                and "password" not in str(key).lower()
                and "webhook" not in str(key).lower()
            }
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        return value

    status_payloads: dict[str, object] = {}
    for name in (
        "collector_status.json",
        "maintenance_status.json",
        "watchdog_status.json",
    ):
        status_path = root / "runtime" / name
        try:
            decoded = json.loads(status_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(decoded, dict):
            status_payloads[name] = sanitize(decoded)

    manifest_path = destination / "manifest.json"
    checksum_path = destination / "SHA256SUMS"
    files_path = destination / "files.txt"
    atomic_write_json(
        manifest_path,
        {
            "schema_version": OPS_SCHEMA_VERSION,
            "run_id": run_id,
            "generated_at": normalized_time.isoformat(),
            "selection": "all" if include_all else "dates",
            "dates": sorted(parsed_dates),
            "public_only": True,
            "open_segments_included": False,
            "environment_files_included": False,
            "files": [
                {
                    "path": item.path,
                    "size_bytes": item.size_bytes,
                    "sha256": item.sha256,
                }
                for item in files
            ],
            "store_manifests": store_manifests,
            "runtime_status": status_payloads,
        },
    )
    checksum_path.write_text(
        "".join(f"{item.sha256}  {item.path}\n" for item in files),
        encoding="utf-8",
    )
    files_path.write_text(
        "".join(f"{item.path}\n" for item in files),
        encoding="utf-8",
    )
    os.chmod(checksum_path, 0o640)
    os.chmod(files_path, 0o640)
    return ExportBundle(
        directory=destination,
        manifest_path=manifest_path,
        checksum_path=checksum_path,
        files_path=files_path,
        files=tuple(files),
    )


def verify_export_manifest(
    manifest_path: Path,
    data_root: Path,
) -> tuple[ExportFile, ...]:
    """Verify every manifest file below the requested local data root."""

    try:
        decoded = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"cannot read export manifest: {manifest_path}") from exc
    if not isinstance(decoded, dict) or decoded.get("public_only") is not True:
        raise ExportError("invalid or unsafe export manifest")
    raw_files = decoded.get("files")
    if not isinstance(raw_files, list):
        raise ExportError("export manifest files are invalid")
    root = data_root.resolve()
    verified: list[ExportFile] = []
    for raw in raw_files:
        if not isinstance(raw, dict):
            raise ExportError("export file entry is invalid")
        relative = raw.get("path")
        size = raw.get("size_bytes")
        checksum = raw.get("sha256")
        if (
            not isinstance(relative, str)
            or not isinstance(size, int)
            or not isinstance(checksum, str)
            or _CHECKSUM.fullmatch(checksum) is None
        ):
            raise ExportError("export file metadata is invalid")
        unresolved = root / relative
        current = root
        for part in Path(relative).parts:
            current /= part
            if current.is_symlink():
                raise ExportError(f"symlink in exported path: {relative}")
        candidate = unresolved.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ExportError(f"manifest path escapes local root: {relative}") from exc
        if candidate.is_symlink() or not candidate.is_file():
            raise ExportError(f"exported file is missing: {relative}")
        content = candidate.read_bytes()
        if len(content) != size or hashlib.sha256(content).hexdigest() != checksum:
            raise ExportError(f"exported file checksum mismatch: {relative}")
        verified.append(ExportFile(relative, size, checksum))
    return tuple(verified)


def materialize_export_store_manifests(
    manifest_path: Path,
    data_root: Path,
) -> tuple[Path, ...]:
    """Restore immutable manifest snapshots beside verified fetched segments."""

    try:
        decoded = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExportError(f"cannot read export manifest: {manifest_path}") from exc
    if not isinstance(decoded, dict) or decoded.get("public_only") is not True:
        raise ExportError("invalid or unsafe export manifest")
    raw_snapshots = decoded.get("store_manifests")
    if not isinstance(raw_snapshots, list):
        raise ExportError("export store manifests are missing")
    raw_files = decoded.get("files")
    if not isinstance(raw_files, list):
        raise ExportError("export manifest files are invalid")
    exported = {
        str(item.get("path")): str(item.get("sha256"))
        for item in raw_files
        if isinstance(item, dict)
    }
    root = data_root.resolve()
    written: list[Path] = []
    for raw_snapshot in raw_snapshots:
        if not isinstance(raw_snapshot, dict):
            raise ExportError("export store manifest snapshot is invalid")
        stream = raw_snapshot.get("stream")
        relative_root = raw_snapshot.get("root")
        checksum = raw_snapshot.get("manifest_sha256")
        store_manifest = raw_snapshot.get("manifest")
        if (
            not isinstance(stream, str)
            or _STREAM.fullmatch(stream) is None
            or relative_root != "data/raw/collector"
            or not isinstance(checksum, str)
            or _CHECKSUM.fullmatch(checksum) is None
            or not isinstance(store_manifest, dict)
            or store_manifest.get("stream") != stream
            or not isinstance(store_manifest.get("segments"), list)
        ):
            raise ExportError("export store manifest snapshot is invalid")
        encoded = _canonical_json(store_manifest)
        if hashlib.sha256(encoded).hexdigest() != checksum:
            raise ExportError("export store manifest snapshot checksum mismatch")
        segment_by_path = {
            str(item.get("path")): item
            for item in store_manifest["segments"]
            if isinstance(item, dict)
        }
        prefixes = (
            f"data/raw/collector/{stream}/",
            f"archive/collector/{stream}/",
        )
        selected_paths = [path for path in exported if path.startswith(prefixes)]
        if not selected_paths:
            raise ExportError(f"store manifest has no exported segments: {stream}")
        for relative in selected_paths:
            name = Path(relative).name
            segment = segment_by_path.get(name)
            if (
                not isinstance(segment, dict)
                or segment.get("storage_sha256") != exported[relative]
            ):
                raise ExportError(f"exported segment evidence mismatch: {relative}")
        stream_root = root / "data" / "raw" / "collector" / stream
        try:
            stream_root.resolve().relative_to(root)
        except ValueError as exc:
            raise ExportError("store manifest path escapes payload root") from exc
        current = root
        for part in ("data", "raw", "collector", stream):
            current /= part
            if current.is_symlink():
                raise ExportError(f"symlink in store manifest path: {current}")
        stream_root.mkdir(parents=True, exist_ok=True)
        target = stream_root / "manifest.json"
        checksum_target = stream_root / "manifest.sha256"
        if target.exists() or checksum_target.exists():
            raise ExportError(f"store manifest already exists: {target}")
        temporary = target.with_suffix(".json.tmp")
        checksum_temporary = checksum_target.with_suffix(".sha256.tmp")
        temporary.write_bytes(encoded)
        checksum_temporary.write_text(checksum + "\n", encoding="ascii")
        os.replace(temporary, target)
        os.replace(checksum_temporary, checksum_target)
        written.append(target)
    if not written:
        raise ExportError("export contains no replayable store manifests")
    return tuple(written)
