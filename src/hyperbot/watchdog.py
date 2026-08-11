"""State-change alerts for public collector health without Docker privileges."""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from hyperbot.ops import HealthResult, OpsConfigurationError


@dataclass(frozen=True, slots=True)
class WatchdogSettings:
    interval_seconds: int
    start_grace_seconds: int
    alert_cooldown_seconds: int
    webhook_url: str | None

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> WatchdogSettings:
        def positive(name: str, default: str, minimum: int) -> int:
            try:
                value = int(environment.get(name, default))
            except ValueError as exc:
                raise OpsConfigurationError(f"{name} must be an integer") from exc
            if value < minimum:
                raise OpsConfigurationError(f"{name} must be >= {minimum}")
            return value

        webhook_file = environment.get("HYPERBOT_ALERT_WEBHOOK_FILE", "").strip()
        webhook: str | None = None
        if webhook_file:
            path = Path(webhook_file)
            try:
                webhook = path.read_text(encoding="utf-8").strip() or None
            except OSError as exc:
                raise OpsConfigurationError(
                    f"cannot read HYPERBOT_ALERT_WEBHOOK_FILE: {path}"
                ) from exc
        if webhook is not None and not (
            webhook.startswith("https://") or webhook.startswith("http://127.0.0.1")
        ):
            raise OpsConfigurationError(
                "HyperBot alert webhook must use HTTPS"
            )
        return cls(
            interval_seconds=positive("HYPERBOT_WATCHDOG_INTERVAL_SECONDS", "30", 10),
            start_grace_seconds=positive(
                "HYPERBOT_WATCHDOG_START_GRACE_SECONDS", "120", 0
            ),
            alert_cooldown_seconds=positive(
                "HYPERBOT_ALERT_COOLDOWN_SECONDS", "900", 60
            ),
            webhook_url=webhook,
        )


class AlertTransport(Protocol):
    def __call__(self, url: str, payload: bytes) -> None: ...


def default_alert_transport(url: str, payload: bytes) -> None:
    request = urllib.request.Request(  # noqa: S310 - HTTPS checked in settings
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(  # noqa: S310 - HTTPS checked in settings
        request,
        timeout=10,
    ) as response:
        if response.status < 200 or response.status >= 300:
            raise RuntimeError(f"alert webhook returned HTTP {response.status}")


def alert_payload(result: HealthResult, *, recovered: bool) -> bytes:
    """Serialize health only; never include environment or webhook details."""

    return json.dumps(
        {
            "service": "hyperbot-collector",
            "severity": "recovery" if recovered else "critical",
            "healthy": result.healthy,
            "reasons": list(result.reasons),
            "checked_at_ms": result.checked_at_ms,
            "status_age_ms": result.status_age_ms,
            "free_bytes": result.free_bytes,
            "config_sha256": result.config_sha256,
            "public_only": True,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def should_alert(
    *,
    healthy: bool,
    previous_healthy: bool | None,
    now_ms: int,
    started_at_ms: int,
    last_alert_at_ms: int | None,
    settings: WatchdogSettings,
) -> bool:
    if healthy:
        return previous_healthy is False
    if now_ms - started_at_ms < settings.start_grace_seconds * 1_000:
        return False
    if previous_healthy is not False:
        return True
    return last_alert_at_ms is None or (
        now_ms - last_alert_at_ms >= settings.alert_cooldown_seconds * 1_000
    )


def watchdog_status(
    result: HealthResult,
    *,
    alert_configured: bool,
    alert_sent: bool,
    delivery_error: str | None,
) -> dict[str, object]:
    payload = asdict(result)
    payload.update(
        {
            "state": "healthy" if result.healthy else "unhealthy",
            "updated_at_ms": result.checked_at_ms,
            "alert_configured": alert_configured,
            "alert_sent": alert_sent,
            "delivery_error": delivery_error,
        }
    )
    return payload
