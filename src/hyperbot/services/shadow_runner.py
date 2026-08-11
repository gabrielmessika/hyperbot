"""Shadow-only orchestration, fill evaluation, and 14-day qualification."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Protocol, cast

from hyperbot.execution import (
    ShadowExecutionGateway,
    ShadowReconciliation,
    SimulatedExchangeState,
)
from hyperbot.models import (
    DomainEvent,
    QuoteIntent,
    RiskAuditEvent,
    ShadowFillEvaluationEvent,
    ShadowQuoteEvent,
    ShadowQuoteStatus,
)
from hyperbot.replay import FillModelKind, ReplayResult, SimulatedFill
from hyperbot.risk import (
    ApprovedIntent,
    IntentMetadata,
    PortfolioState,
    RiskDecision,
    RiskSupervisor,
)
from hyperbot.strategies import MarketState, Strategy

SHADOW_REPORT_SCHEMA_VERSION = 1


class ShadowStore(Protocol):
    def append(self, stream: str, event: DomainEvent) -> object: ...


class ShadowGateStage(StrEnum):
    INSUFFICIENT_FOURTEEN_DAYS = "insufficient_fourteen_days"
    FOURTEEN_DAYS_COMPLETE = "fourteen_days_complete"


@dataclass(frozen=True, slots=True)
class ShadowCycleResult:
    intents: tuple[QuoteIntent, ...]
    decisions: tuple[RiskDecision, ...]
    approvals: tuple[ApprovedIntent, ...]
    staged_quote_ids: tuple[str, ...]
    restart_blocked: bool


@dataclass(frozen=True, slots=True)
class FillModelComparison:
    central_result_sha256: str
    pessimistic_result_sha256: str
    central_fill_count: int
    pessimistic_fill_count: int
    central_filled_notional_usd: Decimal
    pessimistic_filled_notional_usd: Decimal
    central_markout_30s_usd: Decimal
    pessimistic_markout_30s_usd: Decimal


@dataclass(frozen=True, slots=True)
class ShadowDailyReport:
    schema_version: int
    report_date: str
    run_id: str
    code_version: str
    config_sha256: str
    intent_count: int
    approved_count: int
    rejected_count: int
    staged_quote_count: int
    evaluated_quote_count: int
    predicted_fill_count: int
    negative_markout_30s_count: int
    risk_violation_count: int
    restart_divergence_count: int
    quality_day_qualified: bool
    replay_compatible: bool
    latency_stress_tolerable: bool
    qualified_day: bool
    qualification_reasons: tuple[str, ...]
    shadow_only: bool


@dataclass(frozen=True, slots=True)
class ShadowQualificationGate:
    stage: ShadowGateStage
    observed_days: int
    consecutive_qualified_days: int
    required_days: int
    missing_dates: tuple[str, ...]
    eligible_for_canary_discussion: bool
    canary_authorized: bool


class ShadowRunner:
    """Runs strategy/risk/gateway contracts without any live execution path."""

    def __init__(
        self,
        *,
        strategy: Strategy,
        supervisor: RiskSupervisor,
        gateway: ShadowExecutionGateway,
        store: ShadowStore,
    ) -> None:
        self.strategy = strategy
        self.supervisor = supervisor
        self.gateway = gateway
        self.store = store
        self._restart_blocked = False
        self._intent_count = 0
        self._approved_count = 0
        self._rejected_count = 0
        self._risk_violation_count = 0
        self._restart_divergence_count = 0
        self._evaluations: list[ShadowFillEvaluationEvent] = []

    @property
    def restart_blocked(self) -> bool:
        return self._restart_blocked

    def process_market_state(
        self,
        *,
        state: MarketState,
        metadata: IntentMetadata,
        portfolio: PortfolioState,
    ) -> ShadowCycleResult:
        if self._restart_blocked:
            return ShadowCycleResult((), (), (), (), True)
        intents = self.strategy.on_market_state(state)
        for intent in intents:
            self.store.append("shadow-intents", intent)
        decisions = self.supervisor.evaluate_batch(
            tuple((intent, metadata) for intent in intents),
            portfolio,
        )
        approvals: list[ApprovedIntent] = []
        for intent, decision in zip(intents, decisions, strict=True):
            if decision.approved is not None:
                approvals.append(decision.approved)
                reasons = decision.approved.reason_codes
                approved_size = decision.approved.approved_size
                self._approved_count += 1
            else:
                reasons = decision.rejection_reasons or ("rejected_without_reason",)
                approved_size = None
                self._rejected_count += 1
                self._risk_violation_count += 1
            self.store.append(
                "shadow-risk-audit",
                RiskAuditEvent(
                    context=intent.context,
                    decision_ts_ms=portfolio.observed_at_ms,
                    intent_id=intent.intent_id,
                    approved=decision.approved is not None,
                    approved_size=approved_size,
                    action=decision.action.value,
                    reason_codes=reasons,
                ),
            )
        self._intent_count += len(intents)
        staged = self.gateway.stage_approved(
            tuple(approvals),
            timestamp_ms=portfolio.observed_at_ms,
        )
        for record, approval in zip(staged, approvals, strict=True):
            self.store.append(
                "shadow-quotes",
                ShadowQuoteEvent(
                    context=approval.intent.context,
                    decision_id=record.decision_id,
                    intent_id=record.intent_id,
                    market=record.market,
                    side=record.side,
                    price=record.price,
                    size=record.size,
                    staged_ts_ms=record.staged_at_ms,
                    expires_ts_ms=(
                        record.staged_at_ms + approval.intent.ttl_ms
                    ),
                    status=ShadowQuoteStatus.STAGED,
                    shadow_only=True,
                ),
            )
        return ShadowCycleResult(
            intents=intents,
            decisions=decisions,
            approvals=tuple(approvals),
            staged_quote_ids=tuple(record.decision_id for record in staged),
            restart_blocked=False,
        )

    def reconcile_restart(
        self,
        *,
        local_state: SimulatedExchangeState,
        exchange_state: SimulatedExchangeState,
    ) -> ShadowReconciliation:
        result = self.gateway.reconcile(
            local_state=local_state,
            exchange_state=exchange_state,
        )
        self._restart_blocked = not result.clean
        if not result.clean:
            self._restart_divergence_count += 1
            self._risk_violation_count += 1
        return result

    def record_replay_evaluation(
        self,
        *,
        result: ReplayResult,
        quote_ids: tuple[str, ...],
        evaluation_ts_ms: int,
        context: MarketState,
    ) -> tuple[ShadowFillEvaluationEvent, ...]:
        events: list[ShadowFillEvaluationEvent] = []
        for quote_id in quote_ids:
            fills = tuple(fill for fill in result.fills if fill.quote_id == quote_id)
            filled_size = sum((fill.size for fill in fills), Decimal(0))
            event = ShadowFillEvaluationEvent(
                context=context.context,
                evaluation_ts_ms=evaluation_ts_ms,
                replay_result_sha256=result.result_sha256,
                quote_id=quote_id,
                model=result.model.value,
                predicted_fill=filled_size > 0,
                filled_size=filled_size,
                markout_100ms=_sum_markout(fills, "markout_100ms"),
                markout_1s=_sum_markout(fills, "markout_1s"),
                markout_5s=_sum_markout(fills, "markout_5s"),
                markout_30s=_sum_markout(fills, "markout_30s"),
            )
            events.append(event)
            self._evaluations.append(event)
            self.store.append("shadow-fill-evaluations", event)
        return tuple(events)

    def daily_report(
        self,
        *,
        report_date: date,
        state: MarketState,
        quality_day_qualified: bool,
        replay_compatible: bool,
        latency_stress_tolerable: bool,
    ) -> ShadowDailyReport:
        reasons: list[str] = []
        if self._risk_violation_count:
            reasons.append("risk_violations")
        if self._restart_divergence_count:
            reasons.append("restart_divergence")
        if not quality_day_qualified:
            reasons.append("quality_day_not_qualified")
        if not replay_compatible:
            reasons.append("shadow_replay_divergence")
        if not latency_stress_tolerable:
            reasons.append("latency_stress_not_tolerable")
        return ShadowDailyReport(
            schema_version=SHADOW_REPORT_SCHEMA_VERSION,
            report_date=report_date.isoformat(),
            run_id=state.context.run_id,
            code_version=state.context.code_version,
            config_sha256=state.context.config_hash,
            intent_count=self._intent_count,
            approved_count=self._approved_count,
            rejected_count=self._rejected_count,
            staged_quote_count=len(self.gateway.records),
            evaluated_quote_count=len(self._evaluations),
            predicted_fill_count=sum(
                event.predicted_fill for event in self._evaluations
            ),
            negative_markout_30s_count=sum(
                event.markout_30s is not None and event.markout_30s < 0
                for event in self._evaluations
            ),
            risk_violation_count=self._risk_violation_count,
            restart_divergence_count=self._restart_divergence_count,
            quality_day_qualified=quality_day_qualified,
            replay_compatible=replay_compatible,
            latency_stress_tolerable=latency_stress_tolerable,
            qualified_day=not reasons,
            qualification_reasons=tuple(reasons),
            shadow_only=True,
        )


def _sum_markout(
    fills: tuple[SimulatedFill, ...],
    field_name: str,
) -> Decimal | None:
    values = [
        cast(Decimal, getattr(fill, field_name))
        for fill in fills
        if getattr(fill, field_name) is not None
    ]
    return sum(values, Decimal(0)) if values else None


def compare_fill_models(
    central: ReplayResult,
    pessimistic: ReplayResult,
) -> FillModelComparison:
    if central.model is not FillModelKind.CENTRAL:
        raise ValueError("central result has the wrong fill model")
    if pessimistic.model is not FillModelKind.PESSIMISTIC:
        raise ValueError("pessimistic result has the wrong fill model")
    return FillModelComparison(
        central_result_sha256=central.result_sha256,
        pessimistic_result_sha256=pessimistic.result_sha256,
        central_fill_count=len(central.fills),
        pessimistic_fill_count=len(pessimistic.fills),
        central_filled_notional_usd=central.filled_notional_usd,
        pessimistic_filled_notional_usd=pessimistic.filled_notional_usd,
        central_markout_30s_usd=central.gross_markout_30s_usd,
        pessimistic_markout_30s_usd=pessimistic.gross_markout_30s_usd,
    )


def evaluate_shadow_qualification(
    reports: tuple[ShadowDailyReport, ...],
    *,
    required_days: int = 14,
) -> ShadowQualificationGate:
    if required_days <= 0:
        raise ValueError("required_days must be positive")
    by_date = {date.fromisoformat(report.report_date): report for report in reports}
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
            if current != expected or not by_date[current].qualified_day:
                break
            consecutive += 1
            expected -= timedelta(days=1)
    complete = consecutive >= required_days
    return ShadowQualificationGate(
        stage=(
            ShadowGateStage.FOURTEEN_DAYS_COMPLETE
            if complete
            else ShadowGateStage.INSUFFICIENT_FOURTEEN_DAYS
        ),
        observed_days=len(by_date),
        consecutive_qualified_days=consecutive,
        required_days=required_days,
        missing_dates=tuple(missing),
        eligible_for_canary_discussion=complete,
        canary_authorized=False,
    )


def write_shadow_daily_report(
    report: ShadowDailyReport,
    output_root: str | Path,
) -> tuple[Path, Path]:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    stem = f"shadow-{report.report_date}-{report.run_id}"
    json_path = root / f"{stem}.json"
    markdown_path = root / f"{stem}.md"
    if json_path.exists() or markdown_path.exists():
        raise FileExistsError(f"shadow report already exists: {stem}")
    payload = asdict(report)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ).encode("utf-8") + b"\n"
    json_path.write_bytes(encoded)
    json_path.with_suffix(".json.sha256").write_text(
        f"{hashlib.sha256(encoded).hexdigest()}  {json_path.name}\n",
        encoding="ascii",
    )
    lines = [
        f"# Shadow — {report.report_date}",
        "",
        f"- shadow only : `{str(report.shadow_only).lower()}` ;",
        f"- journée qualifiée : `{str(report.qualified_day).lower()}` ;",
        f"- intentions/approuvées/rejetées : {report.intent_count}/"
        f"{report.approved_count}/{report.rejected_count} ;",
        f"- quotes évaluées/fills prédits : {report.evaluated_quote_count}/"
        f"{report.predicted_fill_count} ;",
        f"- violations risque : {report.risk_violation_count} ;",
        f"- divergences restart : {report.restart_divergence_count}.",
    ]
    if report.qualification_reasons:
        lines.extend(("", "## Raisons de non-qualification", ""))
        lines.extend(f"- `{reason}`" for reason in report.qualification_reasons)
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, markdown_path
