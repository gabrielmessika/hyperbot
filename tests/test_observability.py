from __future__ import annotations

import base64
import hashlib
import http.client
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from hyperbot.observability import ObservabilityReader, ObserverSettings
from hyperbot.ops import OpsConfigurationError, atomic_write_json
from hyperbot.services.observer import ObserverHTTPServer, build_observer_handler


def _environment(tmp_path: Path) -> dict[str, str]:
    password_file = tmp_path / "ui_password"
    password_file.write_text("observer-password-for-tests\n", encoding="utf-8")
    return {
        "HYPERBOT_COLLECTOR_ENABLED": "true",
        "HYPERBOT_LIVE_ENABLED": "false",
        "HYPERBOT_SHADOW_ONLY": "true",
        "HYPERBOT_COLLECTOR_DEPTH_MARKETS": "BTC",
        "HYPERBOT_COLLECTOR_BREADTH_MARKETS": "ETH",
        "HYPERBOT_DATA_ROOT": str(tmp_path / "data"),
        "HYPERBOT_RUNTIME_ROOT": str(tmp_path / "runtime"),
        "HYPERBOT_REVIEW_ROOT": str(tmp_path / "reviews"),
        "HYPERBOT_MINIMUM_FREE_BYTES": "1",
        "HYPERBOT_UI_HOST": "127.0.0.1",
        "HYPERBOT_UI_PORT": "3002",
        "HYPERBOT_UI_AUTH_REQUIRED": "true",
        "HYPERBOT_UI_AUTH_USERNAME": "hyperbot",
        "HYPERBOT_UI_AUTH_PASSWORD_FILE": str(password_file),
        "HYPERBOT_UI_REFRESH_SECONDS": "10",
    }


def _write_checksummed_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(payload, sort_keys=True).encode("utf-8") + b"\n"
    path.write_bytes(raw)
    path.with_suffix(path.suffix + ".sha256").write_text(
        f"{hashlib.sha256(raw).hexdigest()}  {path.name}\n",
        encoding="ascii",
    )


def _settings_with_snapshots(tmp_path: Path) -> ObserverSettings:
    settings = ObserverSettings.from_environment(_environment(tmp_path))
    now_ms = int(time.time() * 1_000)
    settings.ops.data_root.mkdir(parents=True)
    atomic_write_json(
        settings.ops.status_path,
        {
            "state": "running",
            "updated_at_ms": now_ms,
            "public_only": True,
            "live_enabled": False,
            "shadow_only": True,
            "config_sha256": settings.ops.config_sha256,
            "connected": True,
            "dropped_events": 0,
            "last_message_receive_ms": now_ms,
            "api_secret": "must-not-leak",
        },
    )
    atomic_write_json(
        settings.ops.runtime_root / "maintenance_status.json",
        {
            "state": "completed",
            "report_date": (
                datetime.now(tz=UTC).date() - timedelta(days=1)
            ).isoformat(),
            "updated_at_ms": now_ms,
        },
    )
    atomic_write_json(
        settings.ops.runtime_root / "watchdog_status.json",
        {"state": "monitoring", "healthy": True},
    )
    for day in range(4, 11):
        report_date = f"2026-08-{day:02d}"
        _write_checksummed_json(
            settings.ops.review_root / f"quality-{report_date}-test.json",
            {
                "report_date": report_date,
                "generated_at_ms": 1_786_000_000_000 + day,
                "qualified_day": True,
                "collector_outage_count": 0,
                "collector_outage_duration_ms": 0,
                "qualification_reasons": [],
                "markets": [
                    {
                        "coin": "BTC",
                        "coverage_pct": "100",
                        "latency_p50_ms": "2",
                        "latency_p95_ms": "4",
                        "latency_p99_ms": "8",
                        "major_gap_count": 0,
                        "spread_bps_p50": "1.2",
                        "trade_count": 120,
                        "trade_notional_usd": "100000",
                    }
                ],
            },
        )
    _write_checksummed_json(
        settings.ops.review_root / "catalog" / "catalog-2026-08-10.json",
        {
            "observed_at_ms": 1_786_406_400_000,
            "issues": [],
            "definitions": [
                {
                    "coin": "BTC",
                    "market_id": "core:BTC",
                    "display_name": "BTC",
                    "market_kind": "core_perp",
                    "dex": "core",
                    "status": "active",
                    "oracle_px": "120000",
                    "mark_px": "120001",
                    "definition_version": 1,
                    "definition_sha256": "a" * 64,
                    "quality_flags": [],
                }
            ],
        },
    )
    stream_root = settings.ops.data_root / "public-market-data"
    manifest_path = stream_root / "manifest.json"
    _write_checksummed_json(
        manifest_path,
        {
            "stream": "public-market-data",
            "segments": [
                {
                    "path": "2026-08-10-000000.jsonl",
                    "record_count": 120,
                    "utc_date": "2026-08-10",
                }
            ],
        },
    )
    manifest_path.with_suffix(".json.sha256").replace(
        manifest_path.with_suffix(".sha256")
    )
    for day in range(8, 11):
        report_date = f"2026-08-{day:02d}"
        _write_checksummed_json(
            settings.ops.review_root / "shadow" / f"shadow-{report_date}-test.json",
            {
                "report_date": report_date,
                "generated_at_ms": 1_786_000_100_000 + day,
                "qualified_day": True,
                "shadow_only": True,
                "risk_violation_count": 0,
            },
        )
    return settings


