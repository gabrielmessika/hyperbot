"""Append-only, hash-chained registry of every tested research variant."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from hyperbot.event_store import EventIntegrityError

VARIANT_JOURNAL_SCHEMA_VERSION = 1
GENESIS_HASH = "0" * 64


@dataclass(frozen=True, slots=True)
class ResearchVariant:
    variant_id: str
    created_at_ms: int
    hypothesis: str
    code_version: str
    config_sha256: str
    train_period: str
    calibration_period: str
    test_period: str
    metrics_json: str

    def __post_init__(self) -> None:
        for name, value in (
            ("variant_id", self.variant_id),
            ("hypothesis", self.hypothesis),
            ("code_version", self.code_version),
            ("train_period", self.train_period),
            ("calibration_period", self.calibration_period),
            ("test_period", self.test_period),
            ("metrics_json", self.metrics_json),
        ):
            if not value.strip():
                raise ValueError(f"{name} must not be empty")
        if self.created_at_ms < 0:
            raise ValueError("created_at_ms must be non-negative")
        if len(self.config_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.config_sha256
        ):
            raise ValueError("config_sha256 must be a lowercase SHA-256")
        decoded = json.loads(self.metrics_json)
        if not isinstance(decoded, dict):
            raise ValueError("metrics_json must encode an object")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _variant_id(record: dict[str, object]) -> str | None:
    variant = record.get("variant")
    if not isinstance(variant, dict):
        return None
    value = variant.get("variant_id")
    return value if isinstance(value, str) else None


class VariantJournal:
    def __init__(self, path: str | Path, *, fsync: bool = True) -> None:
        self.path = Path(path)
        self.fsync = fsync
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, variant: ResearchVariant) -> str:
        descriptor = os.open(
            self.path,
            os.O_APPEND | os.O_CREAT | os.O_RDWR,
            0o640,
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self._read_descriptor(descriptor)
            if any(
                _variant_id(record) == variant.variant_id for record in records
            ):
                raise ValueError(f"variant already exists: {variant.variant_id}")
            previous = records[-1]["record_sha256"] if records else GENESIS_HASH
            base = {
                "schema_version": VARIANT_JOURNAL_SCHEMA_VERSION,
                "previous_record_sha256": previous,
                "variant": asdict(variant),
            }
            record_hash = hashlib.sha256(_canonical(base)).hexdigest()
            line = _canonical({**base, "record_sha256": record_hash}) + b"\n"
            os.lseek(descriptor, 0, os.SEEK_END)
            os.write(descriptor, line)
            if self.fsync:
                os.fsync(descriptor)
            return record_hash
        finally:
            os.close(descriptor)

    def validate(self) -> int:
        if not self.path.exists():
            return 0
        descriptor = os.open(self.path, os.O_RDONLY)
        try:
            return len(self._read_descriptor(descriptor))
        finally:
            os.close(descriptor)

    def _read_descriptor(self, descriptor: int) -> list[dict[str, object]]:
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        content = b"".join(chunks)
        if content and not content.endswith(b"\n"):
            raise EventIntegrityError("variant journal has a partial final line")
        previous = GENESIS_HASH
        records: list[dict[str, object]] = []
        for line_number, line in enumerate(content.splitlines(), start=1):
            try:
                decoded = json.loads(line)
            except json.JSONDecodeError as exc:
                raise EventIntegrityError(
                    f"invalid variant journal JSON at line {line_number}"
                ) from exc
            if not isinstance(decoded, dict):
                raise EventIntegrityError("variant journal record is not an object")
            record = dict(decoded)
            record_hash = record.pop("record_sha256", None)
            if record.get("schema_version") != VARIANT_JOURNAL_SCHEMA_VERSION:
                raise EventIntegrityError("variant journal schema mismatch")
            if record.get("previous_record_sha256") != previous:
                raise EventIntegrityError("variant journal chain mismatch")
            if not isinstance(record_hash, str) or hashlib.sha256(
                _canonical(record)
            ).hexdigest() != record_hash:
                raise EventIntegrityError("variant journal hash mismatch")
            variant = record.get("variant")
            if not isinstance(variant, dict):
                raise EventIntegrityError("variant journal payload is invalid")
            previous = record_hash
            records.append({**record, "record_sha256": record_hash})
        return records
