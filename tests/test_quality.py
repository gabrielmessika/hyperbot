from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from hyperbot.models import (
    CollectorControlEvent,
    CollectorControlKind,
    EventContext,
    PublicMarketDataEvent,
    TimeSource,
)
from hyperbot.quality import (
    DailyQualityAnalyzer,
    GapCause,
    QualityConfig,
    QualityGateStage,
    evaluate_quality_gate,
    write_daily_quality_report,
)

REPORT_DATE = date(2026, 8, 10)
DAY_BEGIN = int(datetime(2026, 8, 10, tzinfo=UTC).timestamp() * 1000)
DAY_MS = 86_400_000


def _context() -> EventContext:
    return EventContext("quality-test", "test", "d" * 64, TimeSource.EXCHANGE)


def _market_event(
    *,
    timestamp: int,
    sequence: int,
    channel: str = "bbo",
    payload: dict[str, object] | None = None,
    latency_ms: int = 10,
) -> PublicMarketDataEvent:
    body = payload or {
        "coin": "BTC",
        "time": timestamp - latency_ms,
        "bbo": [
            {"px": "99", "sz": "2"},
            {"px": "101", "sz": "3"},
        ],
    }
    return PublicMarketDataEvent(
        context=_context(),
        channel=channel,
        coin="BTC",
        exchange_ts_ms=timestamp - latency_ms,
        receive_ts_ms=timestamp,
        receive_monotonic_ns=timestamp * 1_000_000,
        local_sequence=sequence,
        payload_json=json.dumps(body),
    )


def _control(kind: CollectorControlKind, timestamp: int) -> CollectorControlEvent:
    return CollectorControlEvent(
        context=_context(),
        kind=kind,
        receive_ts_ms=timestamp,
        receive_monotonic_ns=timestamp * 1_000_000,
        connection_attempt=1,
        dropped_messages=0,
        reason="fixture",
    )


def test_quality_metrics_and_gap_causes_are_explicit() -> None:
    events = [
        _market_event(timestamp=DAY_BEGIN, sequence=0, latency_ms=5),
        _market_event(
            timestamp=DAY_BEGIN + 5_000,
            sequence=1,
            channel="l2Book",
            latency_ms=15,
            payload={
                "coin": "BTC",
                "time": DAY_BEGIN + 4_985,
                "levels": [
                    [{"px": "99", "sz": "2"}, {"px": "98", "sz": "4"}],
                    [{"px": "101", "sz": "3"}, {"px": "102", "sz": "5"}],
                ],
            },
        ),
        _market_event(
            timestamp=DAY_BEGIN + 5_100,
            sequence=2,
            channel="trades",
            latency_ms=20,
            payload={
                "coin": "BTC",
                "time": DAY_BEGIN + 5_080,
                "px": "100",
                "sz": "0.5",
            },
        ),
        _market_event(timestamp=DAY_BEGIN + DAY_MS - 100, sequence=3),
    ]
    controls = [
        _control(CollectorControlKind.CONNECTED, DAY_BEGIN),
        _control(CollectorControlKind.DISCONNECTED, DAY_BEGIN + 1_000),
        _control(CollectorControlKind.RECONNECTED, DAY_BEGIN + 4_000),
        _control(CollectorControlKind.SHUTDOWN, DAY_BEGIN + DAY_MS),
    ]
    report = DailyQualityAnalyzer(
        QualityConfig(
            expected_markets=("BTC",),
            stale_after_ms=500,
            major_gap_ms=1_000,
        )
    ).analyze(
        report_date=REPORT_DATE,
        market_events=events,
        control_events=controls,
        generated_at_ms=DAY_BEGIN + DAY_MS,
        run_id="quality-run",
        code_version="test",
        source_config_hash="d" * 64,
    )

    market = report.markets[0]
    assert report.collector_outage_count == 1
    assert {gap.cause for gap in report.gaps} == {
        GapCause.COLLECTOR_OUTAGE,
        GapCause.MARKET_STALE,
    }
    assert market.latency_p50_ms == 12
    assert market.latency_p95_ms == 19
    assert market.latency_p99_ms == 20
    assert market.spread_p50_bps == Decimal("200")
    assert market.average_bid_depth == Decimal("10") / 3
    assert market.average_ask_depth == Decimal("14") / 3
    assert market.trade_count == 1
    assert market.trade_notional_usd == Decimal("50.0")
    assert not report.qualified_day


