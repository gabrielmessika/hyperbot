"""Read-only observability snapshots for the HyperBot API and dashboard."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import shutil
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

from hyperbot.ops import (
    HealthResult,
    OpsConfigurationError,
    OpsSettings,
    evaluate_collector_health,
)
from hyperbot.quality import QUALITY_SCHEMA_VERSION

MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_HISTORY_DAYS = 90
MAINTENANCE_STALE_MINIMUM_MS = 5 * 60 * 1_000
MAINTENANCE_COMPLETION_GRACE_MS = 60 * 60 * 1_000


@dataclass(frozen=True, slots=True)
class ObserverSettings:
    """Network and authentication settings for the read-only observer."""

    host: str
    port: int
    auth_required: bool
    auth_username: str | None
    auth_password: str | None
    refresh_seconds: int
    ops: OpsSettings

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> ObserverSettings:
        values = os.environ if environment is None else environment
        host = values.get("HYPERBOT_UI_HOST", "0.0.0.0").strip()
        try:
            address = ipaddress.ip_address(host)
        except ValueError as exc:
            raise OpsConfigurationError(
                "HYPERBOT_UI_HOST must be an explicit IP address"
            ) from exc
        if address.version != 4:
            raise OpsConfigurationError("HYPERBOT_UI_HOST must be an IPv4 address")
        try:
            port = int(values.get("HYPERBOT_UI_PORT", "3002"))
            refresh_seconds = int(values.get("HYPERBOT_UI_REFRESH_SECONDS", "10"))
        except ValueError as exc:
            raise OpsConfigurationError("UI port and refresh must be integers") from exc
        if not 1 <= port <= 65_535:
            raise OpsConfigurationError("HYPERBOT_UI_PORT is outside 1..65535")
        if not 5 <= refresh_seconds <= 300:
            raise OpsConfigurationError(
                "HYPERBOT_UI_REFRESH_SECONDS must be between 5 and 300"
            )
        auth_required = _boolean(values, "HYPERBOT_UI_AUTH_REQUIRED", "true")
        username = values.get("HYPERBOT_UI_AUTH_USERNAME", "").strip() or None
        password_path = values.get("HYPERBOT_UI_AUTH_PASSWORD_FILE", "").strip()
        password: str | None = None
        if password_path:
            resolved_password_path = Path(password_path)
            try:
                if (
                    resolved_password_path.is_symlink()
                    or not resolved_password_path.is_file()
                    or resolved_password_path.stat().st_size > 4_096
                ):
                    raise OpsConfigurationError(
                        "UI password file must be a small regular file"
                    )
                password = resolved_password_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise OpsConfigurationError(
                    "cannot read HYPERBOT_UI_AUTH_PASSWORD_FILE"
                ) from exc
            password = password or None
        if auth_required and (username is None or password is None):
            raise OpsConfigurationError(
                "public UI authentication requires username and password file"
            )
        if not address.is_loopback and not auth_required:
            raise OpsConfigurationError(
                "authentication cannot be disabled on a public UI bind"
            )
        if password is not None and len(password) < 16:
            raise OpsConfigurationError(
                "UI password must contain at least 16 characters"
            )
        return cls(
            host=host,
            port=port,
            auth_required=auth_required,
            auth_username=username,
            auth_password=password,
            refresh_seconds=refresh_seconds,
            ops=OpsSettings.from_environment(values),
        )

    @property
    def credentials(self) -> tuple[str, str] | None:
        if self.auth_username is None or self.auth_password is None:
            return None
        return self.auth_username, self.auth_password


def _boolean(
    environment: Mapping[str, str],
    name: str,
    default: str,
) -> bool:
    raw = environment.get(name, default).strip().lower()
    if raw not in {"true", "false"}:
        raise OpsConfigurationError(f"{name} must be exactly true or false")
    return raw == "true"


def _sanitize(value: object) -> object:
    if isinstance(value, dict):
        return {
            str(key): _sanitize(item)
            for key, item in cast(dict[object, object], value).items()
            if not any(
                token in str(key).lower()
                for token in ("secret", "private", "password", "webhook")
            )
        }
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _read_json(
    path: Path,
    *,
    checksum_required: bool = False,
) -> dict[str, object] | None:
    try:
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > MAX_JSON_BYTES
        ):
            return None
        raw = path.read_bytes()
        if checksum_required:
            checksum_path = path.with_suffix(path.suffix + ".sha256")
            expected = checksum_path.read_text(encoding="ascii").split()[0]
            if hashlib.sha256(raw).hexdigest() != expected:
                return None
        decoded = json.loads(raw)
    except (OSError, IndexError, json.JSONDecodeError):
        return None
    if not isinstance(decoded, dict):
        return None
    return cast(dict[str, object], _sanitize(decoded))


def _integer(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return 0
    return 0


def _quality_files(root: Path) -> tuple[Path, ...]:
    if not root.is_dir():
        return ()
    return tuple(sorted(root.glob("quality-*.json"), reverse=True))


def _quality_reports(root: Path, *, limit: int) -> list[dict[str, object]]:
    by_date: dict[str, dict[str, object]] = {}
    for path in _quality_files(root):
        payload = _read_json(path, checksum_required=True)
        if payload is None:
            continue
        report_date = payload.get("report_date")
        if not isinstance(report_date, str):
            continue
        try:
            date.fromisoformat(report_date)
        except ValueError:
            continue
        previous = by_date.get(report_date)
        if previous is None or _integer(payload.get("generated_at_ms")) > _integer(
            previous.get("generated_at_ms")
        ):
            by_date[report_date] = payload
    return [by_date[key] for key in sorted(by_date, reverse=True)[:limit]]


def _quality_gate(reports: list[dict[str, object]]) -> dict[str, object]:
    by_date = {
        date.fromisoformat(cast(str, report["report_date"])): report
        for report in reports
        if isinstance(report.get("report_date"), str)
        and report.get("schema_version") == QUALITY_SCHEMA_VERSION
    }
    ordered = sorted(by_date)
    missing: list[str] = []
    if ordered:
        current = ordered[0]
        while current <= ordered[-1]:
            if current not in by_date:
                missing.append(current.isoformat())
            current += timedelta(days=1)
    consecutive = 0
    if ordered:
        expected = ordered[-1]
        for current in reversed(ordered):
            if current != expected or by_date[current].get("qualified_day") is not True:
                break
            consecutive += 1
            expected -= timedelta(days=1)
    if consecutive < 7:
        stage = "insufficient_seven_days"
    elif consecutive < 30:
        stage = "collect_to_thirty_days"
    else:
        stage = "thirty_days_complete"
    return {
        "stage": stage,
        "observed_days": len(by_date),
        "consecutive_qualified_days": consecutive,
        "required_initial_days": 7,
        "required_evidence_days": 30,
        "missing_dates": missing,
    }


def _report_summary(report: dict[str, object]) -> dict[str, object]:
    markets = report.get("markets")
    return {
        "report_date": report.get("report_date"),
        "generated_at_ms": report.get("generated_at_ms"),
        "qualified_day": report.get("qualified_day") is True,
        "collector_outage_count": report.get("collector_outage_count", 0),
        "collector_outage_duration_ms": report.get("collector_outage_duration_ms", 0),
        "collector_not_running_count": report.get("collector_not_running_count", 0),
        "collector_not_running_duration_ms": report.get(
            "collector_not_running_duration_ms", 0
        ),
        "operational_gap_count": report.get("operational_gap_count", 0),
        "operational_major_gap_count": report.get("operational_major_gap_count", 0),
        "operational_coverage_pct": report.get("operational_coverage_pct", "0"),
        "qualification_reasons": report.get("qualification_reasons", []),
        "markets": markets if isinstance(markets, list) else [],
    }


class ObservabilityReader:
    """Build bounded snapshots solely from public configuration and runtime files."""

    def __init__(self, settings: ObserverSettings) -> None:
        self.settings = settings

    def service_health(self) -> dict[str, object]:
        return {
            "status": "ok",
            "service": "hyperbot-observer",
            "version": self.settings.ops.code_version,
            "code_commit": self.settings.ops.code_commit,
            "read_only": True,
        }

    def status(self, *, now_ms: int | None = None) -> dict[str, object]:
        checked_at_ms = int(time.time() * 1_000) if now_ms is None else now_ms
        health = self._collector_health(checked_at_ms)
        maintenance = self._status_file("maintenance_status.json")
        return {
            "generated_at_ms": checked_at_ms,
            "service": "hyperbot-observer",
            "version": self.settings.ops.code_version,
            "code_commit": self.settings.ops.code_commit,
            "read_only": True,
            "control_endpoints_enabled": False,
            "public_only": True,
            "live_enabled": False,
            "shadow_only": True,
            "collector_health": asdict(health),
            "collector": self._status_file("collector_status.json"),
            "maintenance": maintenance,
            "maintenance_health": self._maintenance_health(
                maintenance,
                checked_at_ms=checked_at_ms,
            ),
            "watchdog": self._status_file("watchdog_status.json"),
        }

    def markets(self) -> dict[str, object]:
        catalog_root = self.settings.ops.review_root / "catalog"
        catalog: dict[str, object] | None = None
        if catalog_root.is_dir():
            for path in sorted(catalog_root.glob("catalog-*.json"), reverse=True):
                catalog = _read_json(path, checksum_required=True)
                if catalog is not None:
                    break
        definitions = catalog.get("definitions", []) if catalog else []
        by_coin: dict[str, dict[str, object]] = {}
        if isinstance(definitions, list):
            for raw in definitions:
                if not isinstance(raw, dict):
                    continue
                coin = raw.get("coin")
                if isinstance(coin, str) and coin in self.settings.ops.markets:
                    by_coin[coin] = cast(dict[str, object], _sanitize(raw))
        channels_by_market: dict[str, list[str]] = {
            market: [] for market in self.settings.ops.markets
        }
        for channel, market in self.settings.ops.subscriptions:
            channels_by_market.setdefault(market, []).append(channel)
        depth_markets = set(self.settings.ops.depth_markets)
        breadth_markets = set(self.settings.ops.breadth_markets)
        items = []
        for coin in self.settings.ops.markets:
            definition = by_coin.get(coin, {})
            if coin in depth_markets:
                profile = "depth"
            elif coin in breadth_markets:
                profile = "breadth"
            else:
                profile = "legacy"
            items.append(
                {
                    "coin": coin,
                    "profile": profile,
                    "channels": channels_by_market.get(coin, []),
                    "catalog_available": bool(definition),
                    "market_id": definition.get("market_id"),
                    "display_name": definition.get("display_name", coin),
                    "market_kind": definition.get("market_kind"),
                    "dex": definition.get("dex"),
                    "status": definition.get("status"),
                    "oracle_px": definition.get("oracle_px"),
                    "mark_px": definition.get("mark_px"),
                    "definition_version": definition.get("definition_version"),
                    "definition_sha256": definition.get("definition_sha256"),
                    "quality_flags": definition.get("quality_flags", []),
                }
            )
        return {
            "configured_count": len(items),
            "catalog_observed_at_ms": (
                catalog.get("observed_at_ms") if catalog else None
            ),
            "catalog_issues": catalog.get("issues", []) if catalog else [],
            "markets": items,
        }

    def quality_latest(self) -> dict[str, object]:
        reports = _quality_reports(self.settings.ops.review_root, limit=1)
        if not reports:
            return {"available": False, "report": None}
        return {"available": True, "report": reports[0]}

    def quality_history(self, *, limit: int = 30) -> dict[str, object]:
        bounded_limit = max(1, min(limit, MAX_HISTORY_DAYS))
        reports = _quality_reports(
            self.settings.ops.review_root,
            limit=MAX_HISTORY_DAYS,
        )
        chronological = list(reversed(reports))
        return {
            "limit": bounded_limit,
            "gate": _quality_gate(chronological),
            "days": [_report_summary(item) for item in reports[:bounded_limit]],
        }

    def storage(self) -> dict[str, object]:
        root = self.settings.ops.data_root
        if not root.is_dir():
            return {
                "available": False,
                "root": str(root),
                "streams": [],
            }
        disk = shutil.disk_usage(root)
        streams = [
            self._stream_storage(stream)
            for stream in ("public-market-data", "collector-control")
        ]
        total_bytes = 0
        for path in root.rglob("*"):
            try:
                if path.is_file() and not path.is_symlink():
                    total_bytes += path.stat().st_size
            except OSError:
                continue
        archive: dict[str, object] = {
            "enabled": self.settings.ops.archive_enabled,
            "root": (
                str(self.settings.ops.archive_root)
                if self.settings.ops.archive_root is not None
                else None
            ),
            "mount_guard_enabled": (
                self.settings.ops.archive_mount_sentinel is not None
            ),
            "total_bytes": 0,
        }
        archive_root = self.settings.ops.archive_root
        if self.settings.ops.archive_enabled and archive_root is not None:
            archive_bytes = 0
            for path in archive_root.rglob("*"):
                try:
                    if path.is_file() and not path.is_symlink():
                        archive_bytes += path.stat().st_size
                except OSError:
                    continue
            archive_disk_target = (
                archive_root if archive_root.exists() else archive_root.parent
            )
            archive_disk = shutil.disk_usage(archive_disk_target)
            archive.update(
                {
                    "total_bytes": archive_bytes,
                    "disk_total_bytes": archive_disk.total,
                    "disk_used_bytes": archive_disk.used,
                    "disk_free_bytes": archive_disk.free,
                }
            )
        return {
            "available": True,
            "root": str(root),
            "total_bytes": total_bytes,
            "disk_total_bytes": disk.total,
            "disk_used_bytes": disk.used,
            "disk_free_bytes": disk.free,
            "minimum_free_bytes": self.settings.ops.minimum_free_bytes,
            "minimum_retention_days": self.settings.ops.minimum_retention_days,
            "hot_retention_days": self.settings.ops.hot_retention_days,
            "archive": archive,
            "streams": streams,
        }

    def incidents(self, *, now_ms: int | None = None) -> dict[str, object]:
        incidents: list[dict[str, object]] = []
        status = self.status(now_ms=now_ms)
        health = status["collector_health"]
        if isinstance(health, dict):
            for reason in health.get("reasons", []):
                incidents.append(
                    {
                        "severity": "critical",
                        "source": "collector_health",
                        "code": reason,
                    }
                )
        maintenance = status.get("maintenance")
        maintenance_health = status.get("maintenance_health")
        if isinstance(maintenance_health, dict):
            reasons = maintenance_health.get("reasons", [])
            if isinstance(reasons, list):
                for reason in reasons:
                    if not isinstance(reason, str):
                        continue
                    incident: dict[str, object] = {
                        "severity": "critical",
                        "source": "maintenance",
                        "code": reason,
                    }
                    if reason == "maintenance_failed" and isinstance(maintenance, dict):
                        incident["detail"] = maintenance.get("error")
                    incidents.append(incident)

        quality_anomalies: list[dict[str, object]] = []
        latest = self.quality_latest()
        report = latest.get("report")
        if isinstance(report, dict) and report.get("qualified_day") is not True:
            reasons = report.get("qualification_reasons", [])
            if not isinstance(reasons, list):
                reasons = []
            for reason in reasons:
                if not isinstance(reason, str):
                    continue
                quality_anomalies.append(
                    {
                        "severity": "warning",
                        "source": "quality",
                        "code": reason,
                        "report_date": report.get("report_date"),
                    }
                )
        quality_report = {
            "available": isinstance(report, dict),
            "report_date": (
                report.get("report_date") if isinstance(report, dict) else None
            ),
            "qualified_day": (
                report.get("qualified_day") if isinstance(report, dict) else None
            ),
            "count": len(quality_anomalies),
            "anomalies": quality_anomalies[:100],
        }
        return {
            "count": len(incidents),
            "active_count": len(incidents),
            "incidents": incidents[:100],
            "quality_anomalies": quality_report,
        }

    def shadow(self) -> dict[str, object]:
        roots = (
            self.settings.ops.review_root / "shadow",
            self.settings.ops.review_root,
        )
        candidates: list[Path] = []
        for root in roots:
            if root.is_dir():
                candidates.extend(root.glob("shadow-*.json"))
        by_date: dict[date, dict[str, object]] = {}
        for path in sorted(set(candidates), reverse=True):
            payload = _read_json(path, checksum_required=True)
            if payload is None or not isinstance(payload.get("report_date"), str):
                continue
            try:
                report_date = date.fromisoformat(cast(str, payload["report_date"]))
            except ValueError:
                continue
            previous = by_date.get(report_date)
            if previous is None or _integer(payload.get("generated_at_ms")) > _integer(
                previous.get("generated_at_ms")
            ):
                by_date[report_date] = payload
        ordered_dates = sorted(by_date)
        consecutive = 0
        if ordered_dates:
            expected = ordered_dates[-1]
            for report_date in reversed(ordered_dates):
                if (
                    report_date != expected
                    or by_date[report_date].get("qualified_day") is not True
                ):
                    break
                consecutive += 1
                expected -= timedelta(days=1)
        if ordered_dates:
            return {
                "available": True,
                "runner_deployed": False,
                "phase": (
                    "fourteen_days_complete" if consecutive >= 14 else "collecting"
                ),
                "latest_report": by_date[ordered_dates[-1]],
                "observed_days": len(ordered_dates),
                "consecutive_qualified_days": consecutive,
                "required_consecutive_days": 14,
                "canary_authorized": False,
            }
        return {
            "available": False,
            "runner_deployed": False,
            "phase": "software_ready_not_deployed",
            "observed_days": 0,
            "consecutive_qualified_days": 0,
            "canary_authorized": False,
            "required_consecutive_days": 14,
        }

    def configuration(self) -> dict[str, object]:
        ops = self.settings.ops
        return {
            "config_sha256": ops.config_sha256,
            "collector_enabled": ops.collector_enabled,
            "live_enabled": ops.live_enabled,
            "shadow_only": ops.shadow_only,
            "markets": list(ops.markets),
            "channels": list(ops.channels),
            "depth_markets": list(ops.depth_markets),
            "breadth_markets": list(ops.breadth_markets),
            "subscription_count": len(ops.subscriptions),
            "queue_capacity": ops.queue_capacity,
            "persistence_batch_size": ops.persistence_batch_size,
            "fsync_every_records": ops.fsync_every_records,
            "data_mount_guard_enabled": ops.data_mount_sentinel is not None,
            "archive_enabled": ops.archive_enabled,
            "archive_mount_guard_enabled": (ops.archive_mount_sentinel is not None),
            "hot_retention_days": ops.hot_retention_days,
            "heartbeat_interval_seconds": ops.heartbeat_interval_seconds,
            "stale_after_seconds": ops.stale_after_seconds,
            "health_max_age_seconds": ops.health_max_age_seconds,
            "minimum_free_bytes": ops.minimum_free_bytes,
            "minimum_retention_days": ops.minimum_retention_days,
            "code_version": ops.code_version,
            "code_commit": ops.code_commit,
            "ui": {
                "host": self.settings.host,
                "port": self.settings.port,
                "authentication_required": self.settings.auth_required,
                "refresh_seconds": self.settings.refresh_seconds,
                "read_only": True,
            },
        }

    def overview(self, *, history_limit: int = 30) -> dict[str, object]:
        return {
            "status": self.status(),
            "markets": self.markets(),
            "quality": {
                "latest": self.quality_latest(),
                "history": self.quality_history(limit=history_limit),
            },
            "incidents": self.incidents(),
            "storage": self.storage(),
            "shadow": self.shadow(),
            "configuration": self.configuration(),
        }

    def _status_file(self, name: str) -> dict[str, object] | None:
        return _read_json(self.settings.ops.runtime_root / name)

    def _collector_health(self, now_ms: int) -> HealthResult:
        try:
            return evaluate_collector_health(self.settings.ops, now_ms=now_ms)
        except OSError as exc:
            return HealthResult(
                healthy=False,
                reasons=(f"health_io_error:{type(exc).__name__}",),
                checked_at_ms=now_ms,
                status_age_ms=None,
                free_bytes=0,
                config_sha256=self.settings.ops.config_sha256,
            )

    def _maintenance_health(
        self,
        status: dict[str, object] | None,
        *,
        checked_at_ms: int,
    ) -> dict[str, object]:
        payload = status or {}
        state = payload.get("state")
        updated_at_ms = payload.get("updated_at_ms")
        status_age_ms = (
            max(checked_at_ms - updated_at_ms, 0)
            if isinstance(updated_at_ms, int)
            else None
        )
        now = datetime.fromtimestamp(checked_at_ms / 1_000, tz=UTC)
        scheduled = now.replace(
            hour=self.settings.ops.maintenance_hour_utc,
            minute=self.settings.ops.maintenance_minute_utc,
            second=0,
            microsecond=0,
        )
        expected_report_date: str | None = None
        if checked_at_ms >= int(scheduled.timestamp() * 1_000) + (
            MAINTENANCE_COMPLETION_GRACE_MS
        ):
            expected_report_date = (now.date() - timedelta(days=1)).isoformat()

        reasons: list[str] = []
        stale_after_ms = max(
            MAINTENANCE_STALE_MINIMUM_MS,
            self.settings.ops.maintenance_poll_seconds * 5 * 1_000,
        )
        report_date = payload.get("report_date")
        if state == "failed":
            reasons.append("maintenance_failed")
        elif state == "running":
            if status_age_ms is None or status_age_ms > stale_after_ms:
                reasons.append("maintenance_stale")
            if expected_report_date is not None and report_date != expected_report_date:
                reasons.append("maintenance_wrong_report_date")
        elif expected_report_date is not None and report_date != expected_report_date:
            reasons.append("maintenance_overdue")
        return {
            "healthy": not reasons,
            "reasons": reasons,
            "state": state,
            "report_date": report_date,
            "expected_report_date": expected_report_date,
            "status_age_ms": status_age_ms,
            "stale_after_ms": stale_after_ms,
            "checked_at_ms": checked_at_ms,
        }

    def _stream_storage(self, stream: str) -> dict[str, object]:
        stream_root = self.settings.ops.data_root / stream
        manifest_path = stream_root / "manifest.json"
        manifest = _read_json(manifest_path)
        manifest_valid = False
        if manifest is not None:
            try:
                expected = (
                    manifest_path.with_suffix(".sha256")
                    .read_text(encoding="ascii")
                    .split()[0]
                )
                actual = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
                manifest_valid = actual == expected
            except (OSError, IndexError):
                manifest_valid = False
        segments = manifest.get("segments", []) if manifest else []
        segment_count = len(segments) if isinstance(segments, list) else 0
        record_count = 0
        dates: list[str] = []
        if isinstance(segments, list):
            for raw in segments:
                if not isinstance(raw, dict):
                    continue
                count = raw.get("record_count")
                if isinstance(count, int):
                    record_count += count
                utc_date = raw.get("utc_date")
                if isinstance(utc_date, str):
                    dates.append(utc_date)
        active_bytes = 0
        if stream_root.is_dir():
            for active in stream_root.glob("*.open"):
                try:
                    if active.is_file() and not active.is_symlink():
                        active_bytes += active.stat().st_size
                except OSError:
                    continue
        return {
            "stream": stream,
            "manifest_available": manifest is not None,
            "manifest_valid": manifest_valid,
            "closed_segment_count": segment_count,
            "closed_record_count": record_count,
            "active_bytes": active_bytes,
            "first_utc_date": min(dates) if dates else None,
            "last_utc_date": max(dates) if dates else None,
        }
