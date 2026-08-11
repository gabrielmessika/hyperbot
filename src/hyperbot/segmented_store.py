"""Segmented append-only event store with chained integrity manifests."""

from __future__ import annotations

import fcntl
import gzip
import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import cast

from hyperbot.event_store import AppendResult, EventIntegrityError, EventStoreError
from hyperbot.models import DomainEvent, JsonValue, event_payload, event_type

SEGMENT_SCHEMA_VERSION = 2
MANIFEST_SCHEMA_VERSION = 1
GENESIS_HASH = "0" * 64
_STREAM_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_ACTIVE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}-\d{6}\.jsonl\.open$")


@dataclass(frozen=True, slots=True)
class ValidationResult:
    stream: str
    segment_count: int
    record_count: int
    last_record_sha256: str


@dataclass(frozen=True, slots=True)
class _ScanResult:
    count: int
    first_sequence: int | None
    last_sequence: int | None
    begin_recorded_at_ms: int | None
    end_recorded_at_ms: int | None
    first_record_sha256: str | None
    last_record_sha256: str


@dataclass(frozen=True, slots=True)
class _ActiveAppendState:
    path: Path
    size: int
    mtime_ns: int
    ctime_ns: int
    device: int
    inode: int
    previous_hash: str
    next_sequence: int
    manifest_segment_count: int
    manifest_last_record_hash: str


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc_date(timestamp_ms: int) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC).date().isoformat()


def _encoded_event_line(
    stream: str,
    event: DomainEvent,
    *,
    recorded_at_ms: int,
    sequence: int,
    previous_hash: str,
) -> tuple[str, str, bytes]:
    payload = event_payload(event)
    payload_sha256 = _sha256(_canonical_json(payload))
    base_record: dict[str, JsonValue] = {
        "schema_version": SEGMENT_SCHEMA_VERSION,
        "stream": stream,
        "sequence": sequence,
        "recorded_at_ms": recorded_at_ms,
        "previous_record_sha256": previous_hash,
        "event_type": event_type(event),
        "payload_sha256": payload_sha256,
        "payload": payload,
    }
    record_sha256 = _sha256(_canonical_json(base_record))
    record = {**base_record, "record_sha256": record_sha256}
    return payload_sha256, record_sha256, _canonical_json(record) + b"\n"