def test_daily_json_markdown_and_checksum_are_written(tmp_path: Path) -> None:
    analyzer = DailyQualityAnalyzer(
        QualityConfig(
            expected_markets=("BTC",),
            stale_after_ms=DAY_MS,
            major_gap_ms=DAY_MS,
        )
    )
    report = analyzer.analyze(
        report_date=REPORT_DATE,
        market_events=[_market_event(timestamp=DAY_BEGIN, sequence=0)],
        control_events=[],
        generated_at_ms=DAY_BEGIN + DAY_MS,
        run_id="quality-output",
        code_version="test",
        source_config_hash="d" * 64,
    )

    json_path, markdown_path = write_daily_quality_report(report, tmp_path)
    decoded = json.loads(json_path.read_text(encoding="utf-8"))

    assert decoded["dataset_tier"] == "A"
    assert decoded["quality_config_hash"] == analyzer.config.sha256
    assert markdown_path.read_text(encoding="utf-8").startswith("# Qualité des données")
    assert json_path.with_suffix(".json.sha256").is_file()


def test_ordered_analysis_matches_batch_and_cleans_spill_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("hyperbot.quality._PERCENTILE_SORT_CHUNK_VALUES", 2)
    analyzer = DailyQualityAnalyzer(
        QualityConfig(
            expected_markets=("BTC",),
            stale_after_ms=500,
            major_gap_ms=1_000,
        )
    )
    events = [
        _market_event(timestamp=DAY_BEGIN + 100, sequence=0, latency_ms=5),
        _market_event(timestamp=DAY_BEGIN + 600, sequence=1, latency_ms=15),
        _market_event(
            timestamp=DAY_BEGIN + 700,
            sequence=2,
            channel="trades",
            payload={"coin": "BTC", "px": "100", "sz": "0.25"},
        ),
    ]
    controls = [
        _control(CollectorControlKind.CONNECTED, DAY_BEGIN),
        _control(CollectorControlKind.SHUTDOWN, DAY_BEGIN + DAY_MS),
    ]
    expected = analyzer.analyze(
        report_date=REPORT_DATE,
        market_events=events,
        control_events=controls,
        generated_at_ms=DAY_BEGIN + DAY_MS,
        run_id="quality-batch",
        code_version="test",
        source_config_hash="d" * 64,
    )
    progress: list[int] = []

    actual = analyzer.analyze_ordered(
        report_date=REPORT_DATE,
        market_events=iter(events),
        control_events=iter(controls),
        generated_at_ms=DAY_BEGIN + DAY_MS,
        run_id="quality-batch",
        code_version="test",
        source_config_hash="d" * 64,
        spill_root=tmp_path,
        progress=progress.append,
    )

    assert actual == expected
    assert progress[-1] == len(events)
    assert list(tmp_path.iterdir()) == []


def test_time_outside_a_collector_session_is_not_market_inactivity() -> None:
    report = DailyQualityAnalyzer(
        QualityConfig(
            expected_markets=("BTC",),
            stale_after_ms=500,
            major_gap_ms=1_000,
        )
    ).analyze(
        report_date=REPORT_DATE,
        market_events=[
            _market_event(timestamp=DAY_BEGIN + 1_000, sequence=0),
            _market_event(timestamp=DAY_BEGIN + 2_000, sequence=1),
        ],
        control_events=[
            _control(CollectorControlKind.CONNECTED, DAY_BEGIN + 900),
            _control(CollectorControlKind.SHUTDOWN, DAY_BEGIN + 2_100),
        ],
        generated_at_ms=DAY_BEGIN + DAY_MS,
        run_id="quality-session",
        code_version="test",
        source_config_hash="d" * 64,
    )

    causes = {gap.cause for gap in report.gaps}
    assert GapCause.COLLECTOR_NOT_RUNNING in causes
    assert report.markets[0].collector_not_running_gap_count > 0


def test_quality_gate_requires_consecutive_qualified_days() -> None:
    analyzer = DailyQualityAnalyzer(
        QualityConfig(
            expected_markets=("BTC",),
            stale_after_ms=DAY_MS,
            major_gap_ms=DAY_MS,
        )
    )
    base = analyzer.analyze(
        report_date=REPORT_DATE,
        market_events=[_market_event(timestamp=DAY_BEGIN, sequence=0)],
        control_events=[],
        generated_at_ms=DAY_BEGIN + DAY_MS,
        run_id="quality-gate",
        code_version="test",
        source_config_hash="d" * 64,
    )
    seven = [
        replace(base, report_date=(REPORT_DATE + timedelta(days=index)).isoformat())
        for index in range(7)
    ]
    thirty = [
        replace(base, report_date=(REPORT_DATE + timedelta(days=index)).isoformat())
        for index in range(30)
    ]

    assert evaluate_quality_gate(seven[:6]).stage is (
        QualityGateStage.INSUFFICIENT_SEVEN_DAYS
    )
    assert evaluate_quality_gate(seven).stage is QualityGateStage.COLLECT_TO_THIRTY_DAYS
    assert evaluate_quality_gate(thirty).stage is QualityGateStage.THIRTY_DAYS_COMPLETE

    failed_last = [*seven[:-1], replace(seven[-1], qualified_day=False)]
    result = evaluate_quality_gate(failed_last)
    assert result.consecutive_qualified_days == 0
    assert result.stage is QualityGateStage.INSUFFICIENT_SEVEN_DAYS
