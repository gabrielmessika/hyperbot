from __future__ import annotations

import asyncio
import json
import subprocess
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve

from hyperbot.maintenance import deduplicate_automatic_target, run_daily_maintenance
from hyperbot.models import (
    CollectorControlEvent,
    CollectorControlKind,
    EventContext,
    PublicMarketDataEvent,
    TimeSource,
)
from hyperbot.ops import (
    OpsConfigurationError,
    OpsSettings,
    atomic_write_json,
    evaluate_collector_health,
)
from hyperbot.quality import DailyQualityAnalyzer
from hyperbot.segmented_store import SegmentedEventStore
from hyperbot.services.collector_runtime import run_collector_service
from hyperbot.watchdog import WatchdogSettings, alert_payload, should_alert

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_automatic_maintenance_runs_each_utc_date_once_per_process() -> None:
    first = date(2026, 8, 14)
    second = date(2026, 8, 15)

    assert deduplicate_automatic_target(first, None, once=False) == first
    assert deduplicate_automatic_target(first, first, once=False) is None
    assert deduplicate_automatic_target(second, first, once=False) == second
    assert deduplicate_automatic_target(first, first, once=True) == first


def _environment(tmp_path: Path) -> dict[str, str]:
    return {
        "HYPERBOT_COLLECTOR_ENABLED": "true",
        "HYPERBOT_LIVE_ENABLED": "false",
        "HYPERBOT_SHADOW_ONLY": "true",
        "HYPERBOT_CODE_COMMIT": "a" * 40,
        "HYPERBOT_COLLECTOR_MARKETS": "BTC",
        "HYPERBOT_COLLECTOR_CHANNELS": "bbo",
        "HYPERBOT_DATA_ROOT": str(tmp_path / "data"),
        "HYPERBOT_RUNTIME_ROOT": str(tmp_path / "runtime"),
        "HYPERBOT_REVIEW_ROOT": str(tmp_path / "reviews"),
        "HYPERBOT_HEARTBEAT_INTERVAL_SECONDS": "0.1",
        "HYPERBOT_STALE_AFTER_SECONDS": "1",
        "HYPERBOT_RECONNECT_INITIAL_SECONDS": "0.01",
        "HYPERBOT_RECONNECT_MAX_SECONDS": "0.02",
        "HYPERBOT_STATUS_INTERVAL_SECONDS": "0.1",
        "HYPERBOT_HEALTH_MAX_AGE_SECONDS": "10",
        "HYPERBOT_MINIMUM_FREE_BYTES": "1",
    }


def test_ops_configuration_is_explicitly_enabled_and_secret_free(
    tmp_path: Path,
) -> None:
    safe = OpsSettings.from_environment(_environment(tmp_path), require_enabled=True)

    assert safe.collector_enabled
    assert not safe.live_enabled
    assert safe.shadow_only
    assert safe.code_commit == "a" * 40
    assert safe.code_version == f"0.1.0+g{'a' * 40}"
    assert safe.markets == ("BTC",)
    assert safe.persistence_batch_size == 256
    assert safe.fsync_every_records == 100
    assert safe.data_mount_sentinel is None
    assert not safe.archive_enabled
    assert safe.archive_root is None
    assert safe.hot_retention_days == 30
    assert len(safe.config_sha256) == 64

    outcomes = {
        **_environment(tmp_path),
        "HYPERBOT_COLLECTOR_MARKETS": "#123,cash:AMZN",
    }
    assert OpsSettings.from_environment(outcomes).markets == ("#123", "cash:AMZN")

    disabled = {**_environment(tmp_path), "HYPERBOT_COLLECTOR_ENABLED": "false"}
    with pytest.raises(OpsConfigurationError, match="requires"):
        OpsSettings.from_environment(disabled, require_enabled=True)

    live = {**_environment(tmp_path), "HYPERBOT_LIVE_ENABLED": "true"}
    with pytest.raises(OpsConfigurationError, match="not implemented"):
        OpsSettings.from_environment(live)

    secret = {**_environment(tmp_path), "TRIDENT_SECRET_KEY": "forbidden"}
    with pytest.raises(OpsConfigurationError, match="must not be exposed"):
        OpsSettings.from_environment(secret)

    missing_commit = _environment(tmp_path)
    missing_commit.pop("HYPERBOT_CODE_COMMIT")
    with pytest.raises(OpsConfigurationError, match="deployed commit"):
        OpsSettings.from_environment(missing_commit)

    non_finite = {
        **_environment(tmp_path),
        "HYPERBOT_STALE_AFTER_SECONDS": "nan",
    }
    with pytest.raises(OpsConfigurationError, match="must be >="):
        OpsSettings.from_environment(non_finite)


