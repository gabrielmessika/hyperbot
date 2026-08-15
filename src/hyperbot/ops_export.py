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
