"""Fail-closed operational configuration and health contracts."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import cast

from hyperbot.build_info import code_commit, code_version
from hyperbot.services.public_collector import ALLOWED_CHANNELS

OPS_SCHEMA_VERSION = 3
DEPTH_CHANNELS = ("l2Book", "bbo", "trades")
BREADTH_CHANNELS = ("bbo", "trades")
_MARKET_PATTERN = re.compile(r"[A-Za-z0-9#][A-Za-z0-9:#._-]{0,127}")
_FORBIDDEN_SECRET_NAMES = (
    "HYPERBOT_PRIVATE_KEY",
    "HYPERBOT_SECRET_KEY",
    "TRIDENT_SECRET_KEY",
)


class OpsConfigurationError(ValueError):
    """Raised when server operation is ambiguous or unsafe."""


def _boolean(environment: Mapping[str, str], name: str, default: str) -> bool:
    raw = environment.get(name, default).strip().lower()
    if raw not in {"true", "false"}:
        raise OpsConfigurationError(f"{name} must be exactly true or false")
    return raw == "true"


def _integer(
    environment: Mapping[str, str],
    name: str,
    default: str,
    *,
    minimum: int,
) -> int:
    try:
        value = int(environment.get(name, default))
    except ValueError as exc:
        raise OpsConfigurationError(f"{name} must be an integer") from exc
    if value < minimum:
        raise OpsConfigurationError(f"{name} must be >= {minimum}")
    return value


def _float(
    environment: Mapping[str, str],
    name: str,
    default: str,
    *,
    minimum: float,
) -> float:
    try:
        value = float(environment.get(name, default))
    except ValueError as exc:
        raise OpsConfigurationError(f"{name} must be numeric") from exc
    if not math.isfinite(value) or value < minimum:
        raise OpsConfigurationError(f"{name} must be >= {minimum}")
    return value


def _csv(environment: Mapping[str, str], name: str, default: str) -> tuple[str, ...]:
    values = tuple(
        item.strip()
        for item in environment.get(name, default).split(",")
        if item.strip()
    )
    if len(values) != len(set(values)):
        raise OpsConfigurationError(f"{name} entries must be unique")
    return values


def _runtime_path(environment: Mapping[str, str], name: str, default: str) -> Path:
    value = environment.get(name, default).strip()
    if not value:
        raise OpsConfigurationError(f"{name} must not be empty")
    path = Path(value)
    if path == Path("/") or ".." in path.parts:
        raise OpsConfigurationError(f"{name} must be a narrow path without '..'")
    return path


def _mount_sentinel(
    environment: Mapping[str, str],
    *,
    variable: str,
    storage_root: Path,
    required: bool,
) -> Path | None:
    raw = environment.get(variable, "").strip()
    if not raw:
        if required:
            raise OpsConfigurationError(
                f"{variable} is required when storage is enabled"
            )
        return None
    sentinel = Path(raw)
    if sentinel == Path("/") or ".." in sentinel.parts:
        raise OpsConfigurationError(f"{variable} must be a narrow path without '..'")
    allowed_parents = {storage_root, *storage_root.parents} - {Path("/")}
    if sentinel.parent not in allowed_parents:
        raise OpsConfigurationError(f"{variable} must share the storage-root hierarchy")
    try:
        is_valid = (
            not sentinel.is_symlink()
            and sentinel.is_file()
            and sentinel.stat().st_size <= 4_096
        )
    except OSError as exc:
        raise OpsConfigurationError(f"cannot inspect {variable}") from exc
    if not is_valid:
        raise OpsConfigurationError(
            f"required HyperBot storage sentinel is missing or invalid: {variable}"
        )
    return sentinel


def _validate_markets(markets: tuple[str, ...]) -> None:
    invalid = [
        market for market in markets if _MARKET_PATTERN.fullmatch(market) is None
    ]
    if invalid:
        raise OpsConfigurationError(f"invalid collector markets: {sorted(invalid)}")


def _subscriptions(
    environment: Mapping[str, str],
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    tuple[tuple[str, str], ...],
]:
    profile_names = (
        "HYPERBOT_COLLECTOR_DEPTH_MARKETS",
        "HYPERBOT_COLLECTOR_BREADTH_MARKETS",
    )
    if any(name in environment for name in profile_names):
        legacy_names = (
            "HYPERBOT_COLLECTOR_MARKETS",
            "HYPERBOT_COLLECTOR_CHANNELS",
        )
        if any(environment.get(name, "").strip() for name in legacy_names):
            raise OpsConfigurationError(
                "collector profile and legacy subscription settings cannot be mixed"
            )
        depth_markets = _csv(
            environment,
            "HYPERBOT_COLLECTOR_DEPTH_MARKETS",
            "",
        )
        breadth_markets = _csv(
            environment,
            "HYPERBOT_COLLECTOR_BREADTH_MARKETS",
            "",
        )
        overlap = sorted(set(depth_markets) & set(breadth_markets))
        if overlap:
            raise OpsConfigurationError(
                f"collector depth/breadth markets overlap: {overlap}"
            )
        markets = (*depth_markets, *breadth_markets)
        if not markets:
            raise OpsConfigurationError(
                "at least one collector profile market is required"
            )
        _validate_markets(markets)
        subscriptions = tuple(
            (channel, market) for market in depth_markets for channel in DEPTH_CHANNELS
        ) + tuple(
            (channel, market)
            for market in breadth_markets
            for channel in BREADTH_CHANNELS
        )
        channels = tuple(dict.fromkeys(channel for channel, _ in subscriptions))
        return (
            markets,
            channels,
            depth_markets,
            breadth_markets,
            subscriptions,
        )

    markets = _csv(environment, "HYPERBOT_COLLECTOR_MARKETS", "BTC")
    if not markets:
        raise OpsConfigurationError("at least one collector market is required")
    _validate_markets(markets)
    channels = _csv(
        environment,
        "HYPERBOT_COLLECTOR_CHANNELS",
        ",".join(DEPTH_CHANNELS),
    )
    if not channels or not set(channels).issubset(ALLOWED_CHANNELS):
        raise OpsConfigurationError(
            "collector channels must be selected from l2Book,bbo,trades"
        )
    subscriptions = tuple(
        (channel, market) for market in markets for channel in channels
    )
    return markets, channels, (), (), subscriptions


@dataclass(frozen=True, slots=True)
class OpsSettings:
    """Public-only settings shared by collector and maintenance services."""

    collector_enabled: bool
    live_enabled: bool
    shadow_only: bool
    code_commit: str
    markets: tuple[str, ...]
    channels: tuple[str, ...]
    depth_markets: tuple[str, ...]
    breadth_markets: tuple[str, ...]
    subscriptions: tuple[tuple[str, str], ...]
    data_root: Path
    data_mount_sentinel: Path | None
    archive_enabled: bool
    archive_root: Path | None
    archive_mount_sentinel: Path | None
    hot_retention_days: int
    runtime_root: Path
    review_root: Path
    queue_capacity: int
    persistence_batch_size: int
    fsync_every_records: int
    heartbeat_interval_seconds: float
    stale_after_seconds: float
    reconnect_initial_seconds: float
    reconnect_max_seconds: float
    status_interval_seconds: float
    health_max_age_seconds: int
    minimum_free_bytes: int
    minimum_retention_days: int
    maintenance_hour_utc: int
    maintenance_minute_utc: int
    maintenance_poll_seconds: int

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        require_enabled: bool = False,
    ) -> OpsSettings:
        values = os.environ if environment is None else environment
        for name in _FORBIDDEN_SECRET_NAMES:
            if values.get(name, "").strip():
                raise OpsConfigurationError(
                    f"{name} must not be exposed to HyperBot public services"
                )

        collector_enabled = _boolean(values, "HYPERBOT_COLLECTOR_ENABLED", "false")
        live_enabled = _boolean(values, "HYPERBOT_LIVE_ENABLED", "false")
        shadow_only = _boolean(values, "HYPERBOT_SHADOW_ONLY", "true")
        if live_enabled:
            raise OpsConfigurationError(
                "live operation is not implemented or authorized"
            )
        if not shadow_only:
            raise OpsConfigurationError("server operation must remain shadow-only")
        if require_enabled and not collector_enabled:
            raise OpsConfigurationError(
                "collector activation requires HYPERBOT_COLLECTOR_ENABLED=true"
            )
        try:
            exact_code_commit = code_commit(
                values,
                required=require_enabled or collector_enabled,
            )
        except ValueError as exc:
            raise OpsConfigurationError(str(exc)) from exc

        (
            markets,
            channels,
            depth_markets,
            breadth_markets,
            subscriptions,
        ) = _subscriptions(values)

        heartbeat = _float(
            values,
            "HYPERBOT_HEARTBEAT_INTERVAL_SECONDS",
            "20",
            minimum=0.1,
        )
        stale = _float(
            values,
            "HYPERBOT_STALE_AFTER_SECONDS",
            "60",
            minimum=heartbeat,
        )
        reconnect_initial = _float(
            values,
            "HYPERBOT_RECONNECT_INITIAL_SECONDS",
            "0.25",
            minimum=0.01,
        )
        reconnect_max = _float(
            values,
            "HYPERBOT_RECONNECT_MAX_SECONDS",
            "15",
            minimum=reconnect_initial,
        )
        minimum_retention_days = _integer(
            values, "HYPERBOT_MINIMUM_RETENTION_DAYS", "60", minimum=30
        )
        hot_retention_days = _integer(
            values, "HYPERBOT_HOT_RETENTION_DAYS", "30", minimum=7
        )
        archive_enabled = _boolean(values, "HYPERBOT_ARCHIVE_ENABLED", "false")
        if archive_enabled and hot_retention_days >= minimum_retention_days:
            raise OpsConfigurationError(
                "HYPERBOT_HOT_RETENTION_DAYS must be below minimum retention"
            )
        hour = _integer(values, "HYPERBOT_MAINTENANCE_HOUR_UTC", "0", minimum=0)
        minute = _integer(values, "HYPERBOT_MAINTENANCE_MINUTE_UTC", "15", minimum=0)
        if hour > 23 or minute > 59:
            raise OpsConfigurationError("maintenance UTC time is invalid")

        data_root = _runtime_path(values, "HYPERBOT_DATA_ROOT", "data/raw/collector")
        archive_root = (
            _runtime_path(
                values,
                "HYPERBOT_ARCHIVE_ROOT",
                "/app/archive/collector",
            )
            if archive_enabled
            else None
        )
        if archive_root is not None:
            data_absolute = data_root.absolute()
            archive_absolute = archive_root.absolute()
            if (
                data_absolute == archive_absolute
                or data_absolute.is_relative_to(archive_absolute)
                or archive_absolute.is_relative_to(data_absolute)
            ):
                raise OpsConfigurationError(
                    "archive root must be separate from the hot data root"
                )
        settings = cls(
            collector_enabled=collector_enabled,
            live_enabled=live_enabled,
            shadow_only=shadow_only,
            code_commit=exact_code_commit,
            markets=markets,
            channels=channels,
            depth_markets=depth_markets,
            breadth_markets=breadth_markets,
            subscriptions=subscriptions,
            data_root=data_root,
            data_mount_sentinel=_mount_sentinel(
                values,
                variable="HYPERBOT_DATA_MOUNT_SENTINEL",
                storage_root=data_root,
                required=False,
            ),
            archive_enabled=archive_enabled,
            archive_root=archive_root,
            archive_mount_sentinel=(
                _mount_sentinel(
                    values,
                    variable="HYPERBOT_ARCHIVE_MOUNT_SENTINEL",
                    storage_root=archive_root,
                    required=True,
                )
                if archive_root is not None
                else None
            ),
            hot_retention_days=hot_retention_days,
            runtime_root=_runtime_path(values, "HYPERBOT_RUNTIME_ROOT", "runtime"),
            review_root=_runtime_path(values, "HYPERBOT_REVIEW_ROOT", "data/reviews"),
            queue_capacity=_integer(
                values, "HYPERBOT_QUEUE_CAPACITY", "10000", minimum=100
            ),
            persistence_batch_size=_integer(
                values,
                "HYPERBOT_PERSISTENCE_BATCH_SIZE",
                "256",
                minimum=1,
            ),
            fsync_every_records=_integer(
                values,
                "HYPERBOT_FSYNC_EVERY_RECORDS",
                "100",
                minimum=1,
            ),
            heartbeat_interval_seconds=heartbeat,
            stale_after_seconds=stale,
            reconnect_initial_seconds=reconnect_initial,
            reconnect_max_seconds=reconnect_max,
            status_interval_seconds=_float(
                values,
                "HYPERBOT_STATUS_INTERVAL_SECONDS",
                "10",
                minimum=0.1,
            ),
            health_max_age_seconds=_integer(
                values, "HYPERBOT_HEALTH_MAX_AGE_SECONDS", "90", minimum=10
            ),
            minimum_free_bytes=_integer(
                values,
                "HYPERBOT_MINIMUM_FREE_BYTES",
                str(10 * 1024 * 1024 * 1024),
                minimum=1,
            ),
            minimum_retention_days=minimum_retention_days,
            maintenance_hour_utc=hour,
            maintenance_minute_utc=minute,
            maintenance_poll_seconds=_integer(
                values, "HYPERBOT_MAINTENANCE_POLL_SECONDS", "60", minimum=10
            ),
        )
        if len(settings.subscriptions) > 1_000:
            raise OpsConfigurationError("configured subscriptions exceed 1,000")
        if settings.persistence_batch_size > 10_000:
            raise OpsConfigurationError(
                "HYPERBOT_PERSISTENCE_BATCH_SIZE must be <= 10000"
            )
        if settings.fsync_every_records > 1_000:
            raise OpsConfigurationError("HYPERBOT_FSYNC_EVERY_RECORDS must be <= 1000")
        return settings

    @property
    def status_path(self) -> Path:
        return self.runtime_root / "collector_status.json"

    @property
    def maintenance_status_path(self) -> Path:
        return self.runtime_root / "maintenance_status.json"

    @property
    def config_sha256(self) -> str:
        payload = asdict(self)
        payload.pop("code_commit")
        payload["data_root"] = str(self.data_root)
        payload["data_mount_sentinel"] = (
            str(self.data_mount_sentinel)
            if self.data_mount_sentinel is not None
            else None
        )
        payload["archive_root"] = (
            str(self.archive_root) if self.archive_root is not None else None
        )
        payload["archive_mount_sentinel"] = (
            str(self.archive_mount_sentinel)
            if self.archive_mount_sentinel is not None
            else None
        )
        payload["runtime_root"] = str(self.runtime_root)
        payload["review_root"] = str(self.review_root)
        encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        return hashlib.sha256(encoded).hexdigest()

    @property
    def code_version(self) -> str:
        return code_version(self.code_commit)


def atomic_write_json(path: Path, payload: Mapping[str, object]) -> None:
    """Atomically persist operational state without broad file permissions."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o640)
    try:
        view = memoryview(encoded)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(f"failed to write {temporary}")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, path)