def test_data_volume_sentinel_is_optional_and_fail_closed(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    sentinel = tmp_path / "data" / ".hyperbot-volume"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("volume-ready\n", encoding="utf-8")
    guarded = {
        **environment,
        "HYPERBOT_DATA_MOUNT_SENTINEL": str(sentinel),
    }

    settings = OpsSettings.from_environment(guarded)

    assert settings.data_mount_sentinel == sentinel

    sentinel.unlink()
    with pytest.raises(OpsConfigurationError, match="missing or invalid"):
        OpsSettings.from_environment(guarded)

    sentinel.symlink_to(tmp_path / "missing-volume")
    with pytest.raises(OpsConfigurationError, match="missing or invalid"):
        OpsSettings.from_environment(guarded)

    unrelated = {
        **environment,
        "HYPERBOT_DATA_MOUNT_SENTINEL": str(tmp_path / "outside" / ".hyperbot-volume"),
    }
    with pytest.raises(OpsConfigurationError, match="storage-root hierarchy"):
        OpsSettings.from_environment(unrelated)


def test_archive_requires_a_distinct_guarded_mount(tmp_path: Path) -> None:
    environment = {
        **_environment(tmp_path),
        "HYPERBOT_ARCHIVE_ENABLED": "true",
        "HYPERBOT_ARCHIVE_ROOT": str(tmp_path / "archive" / "collector"),
        "HYPERBOT_HOT_RETENTION_DAYS": "30",
        "HYPERBOT_MINIMUM_RETENTION_DAYS": "60",
    }

    with pytest.raises(OpsConfigurationError, match="ARCHIVE_MOUNT_SENTINEL"):
        OpsSettings.from_environment(environment)

    sentinel = tmp_path / "archive" / ".hyperbot-volume"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("archive-ready\n", encoding="utf-8")
    environment["HYPERBOT_ARCHIVE_MOUNT_SENTINEL"] = str(sentinel)
    settings = OpsSettings.from_environment(environment)

    assert settings.archive_enabled
    assert settings.archive_root == tmp_path / "archive" / "collector"
    assert settings.archive_mount_sentinel == sentinel

    overlapping = {
        **environment,
        "HYPERBOT_ARCHIVE_ROOT": str(tmp_path / "data" / "archive"),
        "HYPERBOT_ARCHIVE_MOUNT_SENTINEL": str(tmp_path / "data" / ".archive-volume"),
    }
    overlap_sentinel = tmp_path / "data" / ".archive-volume"
    overlap_sentinel.parent.mkdir(parents=True, exist_ok=True)
    overlap_sentinel.write_text("archive-ready\n", encoding="utf-8")
    with pytest.raises(OpsConfigurationError, match="separate"):
        OpsSettings.from_environment(overlapping)


def test_ops_configuration_builds_distinct_depth_and_breadth_profiles(
    tmp_path: Path,
) -> None:
    environment = _environment(tmp_path)
    environment.pop("HYPERBOT_COLLECTOR_MARKETS")
    environment.pop("HYPERBOT_COLLECTOR_CHANNELS")
    environment.update(
        {
            "HYPERBOT_COLLECTOR_DEPTH_MARKETS": "BTC,ETH",
            "HYPERBOT_COLLECTOR_BREADTH_MARKETS": "SOL,xyz:GOLD",
        }
    )

    settings = OpsSettings.from_environment(environment)

    assert settings.markets == ("BTC", "ETH", "SOL", "xyz:GOLD")
    assert settings.depth_markets == ("BTC", "ETH")
    assert settings.breadth_markets == ("SOL", "xyz:GOLD")
    assert settings.channels == ("l2Book", "bbo", "trades")
    assert settings.subscriptions == (
        ("l2Book", "BTC"),
        ("bbo", "BTC"),
        ("trades", "BTC"),
        ("l2Book", "ETH"),
        ("bbo", "ETH"),
        ("trades", "ETH"),
        ("bbo", "SOL"),
        ("trades", "SOL"),
        ("bbo", "xyz:GOLD"),
        ("trades", "xyz:GOLD"),
    )

    overlap = {
        **environment,
        "HYPERBOT_COLLECTOR_BREADTH_MARKETS": "ETH,SOL",
    }
    with pytest.raises(OpsConfigurationError, match="overlap"):
        OpsSettings.from_environment(overlap)

    empty = {
        **environment,
        "HYPERBOT_COLLECTOR_DEPTH_MARKETS": "",
        "HYPERBOT_COLLECTOR_BREADTH_MARKETS": "",
    }
    with pytest.raises(OpsConfigurationError, match="profile market"):
        OpsSettings.from_environment(empty)

    mixed = {
        **environment,
        "HYPERBOT_COLLECTOR_MARKETS": "BTC",
    }
    with pytest.raises(OpsConfigurationError, match="cannot be mixed"):
        OpsSettings.from_environment(mixed)

    excessive = {
        **environment,
        "HYPERBOT_COLLECTOR_DEPTH_MARKETS": ",".join(
            f"M{index}" for index in range(334)
        ),
        "HYPERBOT_COLLECTOR_BREADTH_MARKETS": "",
    }
    with pytest.raises(OpsConfigurationError, match="exceed"):
        OpsSettings.from_environment(excessive)

    unsafe_fsync = {
        **environment,
        "HYPERBOT_FSYNC_EVERY_RECORDS": "1001",
    }
    with pytest.raises(OpsConfigurationError, match="FSYNC"):
        OpsSettings.from_environment(unsafe_fsync)

    unsafe_batch = {
        **environment,
        "HYPERBOT_PERSISTENCE_BATCH_SIZE": "10001",
    }
    with pytest.raises(OpsConfigurationError, match="BATCH"):
        OpsSettings.from_environment(unsafe_batch)


def test_health_is_fail_closed_for_stale_status_disconnect_and_disk(
    tmp_path: Path,
) -> None:
    settings = OpsSettings.from_environment(_environment(tmp_path))
    now_ms = 1_800_000_000_000
    atomic_write_json(
        settings.status_path,
        {
            "state": "running",
            "updated_at_ms": now_ms,
            "public_only": True,
            "live_enabled": False,
            "shadow_only": True,
            "code_commit": settings.code_commit,
            "config_sha256": settings.config_sha256,
            "connected": True,
            "dropped_events": 0,
            "last_message_receive_ms": now_ms,
        },
    )

    assert evaluate_collector_health(settings, now_ms=now_ms).healthy

    stale = evaluate_collector_health(
        settings,
        now_ms=now_ms + settings.health_max_age_seconds * 1_000 + 1,
    )
    assert not stale.healthy
    assert {"status_stale", "public_feed_stale"} <= set(stale.reasons)

    impossible_reserve = replace(settings, minimum_free_bytes=10**30)
    low_disk = evaluate_collector_health(impossible_reserve, now_ms=now_ms)
    assert not low_disk.healthy
    assert "disk_reserve_low" in low_disk.reasons


def test_collector_runtime_publishes_health_and_stops_cleanly(tmp_path: Path) -> None:
    async def scenario() -> tuple[
        object,
        dict[str, object],
        int,
        list[dict[str, object]],
        object,
    ]:
        environment = _environment(tmp_path)
        environment.pop("HYPERBOT_COLLECTOR_MARKETS")
        environment.pop("HYPERBOT_COLLECTOR_CHANNELS")
        environment.update(
            {
                "HYPERBOT_COLLECTOR_DEPTH_MARKETS": "BTC",
                "HYPERBOT_COLLECTOR_BREADTH_MARKETS": "ETH",
            }
        )
        settings = OpsSettings.from_environment(environment)
        stop = asyncio.Event()
        subscriptions: list[dict[str, object]] = []

        async def handler(socket: ServerConnection) -> None:
            for _ in settings.subscriptions:
                raw = json.loads(await socket.recv())
                subscriptions.append(raw["subscription"])
            await socket.send(
                json.dumps(
                    {
                        "channel": "bbo",
                        "data": {
                            "coin": "BTC",
                            "time": int(time.time() * 1_000),
                            "bbo": [None, None],
                        },
                    }
                )
            )
            await stop.wait()

        async with serve(handler, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]

            def connection_factory(_url: str) -> object:
                return connect(f"ws://127.0.0.1:{port}")

            task = asyncio.create_task(
                run_collector_service(
                    settings,
                    stop,
                    connection_factory=connection_factory,  # type: ignore[arg-type]
                )
            )
            running: dict[str, object] = {}
            for _ in range(100):
                await asyncio.sleep(0.02)
                if settings.status_path.is_file():
                    running = json.loads(
                        settings.status_path.read_text(encoding="utf-8")
                    )
                    if running.get("connected") is True:
                        break
            health = evaluate_collector_health(
                settings, now_ms=int(time.time() * 1_000)
            )
            stop.set()
            metrics = await asyncio.wait_for(task, timeout=2)
        records = SegmentedEventStore(settings.data_root).read_records(
            "public-market-data"
        )
        payload = records[0].get("payload", {}) if records else {}
        context = payload.get("context", {}) if isinstance(payload, dict) else {}
        persisted_code_version = (
            context.get("code_version") if isinstance(context, dict) else None
        )
        return (
            metrics,
            running,
            len(records) if health.healthy else -1,
            subscriptions,
            persisted_code_version,
        )

    metrics, running, record_count, subscriptions, persisted_code_version = asyncio.run(
        scenario()
    )

    assert running["public_only"] is True
    assert running["code_commit"] == "a" * 40
    assert running["code_version"] == f"0.1.0+g{'a' * 40}"
    assert persisted_code_version == f"0.1.0+g{'a' * 40}"
    assert running["live_enabled"] is False
    assert running["depth_markets"] == ["BTC"]
    assert running["breadth_markets"] == ["ETH"]
    assert running["subscription_count"] == 5
    assert running["persistence_batch_size"] == 256
    assert running["fsync_every_records"] == 100
    assert running["persistence_queue_capacity"] == 10_000
    assert isinstance(running["persistence_queue_depth"], int)
    assert subscriptions == [
        {"coin": "BTC", "type": "l2Book"},
        {"coin": "BTC", "type": "bbo"},
        {"coin": "BTC", "type": "trades"},
        {"coin": "ETH", "type": "bbo"},
        {"coin": "ETH", "type": "trades"},
    ]
    assert metrics.received_messages == 1
    assert record_count == 1


def test_daily_maintenance_is_idempotent_and_never_deletes_raw(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = OpsSettings.from_environment(_environment(tmp_path))
    report_date = date(2026, 8, 10)
    day_begin = int(datetime(2026, 8, 10, tzinfo=UTC).timestamp() * 1_000)
    timestamps = iter((day_begin + 1_000, day_begin + 2_000))
    store = SegmentedEventStore(
        settings.data_root,
        fsync=False,
        clock_ms=lambda: next(timestamps),
    )
    context = EventContext("ops-quality", "test", "e" * 64, TimeSource.EXCHANGE)
    store.append(
        "public-market-data",
        PublicMarketDataEvent(
            context=context,
            channel="bbo",
            coin="BTC",
            exchange_ts_ms=day_begin + 990,
            receive_ts_ms=day_begin + 1_000,
            receive_monotonic_ns=1_000,
            local_sequence=0,
            payload_json=json.dumps(
                {
                    "coin": "BTC",
                    "time": day_begin + 990,
                    "bbo": [
                        {"px": "99", "sz": "1"},
                        {"px": "101", "sz": "1"},
                    ],
                }
            ),
        ),
    )
    store.append(
        "collector-control",
        CollectorControlEvent(
            context=context,
            kind=CollectorControlKind.CONNECTED,
            receive_ts_ms=day_begin + 1_000,
            receive_monotonic_ns=1_000,
            connection_attempt=1,
            dropped_messages=0,
            reason=None,
        ),
    )
    first = run_daily_maintenance(
        settings,
        report_date=report_date,
        generated_at_ms=1_786_406_400_000,
    )
    second = run_daily_maintenance(
        settings,
        report_date=report_date,
        generated_at_ms=1_786_406_500_000,
    )

    assert first.report_json.is_file()
    assert first.report_json.with_suffix(".json.sha256").is_file()
    assert first.compressed_segments == 2
    assert second.reused
    reused_status = json.loads(
        settings.maintenance_status_path.read_text(encoding="utf-8")
    )
    assert reused_status["archive_enabled"] is False
    assert reused_status["hot_retention_days"] == 30
    assert reused_status["archived_segments"] == 0
    assert reused_status["archived_bytes"] == 0
    assert not list(settings.data_root.rglob("*.jsonl.open"))
    assert len(list(settings.data_root.rglob("*.gz"))) == 2
    assert first.qualified_day
    marker_path = (
        settings.runtime_root / "maintenance" / f"{report_date.isoformat()}.json"
    )
    marker_path.unlink()

    def unexpected_analysis(*args: object, **kwargs: object) -> object:
        raise AssertionError("a checksummed compatible report must be reused")

    monkeypatch.setattr(
        DailyQualityAnalyzer,
        "analyze_ordered",
        unexpected_analysis,
    )
    recovered = run_daily_maintenance(
        settings,
        report_date=report_date,
        generated_at_ms=1_786_406_450_000,
    )
    assert recovered.reused
    assert recovered.report_json == first.report_json
    assert recovered.compressed_segments == 0

    assert second.reused
    changed_settings = replace(settings, fsync_every_records=101)
    third = run_daily_maintenance(
        changed_settings,
        report_date=report_date,
        generated_at_ms=1_786_406_600_000,
    )
    assert third.reused
    assert third.report_json == first.report_json
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    assert marker["raw_data_deleted"] is False
    maintenance_status = json.loads(
        changed_settings.maintenance_status_path.read_text(encoding="utf-8")
    )
    assert maintenance_status["reused"] is True
    assert maintenance_status["active_config_sha256"] == (
        changed_settings.config_sha256
    )


def test_daily_maintenance_refuses_an_incomplete_utc_day(tmp_path: Path) -> None:
    settings = OpsSettings.from_environment(_environment(tmp_path))
    report_date = date(2026, 8, 10)
    generated_at_ms = int(datetime(2026, 8, 10, 12, tzinfo=UTC).timestamp() * 1_000)

    with pytest.raises(ValueError, match="completed UTC day"):
        run_daily_maintenance(
            settings,
            report_date=report_date,
            generated_at_ms=generated_at_ms,
        )

    assert not settings.maintenance_status_path.exists()


def test_daily_maintenance_archives_only_verified_old_segments(
    tmp_path: Path,
) -> None:
    base = OpsSettings.from_environment(_environment(tmp_path))
    archive_root = tmp_path / "archive" / "collector"
    settings = replace(
        base,
        archive_enabled=True,
        archive_root=archive_root,
        archive_mount_sentinel=tmp_path / "archive" / ".hyperbot-volume",
        hot_retention_days=7,
    )
    old_begin = int(datetime(2026, 8, 1, tzinfo=UTC).timestamp() * 1_000)
    timestamps = iter((old_begin + 1_000, old_begin + 2_000))
    store = SegmentedEventStore(
        settings.data_root,
        fsync=False,
        clock_ms=lambda: next(timestamps),
    )
    context = EventContext("archive-quality", "test", "f" * 64, TimeSource.EXCHANGE)
    store.append(
        "public-market-data",
        PublicMarketDataEvent(
            context=context,
            channel="bbo",
            coin="BTC",
            exchange_ts_ms=old_begin + 990,
            receive_ts_ms=old_begin + 1_000,
            receive_monotonic_ns=1_000,
            local_sequence=0,
            payload_json=json.dumps({"coin": "BTC", "bbo": [None, None]}),
        ),
    )
    store.append(
        "collector-control",
        CollectorControlEvent(
            context=context,
            kind=CollectorControlKind.CONNECTED,
            receive_ts_ms=old_begin + 2_000,
            receive_monotonic_ns=2_000,
            connection_attempt=1,
            dropped_messages=0,
            reason=None,
        ),
    )

    result = run_daily_maintenance(
        settings,
        report_date=date(2026, 8, 10),
        generated_at_ms=int(datetime(2026, 8, 11, tzinfo=UTC).timestamp() * 1_000),
    )

    assert result.archived_segments == 2
    assert result.archived_bytes > 0
    assert not list(settings.data_root.rglob("*.jsonl.gz"))
    assert len(list(archive_root.rglob("*.jsonl.gz"))) == 2
    archived_store = SegmentedEventStore(
        settings.data_root,
        archive_root=archive_root,
        fsync=False,
    )
    assert archived_store.validate("public-market-data").record_count == 1
    marker = json.loads(
        (settings.runtime_root / "maintenance" / "2026-08-10.json").read_text(
            encoding="utf-8"
        )
    )
    assert marker["archive_enabled"] is True
    assert marker["archived_segments"] == 2
    assert marker["raw_data_deleted"] is False


def test_deployment_artifacts_remain_disabled_by_default() -> None:
    environment = (PROJECT_ROOT / ".env.hyperbot.example").read_text(encoding="utf-8")
    compose = (PROJECT_ROOT / "docker-compose.hyperbot.yml").read_text(encoding="utf-8")
    project = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")

    assert "HYPERBOT_COLLECTOR_ENABLED=false" in environment
    assert "HYPERBOT_LIVE_ENABLED=false" in environment
    assert "HYPERBOT_SHADOW_ONLY=true" in environment
    assert "HYPERBOT_CODE_COMMIT=" in environment
    assert "HYPERBOT_COLLECTOR_DEPTH_MARKETS=BTC,ETH,HYPE,SOL" in environment
    assert "HYPERBOT_COLLECTOR_BREADTH_MARKETS=PUMP,ZEC,XRP" in environment
    assert "HYPERBOT_COLLECTOR_MARKETS=" not in environment
    assert "HYPERBOT_COLLECTOR_CHANNELS=" not in environment
    assert "HYPERBOT_FSYNC_EVERY_RECORDS=100" in environment
    assert "HYPERBOT_PERSISTENCE_BATCH_SIZE=256" in environment
    assert "HYPERBOT_DATA_MOUNT_SENTINEL=" in environment
    assert "HYPERBOT_ARCHIVE_ENABLED=false" in environment
    assert "HYPERBOT_ARCHIVE_MOUNT_SENTINEL=" in environment
    assert "HYPERBOT_HOT_RETENTION_DAYS=30" in environment
    assert "HYPERBOT_DATA_MOUNT_SENTINEL" in compose
    assert "HYPERBOT_ARCHIVE_MOUNT_SENTINEL" in compose
    assert "/app/archive" in compose
    assert 'profiles: ["collector"]' in compose
    assert "read_only: true" in compose
    assert sum(line.strip() == "ports:" for line in compose.splitlines()) == 1
    assert "HYPERBOT_UI_PORT:-3002" in compose
    assert "hyperbot_ui_password" in compose
    assert "HYPERBOT_UI_AUTH_REQUIRED=true" in environment
    assert "HYPERBOT_UI_AUTH_PASSWORD=" not in environment
    assert "HYPERBOT_ALERT_WEBHOOK_URL" not in compose
    assert "TRIDENT_SECRET_KEY" not in compose
    assert "hyperliquid-python-sdk" not in project

    deploy = subprocess.run(
        ["bash", "deploy.sh", "--dry-run"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    fetch = subprocess.run(
        ["bash", "scripts/fetch_hyperbot_data.sh", "--dry-run"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert "start=false" in deploy.stdout
    assert "data/server-fetches" in fetch.stdout


def test_watchdog_alerts_on_failure_cooldown_and_recovery(tmp_path: Path) -> None:
    settings = OpsSettings.from_environment(_environment(tmp_path))
    webhook_file = tmp_path / "alert_webhook_url"
    webhook_file.write_text("https://alerts.invalid/hyperbot\n", encoding="utf-8")
    watchdog = WatchdogSettings.from_environment(
        {
            "HYPERBOT_WATCHDOG_INTERVAL_SECONDS": "10",
            "HYPERBOT_WATCHDOG_START_GRACE_SECONDS": "30",
            "HYPERBOT_ALERT_COOLDOWN_SECONDS": "60",
            "HYPERBOT_ALERT_WEBHOOK_FILE": str(webhook_file),
        }
    )
    failed = evaluate_collector_health(settings, now_ms=100_000)

    assert not should_alert(
        healthy=False,
        previous_healthy=None,
        now_ms=120_000,
        started_at_ms=100_000,
        last_alert_at_ms=None,
        settings=watchdog,
    )
    assert should_alert(
        healthy=False,
        previous_healthy=True,
        now_ms=131_000,
        started_at_ms=100_000,
        last_alert_at_ms=None,
        settings=watchdog,
    )
    assert not should_alert(
        healthy=False,
        previous_healthy=False,
        now_ms=150_000,
        started_at_ms=100_000,
        last_alert_at_ms=131_000,
        settings=watchdog,
    )
    assert should_alert(
        healthy=True,
        previous_healthy=False,
        now_ms=151_000,
        started_at_ms=100_000,
        last_alert_at_ms=131_000,
        settings=watchdog,
    )
    payload = json.loads(alert_payload(failed, recovered=False))
    assert payload["service"] == "hyperbot-collector"
    assert "webhook" not in payload

    webhook_file.write_text("http://example.invalid/hook\n", encoding="utf-8")
    with pytest.raises(OpsConfigurationError, match="must use HTTPS"):
        WatchdogSettings.from_environment(
            {"HYPERBOT_ALERT_WEBHOOK_FILE": str(webhook_file)}
        )
