"""Append-only JSONL storage for raw and derived HyperBot events."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from hyperbot.models import DomainEvent, JsonValue, event_payload, event_type

SCHEMA_VERSION = 1
_STREAM_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")


class EventStoreError(RuntimeError):
    """Base error raised by the event store."""


class EventIntegrityError(EventStoreError):
    """Raised when a stored record no longer matches its payload hash."""


@dataclass(frozen=True, slots=True)
class AppendResult:
    path: Path
    byte_offset: int
    bytes_written: int
    payload_sha256: str


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class JsonlEventStore:
    """Single-host append-only store with process-safe file locking."""

    def __init__(self, root: str | Path, *, fsync: bool = True) -> None:
        self.root = Path(root)
        self.fsync = fsync
        self.root.mkdir(parents=True, exist_ok=True)

    def _stream_path(self, stream: str) -> Path:
        if not _STREAM_PATTERN.fullmatch(stream):
            raise EventStoreError(f"invalid stream name: {stream!r}")
        return self.root / f"{stream}.jsonl"

    def append(self, stream: str, event: DomainEvent) -> AppendResult:
        """Append one checksummed event and return its durable location."""

        path = self._stream_path(stream)
        payload = event_payload(event)
        payload_bytes = _canonical_json(payload)
        payload_sha256 = hashlib.sha256(payload_bytes).hexdigest()
        envelope: dict[str, JsonValue] = {
            "schema_version": SCHEMA_VERSION,
            "event_type": event_type(event),
            "payload_sha256": payload_sha256,
            "payload": payload,
        }
        line = _canonical_json(envelope) + b"\n"

        descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_RDWR, 0o640)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            byte_offset = os.lseek(descriptor, 0, os.SEEK_END)
            view = memoryview(line)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise EventStoreError(f"failed to append event to {path}")
                view = view[written:]
            if self.fsync:
                os.fsync(descriptor)
        finally:
            os.close(descriptor)

        return AppendResult(
            path=path,
            byte_offset=byte_offset,
            bytes_written=len(line),
            payload_sha256=payload_sha256,
        )

    def iter_records(self, stream: str) -> Iterator[dict[str, object]]:
        """Yield records after validating schema and payload integrity."""

        path = self._stream_path(stream)
        if not path.exists():
            return
        with path.open("rb") as handle:
            for line_number, line in enumerate(handle, start=1):
                try:
                    decoded = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise EventIntegrityError(
                        f"invalid JSON at {path}:{line_number}"
                    ) from exc
                if not isinstance(decoded, dict):
                    raise EventIntegrityError(
                        f"record is not an object at {path}:{line_number}"
                    )
                record = cast(dict[str, object], decoded)
                if record.get("schema_version") != SCHEMA_VERSION:
                    raise EventIntegrityError(
                        f"unsupported schema at {path}:{line_number}"
                    )
                payload = record.get("payload")
                expected_hash = record.get("payload_sha256")
                if not isinstance(payload, dict) or not isinstance(expected_hash, str):
                    raise EventIntegrityError(
                        f"invalid envelope at {path}:{line_number}"
                    )
                actual_hash = hashlib.sha256(_canonical_json(payload)).hexdigest()
                if actual_hash != expected_hash:
                    raise EventIntegrityError(
                        f"payload hash mismatch at {path}:{line_number}"
                    )
                yield record

    def read_records(self, stream: str) -> list[dict[str, object]]:
        return list(self.iter_records(stream))