@dataclass(frozen=True, slots=True)
class HealthResult:
    healthy: bool
    reasons: tuple[str, ...]
    checked_at_ms: int
    status_age_ms: int | None
    free_bytes: int
    config_sha256: str


def evaluate_collector_health(
    settings: OpsSettings,
    *,
    now_ms: int,
) -> HealthResult:
    """Evaluate a public collector status and disk reserve without exchange access."""

    reasons: list[str] = []
    status_age_ms: int | None = None
    try:
        raw = json.loads(settings.status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
        reasons.append("status_unavailable")
    if not isinstance(raw, dict):
        raw = {}
        reasons.append("status_invalid")
    status = cast(dict[str, object], raw)
    updated_at_ms = status.get("updated_at_ms")
    if isinstance(updated_at_ms, int):
        status_age_ms = max(now_ms - updated_at_ms, 0)
        if status_age_ms > settings.health_max_age_seconds * 1_000:
            reasons.append("status_stale")
    elif status:
        reasons.append("status_timestamp_invalid")
    if status.get("state") != "running":
        reasons.append("collector_not_running")
    if status.get("public_only") is not True:
        reasons.append("public_only_guard_missing")
    if status.get("live_enabled") is not False:
        reasons.append("live_guard_failed")
    if status.get("shadow_only") is not True:
        reasons.append("shadow_guard_failed")
    if status.get("code_commit") != settings.code_commit:
        reasons.append("code_commit_mismatch")
    if status.get("config_sha256") != settings.config_sha256:
        reasons.append("config_hash_mismatch")
    if status.get("connected") is not True:
        reasons.append("collector_disconnected")
    dropped_events = status.get("dropped_events")
    if not isinstance(dropped_events, int) or dropped_events > 0:
        reasons.append("collector_data_loss")
    last_message_ms = status.get("last_message_receive_ms")
    if not isinstance(last_message_ms, int) or (
        now_ms - last_message_ms > settings.health_max_age_seconds * 1_000
    ):
        reasons.append("public_feed_stale")
    elif last_message_ms > now_ms + 5_000:
        reasons.append("collector_clock_future")

    disk_target = settings.data_root
    disk_target.mkdir(parents=True, exist_ok=True)
    free_bytes = shutil.disk_usage(disk_target).free
    if free_bytes < settings.minimum_free_bytes:
        reasons.append("disk_reserve_low")
    if settings.archive_enabled:
        if settings.archive_root is None:
            reasons.append("archive_root_missing")
        else:
            archive_disk_target = (
                settings.archive_root
                if settings.archive_root.exists()
                else settings.archive_root.parent
            )
            if (
                shutil.disk_usage(archive_disk_target).free
                < settings.minimum_free_bytes
            ):
                reasons.append("archive_reserve_low")
    return HealthResult(
        healthy=not reasons,
        reasons=tuple(dict.fromkeys(reasons)),
        checked_at_ms=now_ms,
        status_age_ms=status_age_ms,
        free_bytes=free_bytes,
        config_sha256=settings.config_sha256,
    )