def _request(
    port: int,
    method: str,
    path: str,
    *,
    authenticated: bool = False,
) -> tuple[int, dict[str, str], bytes]:
    headers: dict[str, str] = {}
    if authenticated:
        token = base64.b64encode(b"hyperbot:observer-password-for-tests").decode(
            "ascii"
        )
        headers["Authorization"] = f"Basic {token}"
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(method, path, headers=headers)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        connection.close()


def test_public_bind_requires_authentication_and_password_file(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    environment["HYPERBOT_UI_HOST"] = "0.0.0.0"
    environment["HYPERBOT_UI_AUTH_REQUIRED"] = "false"
    with pytest.raises(OpsConfigurationError, match="cannot be disabled"):
        ObserverSettings.from_environment(environment)

    environment["HYPERBOT_UI_AUTH_REQUIRED"] = "true"
    environment.pop("HYPERBOT_UI_AUTH_PASSWORD_FILE")
    with pytest.raises(OpsConfigurationError, match="requires"):
        ObserverSettings.from_environment(environment)

    local = _environment(tmp_path)
    local["HYPERBOT_UI_AUTH_REQUIRED"] = "false"
    local.pop("HYPERBOT_UI_AUTH_PASSWORD_FILE")
    assert ObserverSettings.from_environment(local).credentials is None

    short_password = tmp_path / "short_password"
    short_password.write_text("too-short\n", encoding="utf-8")
    short = _environment(tmp_path)
    short["HYPERBOT_UI_AUTH_PASSWORD_FILE"] = str(short_password)
    with pytest.raises(OpsConfigurationError, match="at least 16"):
        ObserverSettings.from_environment(short)

    password_symlink = tmp_path / "password_symlink"
    password_symlink.symlink_to(tmp_path / "ui_password")
    linked = _environment(tmp_path)
    linked["HYPERBOT_UI_AUTH_PASSWORD_FILE"] = str(password_symlink)
    with pytest.raises(OpsConfigurationError, match="regular file"):
        ObserverSettings.from_environment(linked)


def test_observability_reader_combines_only_verified_bounded_snapshots(
    tmp_path: Path,
) -> None:
    settings = _settings_with_snapshots(tmp_path)
    invalid = settings.ops.review_root / "quality-2026-08-11-invalid.json"
    invalid.write_text(
        '{"report_date":"2026-08-11","qualified_day":true}',
        encoding="utf-8",
    )
    reader = ObservabilityReader(settings)

    overview = reader.overview(history_limit=5)

    status = overview["status"]
    assert isinstance(status, dict)
    assert status["read_only"] is True
    assert status["control_endpoints_enabled"] is False
    assert status["live_enabled"] is False
    assert status["collector_health"]["healthy"] is True  # type: ignore[index]
    assert "api_secret" not in status["collector"]  # type: ignore[operator]
    markets = overview["markets"]
    assert isinstance(markets, dict)
    assert markets["configured_count"] == 2
    assert markets["markets"][0]["catalog_available"] is True  # type: ignore[index]
    assert markets["markets"][1]["catalog_available"] is False  # type: ignore[index]
    assert markets["markets"][0]["profile"] == "depth"  # type: ignore[index]
    assert markets["markets"][0]["channels"] == [  # type: ignore[index]
        "l2Book",
        "bbo",
        "trades",
    ]
    assert markets["markets"][1]["profile"] == "breadth"  # type: ignore[index]
    assert markets["markets"][1]["channels"] == [  # type: ignore[index]
        "bbo",
        "trades",
    ]
    quality = overview["quality"]
    assert isinstance(quality, dict)
    assert quality["latest"]["report"]["report_date"] == "2026-08-10"  # type: ignore[index]
    assert quality["history"]["gate"]["consecutive_qualified_days"] == 7  # type: ignore[index]
    assert len(quality["history"]["days"]) == 5  # type: ignore[index]
    storage = overview["storage"]
    assert isinstance(storage, dict)
    assert storage["streams"][0]["manifest_valid"] is True  # type: ignore[index]
    shadow = overview["shadow"]
    assert isinstance(shadow, dict)
    assert shadow["consecutive_qualified_days"] == 3
    assert shadow["canary_authorized"] is False


def test_incidents_separate_active_failures_from_quality_anomalies(
    tmp_path: Path,
) -> None:
    settings = _settings_with_snapshots(tmp_path)
    _write_checksummed_json(
        settings.ops.review_root / "quality-2026-08-11-test.json",
        {
            "report_date": "2026-08-11",
            "generated_at_ms": 1_786_406_500_000,
            "qualified_day": False,
            "collector_outage_count": 0,
            "collector_outage_duration_ms": 0,
            "qualification_reasons": [
                "coverage_below_threshold:BTC",
                "major_gap:BTC",
            ],
            "markets": [],
        },
    )
    reader = ObservabilityReader(settings)

    result = reader.incidents()

    assert result["count"] == 0
    assert result["active_count"] == 0
    assert result["incidents"] == []
    quality = result["quality_anomalies"]
    assert isinstance(quality, dict)
    assert quality["available"] is True
    assert quality["report_date"] == "2026-08-11"
    assert quality["qualified_day"] is False
    assert quality["count"] == 2
    assert [item["code"] for item in quality["anomalies"]] == [  # type: ignore[index]
        "coverage_below_threshold:BTC",
        "major_gap:BTC",
    ]

    atomic_write_json(
        settings.ops.runtime_root / "maintenance_status.json",
        {"state": "failed", "error": "quality report failed"},
    )

    failed = reader.incidents()

    assert failed["count"] == 1
    assert failed["active_count"] == 1
    assert failed["incidents"] == [
        {
            "severity": "critical",
            "source": "maintenance",
            "code": "maintenance_failed",
            "detail": "quality report failed",
        }
    ]
    failed_quality = failed["quality_anomalies"]
    assert isinstance(failed_quality, dict)
    assert failed_quality["count"] == 2


def test_maintenance_health_detects_stale_and_overdue_reports(
    tmp_path: Path,
) -> None:
    settings = _settings_with_snapshots(tmp_path)
    reader = ObservabilityReader(settings)
    now = datetime(2026, 8, 12, 6, tzinfo=UTC)
    now_ms = int(now.timestamp() * 1_000)

    atomic_write_json(
        settings.ops.runtime_root / "maintenance_status.json",
        {
            "state": "running",
            "report_date": "2026-08-11",
            "updated_at_ms": now_ms - 10 * 60 * 1_000,
        },
    )
    stale = reader.incidents(now_ms=now_ms)

    assert [
        item["code"] for item in stale["incidents"] if item["source"] == "maintenance"
    ] == ["maintenance_stale"]

    atomic_write_json(
        settings.ops.runtime_root / "maintenance_status.json",
        {
            "state": "completed",
            "report_date": "2026-08-10",
            "updated_at_ms": now_ms,
        },
    )
    overdue = reader.incidents(now_ms=now_ms)

    assert [
        item["code"] for item in overdue["incidents"] if item["source"] == "maintenance"
    ] == ["maintenance_overdue"]


def test_http_dashboard_and_api_are_authenticated_and_strictly_read_only(
    tmp_path: Path,
) -> None:
    settings = _settings_with_snapshots(tmp_path)
    reader = ObservabilityReader(settings)
    with ObserverHTTPServer(
        ("127.0.0.1", 0),
        build_observer_handler(reader, settings),
    ) as server:
        port = int(server.server_address[1])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            health_status, _, health_body = _request(port, "GET", "/health")
            assert health_status == 200
            assert json.loads(health_body)["read_only"] is True

            denied_status, denied_headers, _ = _request(port, "GET", "/api/status")
            assert denied_status == 401
            assert "Basic" in denied_headers["WWW-Authenticate"]

            status_code, headers, body = _request(
                port, "GET", "/api/overview?limit=7", authenticated=True
            )
            assert status_code == 200
            assert json.loads(body)["status"]["read_only"] is True
            assert headers["Cache-Control"] == "no-store"
            assert headers["X-Frame-Options"] == "DENY"
            assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]

            ui_status, _, ui_body = _request(port, "GET", "/", authenticated=True)
            ui = ui_body.decode("utf-8").lower()
            assert ui_status == 200
            assert "hyperbot" in ui
            assert "observabilité" in ui
            assert "incidents opérationnels actifs" in ui
            assert "anomalies du dernier rapport m3" in ui
            assert "ces anomalies ne sont pas des incidents actifs" in ui
            assert "maintenance_health" in ui
            assert "active_count" in ui
            assert "/api/overview" in ui
            assert "/api/start" not in ui
            assert "/api/stop" not in ui
            assert ">start<" not in ui
            assert ">stop<" not in ui
            assert "__hyperbot_refresh_ms__" not in ui
            assert "setinterval(refresh, 10000)" in ui

            head_status, head_headers, head_body = _request(
                port, "HEAD", "/api/status", authenticated=True
            )
            assert head_status == 200
            assert int(head_headers["Content-Length"]) > 0
            assert head_body == b""

            endpoints_status, _, endpoints_body = _request(
                port, "GET", "/api/endpoints", authenticated=True
            )
            endpoints = json.loads(endpoints_body)
            assert endpoints_status == 200
            assert endpoints["methods"] == ["GET", "HEAD"]
            assert endpoints["control_endpoints_enabled"] is False
            assert not any(
                action in route
                for route in endpoints["routes"]
                for action in ("start", "stop", "restart", "order")
            )

            for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
                rejected, rejected_headers, rejected_body = _request(
                    port, method, "/api/status", authenticated=True
                )
                assert rejected == 405
                assert rejected_headers["Allow"] == "GET, HEAD"
                assert json.loads(rejected_body)["error"] == "read_only_api"

            invalid_status, _, _ = _request(
                port,
                "GET",
                "/api/quality/history?limit=invalid",
                authenticated=True,
            )
            assert invalid_status == 400
            missing_status, _, _ = _request(
                port, "GET", "/api/missing", authenticated=True
            )
            assert missing_status == 404
        finally:
            server.shutdown()
            thread.join(timeout=3)
