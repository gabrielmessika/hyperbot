from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path

from hyperbot.market_catalog import MarketCatalogClient
from hyperbot.models import EventContext, JsonValue, MarketKind, TimeSource

FIXTURES = Path(__file__).parent / "fixtures" / "m2"


class FixtureTransport:
    def __init__(self) -> None:
        self.core = _load("core_meta_contexts.json")
        self.responses: dict[str, object] = {
            "perpDexs": _load("perp_dexs.json"),
            "xyz": _load("xyz_meta_contexts.json"),
            "spotMetaAndAssetCtxs": _load("spot_meta_contexts.json"),
            "outcomeMeta": _load("outcome_meta.json"),
        }
        self.requests: list[dict[str, JsonValue]] = []

    def post(self, payload: Mapping[str, JsonValue]) -> object:
        self.requests.append(dict(payload))
        request_type = payload["type"]
        if request_type == "metaAndAssetCtxs" and "dex" not in payload:
            return copy.deepcopy(self.core)
        if request_type == "metaAndAssetCtxs":
            return copy.deepcopy(self.responses[str(payload["dex"])])
        return copy.deepcopy(self.responses[str(request_type)])


def _load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _context() -> EventContext:
    return EventContext("catalog-test", "test", "a" * 64, TimeSource.EXCHANGE)


def test_catalog_normalizes_core_hip3_and_outcomes_without_credentials() -> None:
    transport = FixtureTransport()
    snapshot = MarketCatalogClient(transport).snapshot(
        _context(), observed_at_ms=1_700_000_000_000
    )

    assert len(snapshot.definitions) == 4
    assert snapshot.change_events == snapshot.definitions
    by_id = {definition.market_id: definition for definition in snapshot.definitions}

    btc = by_id["core:BTC"]
    assert btc.market_kind is MarketKind.CORE_PERP
    assert btc.asset_id == 0
    assert str(btc.size_increment) == "0.00001"
    assert str(btc.tick_size_at_reference) == "1"
    assert str(btc.maker_fee_bps) == "1.5"
    assert str(btc.taker_fee_bps) == "4.5"

    hip3 = by_id["xyz:xyz:XYZ100"]
    assert hip3.market_kind is MarketKind.HIP3_PERP
    assert hip3.asset_id == 100_000
    assert str(hip3.tick_size_at_reference) == "0.01"
    assert hip3.maker_fee_bps == Decimal("0.3")
    assert hip3.taker_fee_bps == Decimal("0.9")

    yes = by_id["outcome:42:0"]
    assert yes.coin == "#420"
    assert yes.asset_id == 100_000_420
    assert yes.sz_decimals is None
    assert yes.tick_size_at_reference is None
    assert "outcome_tick_and_lot_unpublished" in yes.quality_flags

    assert any("lacks name/tick data" in issue.detail for issue in snapshot.issues)
    assert all(set(request) <= {"type", "dex"} for request in transport.requests)


def test_catalog_emits_a_new_revision_when_tick_rules_change() -> None:
    transport = FixtureTransport()
    client = MarketCatalogClient(transport)
    first = client.snapshot(_context(), observed_at_ms=100)
    first_btc = next(item for item in first.definitions if item.market_id == "core:BTC")

    core = transport.core
    assert isinstance(core, list)
    assert isinstance(core[0], dict)
    universe = core[0]["universe"]
    assert isinstance(universe, list)
    assert isinstance(universe[0], dict)
    universe[0]["szDecimals"] = 4
    second = client.snapshot(_context(), observed_at_ms=200)
    changes = {item.market_id: item for item in second.change_events}

    assert set(changes) == {"core:BTC"}
    assert changes["core:BTC"].definition_version == 2
    assert changes["core:BTC"].previous_definition_sha256 == (
        first_btc.definition_sha256
    )


def test_catalog_tolerates_unavailable_outcome_metadata() -> None:
    transport = FixtureTransport()
    transport.responses["outcomeMeta"] = {"unexpected": "shape"}

    snapshot = MarketCatalogClient(transport).snapshot(_context(), observed_at_ms=100)

    assert all(
        item.market_kind is not MarketKind.OUTCOME
        for item in snapshot.definitions
    )
    assert any(issue.request_type == "outcomeMeta" for issue in snapshot.issues)