class SegmentedEventStore:
    """Rotate, validate, recover, and compress hash-chained JSONL segments."""

    def __init__(
        self,
        root: str | Path,
        *,
        max_segment_bytes: int = 128 * 1024 * 1024,
        fsync: bool = True,
        fsync_every_records: int = 1,
        always_fsync_streams: frozenset[str] = frozenset(),
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        if max_segment_bytes <= 0:
            raise ValueError("max_segment_bytes must be positive")
        if fsync_every_records <= 0:
            raise ValueError("fsync_every_records must be positive")
        invalid_sync_streams = [
            stream
            for stream in always_fsync_streams
            if _STREAM_PATTERN.fullmatch(stream) is None
        ]
        if invalid_sync_streams:
            raise ValueError("always_fsync_streams contains an invalid stream")
        self.root = Path(root)
        self.max_segment_bytes = max_segment_bytes
        self.fsync = fsync
        self.fsync_every_records = fsync_every_records
        self.always_fsync_streams = always_fsync_streams
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._append_states: dict[str, _ActiveAppendState] = {}
        self._pending_fsync_records: dict[str, int] = {}
        self.root.mkdir(parents=True, exist_ok=True)

    def append(self, stream: str, event: DomainEvent) -> AppendResult:
        return self.append_many(stream, (event,))[0]

    def append_many(
        self,
        stream: str,
        events: Sequence[DomainEvent],
    ) -> tuple[AppendResult, ...]:
        """Append a bounded event batch under one lock and file descriptor."""

        if not events:
            return ()
        stream_dir = self._stream_dir(stream)
        stream_dir.mkdir(parents=True, exist_ok=True)
        results: list[AppendResult] = []
        with self._lock(stream_dir):
            manifest = self._load_manifest(stream)
            cached = self._cached_append_position(stream, manifest)
            if cached is None:
                previous_hash, next_sequence, active_path, active_scan = (
                    self._recover_active(stream, manifest)
                )
                active_count = active_scan.count
            else:
                previous_hash, next_sequence, active_path, active_count = cached
            active_size = active_path.stat().st_size if active_path else 0
            descriptor: int | None = None
            descriptor_path: Path | None = None
            try:
                for event in events:
                    now_ms = self.clock_ms()
                    if now_ms < 0:
                        raise EventStoreError("clock returned a negative timestamp")
                    date = _utc_date(now_ms)
                    payload_sha256, record_sha256, line = _encoded_event_line(
                        stream,
                        event,
                        recorded_at_ms=now_ms,
                        sequence=next_sequence,
                        previous_hash=previous_hash,
                    )
                    must_rotate = (
                        active_path is not None
                        and active_count > 0
                        and (
                            not active_path.name.startswith(date)
                            or active_size + len(line) > self.max_segment_bytes
                        )
                    )
                    if must_rotate and active_path is not None:
                        if descriptor is not None:
                            os.close(descriptor)
                            descriptor = None
                            descriptor_path = None
                        manifest = self._finalize_active(
                            stream,
                            manifest,
                            active_path,
                        )
                        self._append_states.pop(stream, None)
                        self._pending_fsync_records.pop(stream, None)
                        active_path = None
                        active_size = 0
                        active_count = 0
                        previous_hash = self._manifest_last_record_hash(manifest)
                        next_sequence = self._manifest_next_sequence(manifest)
                        payload_sha256, record_sha256, line = _encoded_event_line(
                            stream,
                            event,
                            recorded_at_ms=now_ms,
                            sequence=next_sequence,
                            previous_hash=previous_hash,
                        )

                    if active_path is None:
                        ordinal = len(self._manifest_segments(manifest)) + 1
                        active_path = stream_dir / f"{date}-{ordinal:06d}.jsonl.open"

                    if descriptor is None or descriptor_path != active_path:
                        if descriptor is not None:
                            os.close(descriptor)
                        descriptor = os.open(
                            active_path,
                            os.O_APPEND | os.O_CREAT | os.O_RDWR,
                            0o640,
                        )
                        descriptor_path = active_path
                        active_size = os.lseek(descriptor, 0, os.SEEK_END)

                    byte_offset = active_size
                    view = memoryview(line)
                    while view:
                        written = os.write(descriptor, view)
                        if written <= 0:
                            raise EventStoreError(
                                f"failed to append event to {active_path}"
                            )
                        view = view[written:]
                    active_size += len(line)
                    active_count += 1
                    previous_hash = record_sha256
                    next_sequence += 1
                    pending_fsync = self._pending_fsync_records.get(stream, 0) + 1
                    should_fsync = (
                        stream in self.always_fsync_streams
                        or pending_fsync >= self.fsync_every_records
                    )
                    if self.fsync and should_fsync:
                        os.fsync(descriptor)
                        pending_fsync = 0
                    self._pending_fsync_records[stream] = pending_fsync
                    results.append(
                        AppendResult(
                            path=active_path,
                            byte_offset=byte_offset,
                            bytes_written=len(line),
                            payload_sha256=payload_sha256,
                        )
                    )
            finally:
                if descriptor is not None:
                    os.close(descriptor)
            if active_path is None:
                raise EventStoreError("event batch produced no active segment")
            stat = active_path.stat()
            self._append_states[stream] = _ActiveAppendState(
                path=active_path,
                size=stat.st_size,
                mtime_ns=stat.st_mtime_ns,
                ctime_ns=stat.st_ctime_ns,
                device=stat.st_dev,
                inode=stat.st_ino,
                previous_hash=previous_hash,
                next_sequence=next_sequence,
                manifest_segment_count=len(self._manifest_segments(manifest)),
                manifest_last_record_hash=self._manifest_last_record_hash(manifest),
            )
        return tuple(results)

    def close(self, stream: str | None = None) -> None:
        streams = [stream] if stream is not None else self._known_streams()
        for current_stream in streams:
            stream_dir = self._stream_dir(current_stream)
            if not stream_dir.exists():
                continue
            with self._lock(stream_dir):
                manifest = self._load_manifest(current_stream)
                _, _, active_path, _ = self._recover_active(
                    current_stream,
                    manifest,
                )
                if active_path is not None and active_path.stat().st_size > 0:
                    self._finalize_active(current_stream, manifest, active_path)
                self._append_states.pop(current_stream, None)
                self._pending_fsync_records.pop(current_stream, None)

    def validate(self, stream: str) -> ValidationResult:
        stream_dir = self._stream_dir(stream)
        if not stream_dir.exists():
            return ValidationResult(stream, 0, 0, GENESIS_HASH)
        with self._lock(stream_dir, exclusive=False):
            manifest = self._load_manifest(stream)
            previous_record_hash = GENESIS_HASH
            previous_content_hash = GENESIS_HASH
            expected_sequence = 0
            record_count = 0
            segments = self._manifest_segments(manifest)
            for segment_number, segment in enumerate(segments, start=1):
                path_name = segment.get("path")
                if not isinstance(path_name, str):
                    raise EventIntegrityError("segment manifest path is invalid")
                path = stream_dir / path_name
                if not path.is_file():
                    raise EventIntegrityError(f"missing segment: {path}")
                stored = path.read_bytes()
                expected_storage_hash = segment.get("storage_sha256")
                if (
                    not isinstance(expected_storage_hash, str)
                    or _sha256(stored) != expected_storage_hash
                ):
                    raise EventIntegrityError(f"segment checksum mismatch: {path}")
                content = gzip.decompress(stored) if path.suffix == ".gz" else stored
                expected_content_hash = segment.get("content_sha256")
                if (
                    not isinstance(expected_content_hash, str)
                    or _sha256(content) != expected_content_hash
                ):
                    raise EventIntegrityError(f"segment content mismatch: {path}")
                if segment.get("previous_segment_sha256") != previous_content_hash:
                    raise EventIntegrityError(
                        f"segment chain mismatch at segment {segment_number}"
                    )
                scan = self._scan_content(
                    content,
                    stream=stream,
                    previous_hash=previous_record_hash,
                    expected_sequence=expected_sequence,
                    path=path,
                )
                if segment.get("record_count") != scan.count:
                    raise EventIntegrityError(f"record count mismatch: {path}")
                if segment.get("first_sequence") != scan.first_sequence:
                    raise EventIntegrityError(f"first sequence mismatch: {path}")
                if segment.get("last_sequence") != scan.last_sequence:
                    raise EventIntegrityError(f"last sequence mismatch: {path}")
                if segment.get("begin_recorded_at_ms") != scan.begin_recorded_at_ms:
                    raise EventIntegrityError(f"begin timestamp mismatch: {path}")
                if segment.get("end_recorded_at_ms") != scan.end_recorded_at_ms:
                    raise EventIntegrityError(f"end timestamp mismatch: {path}")
                if segment.get("first_record_sha256") != scan.first_record_sha256:
                    raise EventIntegrityError(f"first record hash mismatch: {path}")
                if segment.get("last_record_sha256") != scan.last_record_sha256:
                    raise EventIntegrityError(f"last record hash mismatch: {path}")
                previous_record_hash = scan.last_record_sha256
                previous_content_hash = expected_content_hash
                expected_sequence += scan.count
                record_count += scan.count

            _, _, active_path, active_scan = self._inspect_active(
                stream,
                manifest,
                recover_partial=False,
            )
            if active_path is not None:
                record_count += active_scan.count
                previous_record_hash = active_scan.last_record_sha256
            return ValidationResult(
                stream,
                len(segments) + (1 if active_path is not None else 0),
                record_count,
                previous_record_hash,
            )

    def iter_records(self, stream: str) -> Iterator[dict[str, object]]:
        self.validate(stream)
        stream_dir = self._stream_dir(stream)
        if not stream_dir.exists():
            return
        manifest = self._load_manifest(stream)
        for segment in self._manifest_segments(manifest):
            path = stream_dir / cast(str, segment["path"])
            stored = path.read_bytes()
            content = gzip.decompress(stored) if path.suffix == ".gz" else stored
            yield from self._decode_records(content)
        active = self._active_paths(stream_dir)
        if active:
            yield from self._decode_records(active[0].read_bytes())

    def iter_records_for_utc_date(
        self,
        stream: str,
        utc_date: str,
    ) -> Iterator[dict[str, object]]:
        """Read and verify immutable closed segments for one UTC date only."""

        try:
            parsed_date = date.fromisoformat(utc_date)
        except ValueError as exc:
            raise EventStoreError(f"invalid UTC date: {utc_date!r}") from exc
        if parsed_date.isoformat() != utc_date:
            raise EventStoreError(f"invalid UTC date: {utc_date!r}")
        stream_dir = self._stream_dir(stream)
        if not stream_dir.exists():
            return
        with self._lock(stream_dir, exclusive=False):
            manifest = self._load_manifest(stream)
            segments = [dict(item) for item in self._manifest_segments(manifest)]
            active = self._active_paths(stream_dir)
            if active and active[0].name.startswith(utc_date):
                raise EventIntegrityError(
                    f"UTC date {utc_date} still has an active segment for {stream}"
                )

        for index, segment in enumerate(segments):
            if segment.get("utc_date") != utc_date:
                continue
            path_name = segment.get("path")
            if not isinstance(path_name, str):
                raise EventIntegrityError("segment manifest path is invalid")
            path = stream_dir / path_name
            if not path.is_file():
                raise EventIntegrityError(f"missing segment: {path}")
            stored = path.read_bytes()
            storage_hash = segment.get("storage_sha256")
            if not isinstance(storage_hash, str) or _sha256(stored) != storage_hash:
                raise EventIntegrityError(f"segment checksum mismatch: {path}")
            content = gzip.decompress(stored) if path.suffix == ".gz" else stored
            content_hash = segment.get("content_sha256")
            if not isinstance(content_hash, str) or _sha256(content) != content_hash:
                raise EventIntegrityError(f"segment content mismatch: {path}")
            previous_record_hash: object = (
                GENESIS_HASH
                if index == 0
                else segments[index - 1].get("last_record_sha256")
            )
            if not isinstance(previous_record_hash, str):
                raise EventIntegrityError(f"previous record hash is invalid: {path}")
            previous_content_hash: object = (
                GENESIS_HASH
                if index == 0
                else segments[index - 1].get("content_sha256")
            )
            if segment.get("previous_segment_sha256") != previous_content_hash:
                raise EventIntegrityError(f"segment chain mismatch: {path}")
            expected_sequence = segment.get("first_sequence")
            if not isinstance(expected_sequence, int):
                raise EventIntegrityError(f"first sequence is invalid: {path}")
            scan = self._scan_content(
                content,
                stream=stream,
                previous_hash=previous_record_hash,
                expected_sequence=expected_sequence,
                path=path,
            )
            expected_fields = {
                "record_count": scan.count,
                "first_sequence": scan.first_sequence,
                "last_sequence": scan.last_sequence,
                "begin_recorded_at_ms": scan.begin_recorded_at_ms,
                "end_recorded_at_ms": scan.end_recorded_at_ms,
                "first_record_sha256": scan.first_record_sha256,
                "last_record_sha256": scan.last_record_sha256,
            }
            for field, expected in expected_fields.items():
                if segment.get(field) != expected:
                    raise EventIntegrityError(f"{field} mismatch: {path}")
            yield from self._decode_records(content)

    def read_records(self, stream: str) -> list[dict[str, object]]:
        return list(self.iter_records(stream))

    def compress_closed_segments(self, stream: str) -> int:
        stream_dir = self._stream_dir(stream)
        if not stream_dir.exists():
            return 0
        self.validate(stream)
        with self._lock(stream_dir):
            manifest = self._load_manifest(stream)
            segments = self._manifest_segments(manifest)
            compressed = 0
            for index, segment in enumerate(segments):
                path = stream_dir / cast(str, segment["path"])
                if path.suffix == ".gz":
                    continue
                content = path.read_bytes()
                if _sha256(content) != segment.get("content_sha256"):
                    raise EventIntegrityError(
                        f"cannot compress invalid segment: {path}"
                    )
                gzip_path = path.with_suffix(path.suffix + ".gz")
                temporary = gzip_path.with_suffix(gzip_path.suffix + ".tmp")
                with temporary.open("wb") as raw_handle:
                    with gzip.GzipFile(
                        filename="",
                        mode="wb",
                        fileobj=raw_handle,
                        mtime=0,
                    ) as gzip_handle:
                        gzip_handle.write(content)
                    raw_handle.flush()
                    if self.fsync:
                        os.fsync(raw_handle.fileno())
                compressed_bytes = temporary.read_bytes()
                if gzip.decompress(compressed_bytes) != content:
                    temporary.unlink(missing_ok=True)
                    raise EventIntegrityError(f"gzip verification failed: {path}")
                os.replace(temporary, gzip_path)
                updated = dict(segment)
                updated["path"] = gzip_path.name
                updated["storage_sha256"] = _sha256(compressed_bytes)
                updated["compression"] = "gzip"
                segments[index] = updated
                manifest["segments"] = cast(JsonValue, segments)
                self._write_manifest(stream, manifest)
                path.unlink()
                compressed += 1
        self.validate(stream)
        return compressed

    def _stream_dir(self, stream: str) -> Path:
        if not _STREAM_PATTERN.fullmatch(stream):
            raise EventStoreError(f"invalid stream name: {stream!r}")
        return self.root / stream

    def _known_streams(self) -> list[str]:
        return sorted(
            entry.name
            for entry in self.root.iterdir()
            if entry.is_dir() and _STREAM_PATTERN.fullmatch(entry.name)
        )

    def _lock(self, stream_dir: Path, *, exclusive: bool = True) -> _FileLock:
        return _FileLock(stream_dir / ".lock", exclusive=exclusive)

    def _manifest_path(self, stream: str) -> Path:
        return self._stream_dir(stream) / "manifest.json"

    def _new_manifest(self, stream: str) -> dict[str, JsonValue]:
        config: dict[str, JsonValue] = {
            "max_segment_bytes": self.max_segment_bytes,
            "record_schema_version": SEGMENT_SCHEMA_VERSION,
        }
        return {
            "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
            "stream": stream,
            "config": config,
            "config_sha256": _sha256(_canonical_json(config)),
            "segments": [],
        }

    def _load_manifest(self, stream: str) -> dict[str, JsonValue]:
        path = self._manifest_path(stream)
        checksum_path = path.with_suffix(".sha256")
        if not path.exists():
            if checksum_path.exists():
                raise EventIntegrityError(f"manifest missing for {stream}")
            return self._new_manifest(stream)
        if not checksum_path.is_file():
            raise EventIntegrityError(f"manifest checksum missing for {stream}")
        raw = path.read_bytes()
        if checksum_path.read_text(encoding="ascii").strip() != _sha256(raw):
            raise EventIntegrityError(f"manifest checksum mismatch for {stream}")
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise EventIntegrityError(f"invalid manifest JSON for {stream}") from exc
        if not isinstance(decoded, dict):
            raise EventIntegrityError(f"invalid manifest for {stream}")
        manifest = cast(dict[str, JsonValue], decoded)
        if (
            manifest.get("manifest_schema_version") != MANIFEST_SCHEMA_VERSION
            or manifest.get("stream") != stream
        ):
            raise EventIntegrityError(f"unsupported manifest for {stream}")
        config = manifest.get("config")
        if not isinstance(config, dict) or manifest.get("config_sha256") != _sha256(
            _canonical_json(config)
        ):
            raise EventIntegrityError(f"manifest configuration mismatch for {stream}")
        return manifest

    def _write_manifest(self, stream: str, manifest: Mapping[str, JsonValue]) -> None:
        path = self._manifest_path(stream)
        raw = _canonical_json(manifest) + b"\n"
        temporary = path.with_suffix(".json.tmp")
        checksum_path = path.with_suffix(".sha256")
        checksum_temporary = checksum_path.with_suffix(".sha256.tmp")
        self._write_file(temporary, raw)
        self._write_file(checksum_temporary, (_sha256(raw) + "\n").encode("ascii"))
        os.replace(temporary, path)
        os.replace(checksum_temporary, checksum_path)

    def _write_file(self, path: Path, content: bytes) -> None:
        descriptor = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o640)
        try:
            view = memoryview(content)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise EventStoreError(f"failed to write {path}")
                view = view[written:]
            if self.fsync:
                os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _manifest_segments(
        self,
        manifest: Mapping[str, JsonValue],
    ) -> list[dict[str, JsonValue]]:
        raw_segments = manifest.get("segments")
        if not isinstance(raw_segments, list):
            raise EventIntegrityError("manifest segments are invalid")
        segments: list[dict[str, JsonValue]] = []
        for raw_segment in raw_segments:
            if not isinstance(raw_segment, dict):
                raise EventIntegrityError("manifest segment entry is invalid")
            segments.append(raw_segment)
        return segments

    def _manifest_last_record_hash(self, manifest: Mapping[str, JsonValue]) -> str:
        segments = self._manifest_segments(manifest)
        if not segments:
            return GENESIS_HASH
        value = segments[-1].get("last_record_sha256")
        if not isinstance(value, str):
            raise EventIntegrityError("manifest last record hash is invalid")
        return value

    def _manifest_next_sequence(self, manifest: Mapping[str, JsonValue]) -> int:
        segments = self._manifest_segments(manifest)
        if not segments:
            return 0
        value = segments[-1].get("last_sequence")
        if not isinstance(value, int):
            raise EventIntegrityError("manifest last sequence is invalid")
        return value + 1

    def _active_paths(self, stream_dir: Path) -> list[Path]:
        active = sorted(
            path
            for path in stream_dir.iterdir()
            if path.is_file() and _ACTIVE_PATTERN.fullmatch(path.name)
        )
        if len(active) > 1:
            raise EventIntegrityError(f"multiple active segments in {stream_dir}")
        return active

    def _cached_append_position(
        self,
        stream: str,
        manifest: Mapping[str, JsonValue],
    ) -> tuple[str, int, Path, int] | None:
        state = self._append_states.get(stream)
        if state is None:
            return None
        try:
            active_paths = self._active_paths(self._stream_dir(stream))
            stat = state.path.stat()
        except OSError:
            self._append_states.pop(stream, None)
            self._pending_fsync_records.pop(stream, None)
            return None
        manifest_segment_count = len(self._manifest_segments(manifest))
        manifest_last_hash = self._manifest_last_record_hash(manifest)
        manifest_next_sequence = self._manifest_next_sequence(manifest)
        unchanged = (
            active_paths == [state.path]
            and stat.st_size == state.size
            and stat.st_mtime_ns == state.mtime_ns
            and stat.st_ctime_ns == state.ctime_ns
            and stat.st_dev == state.device
            and stat.st_ino == state.inode
            and manifest_segment_count == state.manifest_segment_count
            and manifest_last_hash == state.manifest_last_record_hash
            and state.next_sequence > manifest_next_sequence
        )
        if not unchanged:
            self._append_states.pop(stream, None)
            self._pending_fsync_records.pop(stream, None)
            return None
        return (
            state.previous_hash,
            state.next_sequence,
            state.path,
            state.next_sequence - manifest_next_sequence,
        )

    def _recover_active(
        self,
        stream: str,
        manifest: dict[str, JsonValue],
    ) -> tuple[str, int, Path | None, _ScanResult]:
        return self._inspect_active(
            stream,
            manifest,
            recover_partial=True,
        )

    def _inspect_active(
        self,
        stream: str,
        manifest: Mapping[str, JsonValue],
        *,
        recover_partial: bool,
    ) -> tuple[str, int, Path | None, _ScanResult]:
        previous_hash = self._manifest_last_record_hash(manifest)
        expected_sequence = self._manifest_next_sequence(manifest)
        active_paths = self._active_paths(self._stream_dir(stream))
        empty_scan = _ScanResult(0, None, None, None, None, None, previous_hash)
        if not active_paths:
            return previous_hash, expected_sequence, None, empty_scan
        path = active_paths[0]
        content = path.read_bytes()
        if content and not content.endswith(b"\n"):
            if not recover_partial:
                raise EventIntegrityError(f"partial final line in {path}")
            valid_end = content.rfind(b"\n") + 1
            with path.open("r+b") as handle:
                handle.truncate(valid_end)
                if self.fsync:
                    handle.flush()
                    os.fsync(handle.fileno())
            content = content[:valid_end]
        scan = self._scan_content(
            content,
            stream=stream,
            previous_hash=previous_hash,
            expected_sequence=expected_sequence,
            path=path,
        )
        return (
            scan.last_record_sha256,
            expected_sequence + scan.count,
            path,
            scan,
        )

    def _scan_content(
        self,
        content: bytes,
        *,
        stream: str,
        previous_hash: str,
        expected_sequence: int,
        path: Path,
    ) -> _ScanResult:
        if not content:
            return _ScanResult(0, None, None, None, None, None, previous_hash)
        if not content.endswith(b"\n"):
            raise EventIntegrityError(f"partial final line in {path}")
        current_previous = previous_hash
        first_record_hash: str | None = None
        begin_recorded_at_ms: int | None = None
        end_recorded_at_ms: int | None = None
        count = 0
        for line_number, line in enumerate(content.splitlines(), start=1):
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EventIntegrityError(
                    f"invalid JSON at {path}:{line_number}"
                ) from exc
            if not isinstance(decoded, dict):
                raise EventIntegrityError(f"invalid record at {path}:{line_number}")
            record = cast(dict[str, object], decoded)
            if record.get("schema_version") != SEGMENT_SCHEMA_VERSION:
                raise EventIntegrityError(f"invalid schema at {path}:{line_number}")
            if record.get("stream") != stream:
                raise EventIntegrityError(f"stream mismatch at {path}:{line_number}")
            if record.get("sequence") != expected_sequence + count:
                raise EventIntegrityError(f"sequence gap at {path}:{line_number}")
            recorded_at_ms = record.get("recorded_at_ms")
            if not isinstance(recorded_at_ms, int) or recorded_at_ms < 0:
                raise EventIntegrityError(
                    f"record timestamp is invalid at {path}:{line_number}"
                )
            if record.get("previous_record_sha256") != current_previous:
                raise EventIntegrityError(
                    f"record chain mismatch at {path}:{line_number}"
                )
            payload = record.get("payload")
            payload_hash = record.get("payload_sha256")
            if not isinstance(payload, dict) or not isinstance(payload_hash, str):
                raise EventIntegrityError(f"invalid payload at {path}:{line_number}")
            if _sha256(_canonical_json(payload)) != payload_hash:
                raise EventIntegrityError(
                    f"payload hash mismatch at {path}:{line_number}"
                )
            record_hash = record.pop("record_sha256", None)
            if (
                not isinstance(record_hash, str)
                or _sha256(_canonical_json(record)) != record_hash
            ):
                raise EventIntegrityError(
                    f"record hash mismatch at {path}:{line_number}"
                )
            if first_record_hash is None:
                first_record_hash = record_hash
                begin_recorded_at_ms = recorded_at_ms
            end_recorded_at_ms = recorded_at_ms
            current_previous = record_hash
            count += 1
        return _ScanResult(
            count=count,
            first_sequence=expected_sequence,
            last_sequence=expected_sequence + count - 1,
            begin_recorded_at_ms=begin_recorded_at_ms,
            end_recorded_at_ms=end_recorded_at_ms,
            first_record_sha256=first_record_hash,
            last_record_sha256=current_previous,
        )

    def _finalize_active(
        self,
        stream: str,
        manifest: dict[str, JsonValue],
        active_path: Path,
    ) -> dict[str, JsonValue]:
        previous_record_hash = self._manifest_last_record_hash(manifest)
        expected_sequence = self._manifest_next_sequence(manifest)
        content = active_path.read_bytes()
        scan = self._scan_content(
            content,
            stream=stream,
            previous_hash=previous_record_hash,
            expected_sequence=expected_sequence,
            path=active_path,
        )
        if scan.count == 0:
            return manifest
        closed_path = active_path.with_suffix("")
        os.replace(active_path, closed_path)
        if self.fsync:
            descriptor = os.open(closed_path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        content_hash = _sha256(content)
        segments = self._manifest_segments(manifest)
        previous_segment_hash = (
            cast(str, segments[-1]["content_sha256"]) if segments else GENESIS_HASH
        )
        segments.append(
            {
                "path": closed_path.name,
                "utc_date": closed_path.name[:10],
                "first_sequence": scan.first_sequence,
                "last_sequence": scan.last_sequence,
                "begin_recorded_at_ms": scan.begin_recorded_at_ms,
                "end_recorded_at_ms": scan.end_recorded_at_ms,
                "record_count": scan.count,
                "first_record_sha256": scan.first_record_sha256,
                "last_record_sha256": scan.last_record_sha256,
                "previous_segment_sha256": previous_segment_hash,
                "content_sha256": content_hash,
                "storage_sha256": content_hash,
                "compression": "none",
            }
        )
        manifest["segments"] = cast(JsonValue, segments)
        self._write_manifest(stream, manifest)
        return manifest

    def _decode_records(self, content: bytes) -> Iterator[dict[str, object]]:
        for line in content.splitlines():
            decoded = json.loads(line)
            yield cast(dict[str, object], decoded)


class _FileLock:
    def __init__(self, path: Path, *, exclusive: bool) -> None:
        self.path = path
        self.exclusive = exclusive
        self.descriptor: int | None = None

    def __enter__(self) -> _FileLock:
        self.descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o640)
        operation = fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH
        fcntl.flock(self.descriptor, operation)
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        if self.descriptor is not None:
            os.close(self.descriptor)
            self.descriptor = None
