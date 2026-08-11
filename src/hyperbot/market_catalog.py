"""Credential-free Hyperliquid market catalog and revision detection."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from typing import Protocol, cast

from hyperbot.models import (
    EventContext,
    JsonValue,
    MarketDefinition,
    MarketKind,
    MarketStatus,
)

CATALOG_VERSION = 1
INFO_URL = "https://api.hyperliquid.xyz/info"
MINIMUM_ORDER_NOTIONAL_USD = Decimal("10")
MAX_SIGNIFICANT_FIGURES = 5


class CatalogError(RuntimeError):
    """Raised when the public catalog cannot be fetched safely."""


class PublicInfoTransport(Protocol):
    def post(self, payload: Mapping[str, JsonValue]) -> object:
        """POST one public info request and return its decoded JSON body."""


class UrllibPublicInfoTransport:
    """Minimal public-info transport with no signing or exchange endpoint."""

    def __init__(self, *, url: str = INFO_URL, timeout_seconds: float = 15.0) -> None:
        if not url.startswith("https://"):
            raise ValueError("public info URL must use HTTPS")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.url = url
        self.timeout_seconds = timeout_seconds

    def post(self, payload: Mapping[str, JsonValue]) -> object:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - fixed HTTPS URL required
            self.url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(  # noqa: S310 - URL validated above
            request,
            timeout=self.timeout_seconds,
        ) as response:
            return json.load(response)


@dataclass(frozen=True, slots=True)
class CatalogIssue:
    request_type: str
    detail: str


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    observed_at_ms: int
    definitions: tuple[MarketDefinition, ...]
    change_events: tuple[MarketDefinition, ...]
    issues: tuple[CatalogIssue, ...]


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _definition_hash(fields: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json(fields)).hexdigest()


def _mapping(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return cast(dict[str, object], value)


def _list(value: object) -> list[object] | None:
    if not isinstance(value, list):
        return None
    return cast(list[object], value)


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _size_increment(sz_decimals: int | None) -> Decimal | None:
    if sz_decimals is None or sz_decimals < 0:
        return None
    return Decimal(1).scaleb(-sz_decimals)


def _tick_at_reference(
    reference_price: Decimal | None,
    max_price_decimals: int | None,
) -> Decimal | None:
    if reference_price is None or reference_price <= 0 or max_price_decimals is None:
        return None
    significant_step = Decimal(1).scaleb(reference_price.adjusted() - 4)
    decimal_step = Decimal(1).scaleb(-max_price_decimals)
    return max(significant_step, decimal_step)


def _status(
    universe: Mapping[str, object],
    context: Mapping[str, object],
) -> MarketStatus:
    if universe.get("isDelisted") is True or context.get("isDelisted") is True:
        return MarketStatus.INACTIVE
    if context:
        return MarketStatus.ACTIVE
    return MarketStatus.UNKNOWN


def _fee_reference(
    kind: MarketKind,
    growth_mode: str | None,
    deployer_fee_scale: Decimal | None,
) -> tuple[Decimal | None, Decimal | None, str]:
    if kind is MarketKind.OUTCOME:
        return Decimal("4"), Decimal("7"), "tier0_spot_before_account_discounts"
    if kind is MarketKind.CORE_PERP:
        return Decimal("1.5"), Decimal("4.5"), "tier0_perp_before_account_discounts"
    if deployer_fee_scale is None:
        return None, None, "unknown_hip3_fee_scale"
    multiplier = (
        deployer_fee_scale + Decimal(1)
        if deployer_fee_scale < 1
        else deployer_fee_scale * 2
    )
    if growth_mode == "enabled":
        multiplier *= Decimal("0.1")
    return (
        Decimal("1.5") * multiplier,
        Decimal("4.5") * multiplier,
        "tier0_hip3_before_account_discounts",
    )


def _market_definition(
    *,
    context: EventContext,
    observed_at_ms: int,
    market_id: str,
    kind: MarketKind,
    dex: str,
    coin: str,
    display_name: str,
    asset_id: int,
    sz_decimals: int | None,
    growth_mode: str | None,
    deployer_fee_scale: Decimal | None,
    oracle_px: Decimal | None,
    mark_px: Decimal | None,
    status: MarketStatus,
    quality_flags: tuple[str, ...] = (),
) -> MarketDefinition:
    max_price_decimals = (
        (8 if kind is MarketKind.OUTCOME else 6) - sz_decimals
        if sz_decimals is not None
        else None
    )
    maker_fee_bps, taker_fee_bps, fee_basis = _fee_reference(
        kind,
        growth_mode,
        deployer_fee_scale,
    )
    reference_price = mark_px or oracle_px
    specification = {
        "catalog_version": CATALOG_VERSION,
        "market_id": market_id,
        "market_kind": kind.value,
        "dex": dex,
        "coin": coin,
        "display_name": display_name,
        "asset_id": asset_id,
        "sz_decimals": sz_decimals,
        "size_increment": (
            str(_size_increment(sz_decimals)) if sz_decimals is not None else None
        ),
        "max_price_decimals": max_price_decimals,
        "max_significant_figures": (
            MAX_SIGNIFICANT_FIGURES if sz_decimals is not None else None
        ),
        "minimum_order_notional_usd": str(MINIMUM_ORDER_NOTIONAL_USD),
        "growth_mode": growth_mode,
        "deployer_fee_scale": (
            str(deployer_fee_scale) if deployer_fee_scale is not None else None
        ),
        "maker_fee_bps": str(maker_fee_bps) if maker_fee_bps is not None else None,
        "taker_fee_bps": str(taker_fee_bps) if taker_fee_bps is not None else None,
        "fee_basis": fee_basis,
        "status": status.value,
        "quality_flags": list(quality_flags),
    }
    return MarketDefinition(
        context=context,
        observed_at_ms=observed_at_ms,
        catalog_version=CATALOG_VERSION,
        definition_version=1,
        market_id=market_id,
        market_kind=kind,
        dex=dex,
        coin=coin,
        display_name=display_name,
        asset_id=asset_id,
        sz_decimals=sz_decimals,
        size_increment=_size_increment(sz_decimals),
        max_price_decimals=max_price_decimals,
        max_significant_figures=(
            MAX_SIGNIFICANT_FIGURES if sz_decimals is not None else None
        ),
        tick_size_at_reference=_tick_at_reference(
            reference_price,
            max_price_decimals,
        ),
        minimum_order_notional_usd=MINIMUM_ORDER_NOTIONAL_USD,
        growth_mode=growth_mode,
        deployer_fee_scale=deployer_fee_scale,
        maker_fee_bps=maker_fee_bps,
        taker_fee_bps=taker_fee_bps,
        fee_basis=fee_basis,
        oracle_px=oracle_px,
        mark_px=mark_px,
        status=status,
        definition_sha256=_definition_hash(specification),
        previous_definition_sha256=None,
        quality_flags=quality_flags,
    )


class MarketCatalogClient:
    """Build catalog snapshots from public info responses only."""

    def __init__(self, transport: PublicInfoTransport) -> None:
        self.transport = transport
        self._previous: dict[str, MarketDefinition] = {}

    def snapshot(
        self,
        context: EventContext,
        *,
        observed_at_ms: int | None = None,
    ) -> CatalogSnapshot:
        observed = int(time.time() * 1000) if observed_at_ms is None else observed_at_ms
        if observed < 0:
            raise ValueError("observed_at_ms must be non-negative")
        issues: list[CatalogIssue] = []
        definitions: list[MarketDefinition] = []

        core = self._request({"type": "metaAndAssetCtxs"}, "metaAndAssetCtxs")
        definitions.extend(
            self._parse_perps(
                core,
                context=context,
                observed_at_ms=observed,
                kind=MarketKind.CORE_PERP,
                dex="core",
                dex_asset_base=0,
                issues=issues,
            )
        )

        dex_response = self._request({"type": "perpDexs"}, "perpDexs")
        dex_items = _list(dex_response)
        if dex_items is None:
            issues.append(CatalogIssue("perpDexs", "response is not a list"))
        else:
            deployed_index = 0
            for raw_dex in dex_items:
                dex = _mapping(raw_dex)
                if dex is None:
                    continue
                dex_name = _string(dex.get("name"))
                if dex_name is None:
                    issues.append(CatalogIssue("perpDexs", "DEX without a name"))
                    continue
                dex_meta = self._request(
                    {"type": "metaAndAssetCtxs", "dex": dex_name},
                    f"metaAndAssetCtxs:{dex_name}",
                )
                definitions.extend(
                    self._parse_perps(
                        dex_meta,
                        context=context,
                        observed_at_ms=observed,
                        kind=MarketKind.HIP3_PERP,
                        dex=dex_name,
                        dex_asset_base=100_000 + deployed_index * 10_000,
                        issues=issues,
                    )
                )
                deployed_index += 1

        spot = self._request(
            {"type": "spotMetaAndAssetCtxs"},
            "spotMetaAndAssetCtxs",
        )
        outcomes = self._request({"type": "outcomeMeta"}, "outcomeMeta")
        definitions.extend(
            self._parse_outcomes(
                outcomes,
                spot,
                context=context,
                observed_at_ms=observed,
                issues=issues,
            )
        )

        current: list[MarketDefinition] = []
        changes: list[MarketDefinition] = []
        for definition in sorted(definitions, key=lambda item: item.market_id):
            previous = self._previous.get(definition.market_id)
            if previous is None:
                versioned = definition
                changes.append(versioned)
            elif previous.definition_sha256 != definition.definition_sha256:
                versioned = replace(
                    definition,
                    definition_version=previous.definition_version + 1,
                    previous_definition_sha256=previous.definition_sha256,
                )
                changes.append(versioned)
            else:
                versioned = replace(
                    definition,
                    definition_version=previous.definition_version,
                    previous_definition_sha256=previous.previous_definition_sha256,
                )
            self._previous[versioned.market_id] = versioned
            current.append(versioned)
        return CatalogSnapshot(observed, tuple(current), tuple(changes), tuple(issues))

    def _request(self, payload: Mapping[str, JsonValue], label: str) -> object:
        try:
            return self.transport.post(payload)
        except Exception as exc:
            return {"_hyperbot_request_error": f"{label}: {type(exc).__name__}: {exc}"}

    def _parse_perps(
        self,
        response: object,
        *,
        context: EventContext,
        observed_at_ms: int,
        kind: MarketKind,
        dex: str,
        dex_asset_base: int,
        issues: list[CatalogIssue],
    ) -> list[MarketDefinition]:
        pair = _list(response)
        if pair is None or len(pair) < 2:
            detail = "response is not [meta, contexts]"
            response_map = _mapping(response)
            if response_map and "_hyperbot_request_error" in response_map:
                detail = str(response_map["_hyperbot_request_error"])
            issues.append(CatalogIssue(f"perps:{dex}", detail))
            return []
        meta = _mapping(pair[0])
        contexts = _list(pair[1])
        universe = _list(meta.get("universe")) if meta else None
        if meta is None or universe is None or contexts is None:
            issues.append(CatalogIssue(f"perps:{dex}", "missing universe or contexts"))
            return []
        parsed: list[MarketDefinition] = []
        for index, raw_market in enumerate(universe):
            market = _mapping(raw_market)
            if market is None:
                issues.append(
                    CatalogIssue(f"perps:{dex}", f"market {index} is invalid")
                )
                continue
            name = _string(market.get("name"))
            sz_decimals = _integer(market.get("szDecimals"))
            if name is None or sz_decimals is None:
                issues.append(
                    CatalogIssue(f"perps:{dex}", f"market {index} lacks name/tick data")
                )
                continue
            market_context = _mapping(contexts[index]) if index < len(contexts) else {}
            if market_context is None:
                market_context = {}
            growth_mode = _string(market.get("growthMode"))
            fee_scale = _decimal(market.get("deployerFeeScale"))
            flags: list[str] = []
            if kind is MarketKind.HIP3_PERP and fee_scale is None:
                flags.append("hip3_fee_scale_unknown")
            parsed.append(
                _market_definition(
                    context=context,
                    observed_at_ms=observed_at_ms,
                    market_id=f"{dex}:{name}",
                    kind=kind,
                    dex=dex,
                    coin=name,
                    display_name=name,
                    asset_id=dex_asset_base + index,
                    sz_decimals=sz_decimals,
                    growth_mode=growth_mode,
                    deployer_fee_scale=fee_scale,
                    oracle_px=_decimal(market_context.get("oraclePx")),
                    mark_px=_decimal(market_context.get("markPx")),
                    status=_status(market, market_context),
                    quality_flags=tuple(flags),
                )
            )
        return parsed

    def _parse_outcomes(
        self,
        outcome_response: object,
        spot_response: object,
        *,
        context: EventContext,
        observed_at_ms: int,
        issues: list[CatalogIssue],
    ) -> list[MarketDefinition]:
        outcome_meta = _mapping(outcome_response)
        outcomes = _list(outcome_meta.get("outcomes")) if outcome_meta else None
        if outcomes is None:
            detail = "outcomeMeta unavailable; outcomes omitted"
            if outcome_meta and "_hyperbot_request_error" in outcome_meta:
                detail = str(outcome_meta["_hyperbot_request_error"])
            issues.append(CatalogIssue("outcomeMeta", detail))
            return []
        spot_pair = _list(spot_response)
        spot_contexts = _list(spot_pair[1]) if spot_pair and len(spot_pair) >= 2 else []
        context_by_coin: dict[str, dict[str, object]] = {}
        if spot_contexts is not None:
            for raw_context in spot_contexts:
                spot_context = _mapping(raw_context)
                coin = _string(spot_context.get("coin")) if spot_context else None
                if spot_context is not None and coin is not None:
                    context_by_coin[coin] = spot_context

        parsed: list[MarketDefinition] = []
        for raw_outcome in outcomes:
            outcome = _mapping(raw_outcome)
            outcome_id = _integer(outcome.get("outcome")) if outcome else None
            outcome_name = _string(outcome.get("name")) if outcome else None
            sides = _list(outcome.get("sideSpecs")) if outcome else None
            if outcome_id is None or outcome_name is None or sides is None:
                issues.append(CatalogIssue("outcomeMeta", "invalid outcome entry"))
                continue
            for side_index, raw_side in enumerate(sides):
                side = _mapping(raw_side)
                side_name = _string(side.get("name")) if side else None
                if side_name is None:
                    issues.append(
                        CatalogIssue(
                            "outcomeMeta",
                            f"outcome {outcome_id} has an invalid side",
                        )
                    )
                    continue
                encoding = outcome_id * 10 + side_index
                coin = f"#{encoding}"
                market_context = context_by_coin.get(coin, {})
                parsed.append(
                    _market_definition(
                        context=context,
                        observed_at_ms=observed_at_ms,
                        market_id=f"outcome:{outcome_id}:{side_index}",
                        kind=MarketKind.OUTCOME,
                        dex="outcome",
                        coin=coin,
                        display_name=f"{outcome_name}:{side_name}",
                        asset_id=100_000_000 + encoding,
                        sz_decimals=None,
                        growth_mode=None,
                        deployer_fee_scale=None,
                        oracle_px=_decimal(market_context.get("oraclePx")),
                        mark_px=_decimal(market_context.get("markPx")),
                        status=_status(outcome or {}, market_context),
                        quality_flags=("outcome_tick_and_lot_unpublished",),
                    )
                )
        return parsed
