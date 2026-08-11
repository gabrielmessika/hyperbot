"""Shadow gateway that records approvals and cannot send exchange orders."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from hyperbot.models import Side
from hyperbot.risk import ApprovedIntent


@dataclass(frozen=True, slots=True)
class ShadowQuoteRecord:
    decision_id: str
    intent_id: str
    market: str
    side: Side
    price: Decimal
    size: Decimal
    staged_at_ms: int
    shadow_only: bool = True


@dataclass(frozen=True, slots=True)
class SimulatedExchangeState:
    observed_at_ms: int
    positions: tuple[tuple[str, Decimal], ...]
    open_order_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.observed_at_ms < 0:
            raise ValueError("observed_at_ms must be non-negative")
        if len(set(self.open_order_ids)) != len(self.open_order_ids):
            raise ValueError("open_order_ids must be unique")


@dataclass(frozen=True, slots=True)
class ShadowReconciliation:
    authoritative_state: SimulatedExchangeState
    position_mismatches: tuple[str, ...]
    orphan_order_ids: tuple[str, ...]
    missing_exchange_order_ids: tuple[str, ...]

    @property
    def clean(self) -> bool:
        return not (
            self.position_mismatches
            or self.orphan_order_ids
            or self.missing_exchange_order_ids
        )


class ShadowExecutionGateway:
    """Stores would-be quotes; deliberately exposes no order-send operation."""

    def __init__(self) -> None:
        self._records: list[ShadowQuoteRecord] = []

    @property
    def records(self) -> tuple[ShadowQuoteRecord, ...]:
        return tuple(self._records)

    def stage_approved(
        self,
        approvals: tuple[ApprovedIntent, ...],
        *,
        timestamp_ms: int,
    ) -> tuple[ShadowQuoteRecord, ...]:
        if timestamp_ms < 0:
            raise ValueError("timestamp_ms must be non-negative")
        staged = tuple(
            ShadowQuoteRecord(
                decision_id=approval.decision_id,
                intent_id=approval.intent.intent_id,
                market=approval.intent.market,
                side=approval.intent.side,
                price=approval.intent.price,
                size=approval.approved_size,
                staged_at_ms=timestamp_ms,
            )
            for approval in approvals
        )
        self._records.extend(staged)
        return staged

    def reconcile(
        self,
        *,
        local_state: SimulatedExchangeState,
        exchange_state: SimulatedExchangeState,
    ) -> ShadowReconciliation:
        local_positions = dict(local_state.positions)
        exchange_positions = dict(exchange_state.positions)
        markets = sorted(set(local_positions) | set(exchange_positions))
        mismatches = tuple(
            market
            for market in markets
            if local_positions.get(market, Decimal(0))
            != exchange_positions.get(market, Decimal(0))
        )
        local_orders = set(local_state.open_order_ids)
        exchange_orders = set(exchange_state.open_order_ids)
        return ShadowReconciliation(
            authoritative_state=exchange_state,
            position_mismatches=mismatches,
            orphan_order_ids=tuple(sorted(exchange_orders - local_orders)),
            missing_exchange_order_ids=tuple(sorted(local_orders - exchange_orders)),
        )
