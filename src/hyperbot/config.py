"""Typed, fail-closed HyperBot configuration loading."""

from __future__ import annotations

import hashlib
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, cast


class ConfigurationError(ValueError):
    """Raised when a configuration is invalid or unsafe."""


class UnsafeConfigurationError(ConfigurationError):
    """Raised when configuration attempts to enable an unsafe mode."""


def _positive(value: Decimal, name: str) -> None:
    if not value.is_finite() or value <= 0:
        raise ConfigurationError(f"{name} must be finite and positive")


@dataclass(frozen=True, slots=True)
class ModeConfig:
    live_enabled: bool
    shadow_only: bool

    def __post_init__(self) -> None:
        if self.live_enabled:
            raise UnsafeConfigurationError(
                "live trading is not implemented or authorized in Phase 0"
            )
        if not self.shadow_only:
            raise UnsafeConfigurationError("Phase 0 requires shadow_only = true")


@dataclass(frozen=True, slots=True)
class PortfolioConfig:
    reference_equity_usd: Decimal
    daily_loss_stop_pct: Decimal
    soft_drawdown_pct: Decimal
    hard_drawdown_pct: Decimal
    unknown_loss_stop_usd: Decimal

    def __post_init__(self) -> None:
        _positive(self.reference_equity_usd, "reference_equity_usd")
        _positive(self.daily_loss_stop_pct, "daily_loss_stop_pct")
        _positive(self.soft_drawdown_pct, "soft_drawdown_pct")
        _positive(self.hard_drawdown_pct, "hard_drawdown_pct")
        _positive(self.unknown_loss_stop_usd, "unknown_loss_stop_usd")
        if not (
            self.daily_loss_stop_pct
            < self.soft_drawdown_pct
            < self.hard_drawdown_pct
            < Decimal("100")
        ):
            raise ConfigurationError(
                "drawdown limits must satisfy daily < soft < hard < 100"
            )


@dataclass(frozen=True, slots=True)
class OutcomeMakerConfig:
    enabled: bool
    research_allocation_usd: Decimal
    order_usd: Decimal
    max_inventory_per_market_usd: Decimal
    max_settlement_loss_per_market_usd: Decimal
    max_correlated_settlement_loss_usd: Decimal
    max_total_settlement_loss_usd: Decimal
    max_markets: int
    stale_book_ms: int
    entry_order_type: str

    def __post_init__(self) -> None:
        for name, value in (
            ("research_allocation_usd", self.research_allocation_usd),
            ("order_usd", self.order_usd),
            ("max_inventory_per_market_usd", self.max_inventory_per_market_usd),
            (
                "max_settlement_loss_per_market_usd",
                self.max_settlement_loss_per_market_usd,
            ),
            (
                "max_correlated_settlement_loss_usd",
                self.max_correlated_settlement_loss_usd,
            ),
            ("max_total_settlement_loss_usd", self.max_total_settlement_loss_usd),
        ):
            _positive(value, name)
        if self.max_markets <= 0 or self.stale_book_ms <= 0:
            raise ConfigurationError("max_markets and stale_book_ms must be positive")
        if self.entry_order_type != "ALO":
            raise UnsafeConfigurationError("outcome entry_order_type must be ALO")
        if (
            self.max_settlement_loss_per_market_usd
            > self.max_correlated_settlement_loss_usd
            or self.max_correlated_settlement_loss_usd
            > self.max_total_settlement_loss_usd
        ):
            raise ConfigurationError(
                "outcome loss caps must satisfy per-market <= correlated <= total"
            )


@dataclass(frozen=True, slots=True)
class GrowthMakerConfig:
    enabled: bool
    research_allocation_usd: Decimal
    order_usd: Decimal
    max_inventory_per_symbol_usd: Decimal
    max_gross_inventory_usd: Decimal
    max_net_delta_usd: Decimal
    min_median_spread_bps: Decimal
    min_daily_volume_usd: Decimal
    require_growth_mode: bool
    entry_order_type: str

    def __post_init__(self) -> None:
        for name, value in (
            ("research_allocation_usd", self.research_allocation_usd),
            ("order_usd", self.order_usd),
            ("max_inventory_per_symbol_usd", self.max_inventory_per_symbol_usd),
            ("max_gross_inventory_usd", self.max_gross_inventory_usd),
            ("max_net_delta_usd", self.max_net_delta_usd),
            ("min_median_spread_bps", self.min_median_spread_bps),
            ("min_daily_volume_usd", self.min_daily_volume_usd),
        ):
            _positive(value, name)
        if not self.require_growth_mode:
            raise UnsafeConfigurationError("growth mode must be required in Phase 0")
        if self.entry_order_type != "ALO":
            raise UnsafeConfigurationError("growth entry_order_type must be ALO")
        if self.max_net_delta_usd > self.max_gross_inventory_usd:
            raise ConfigurationError("max_net_delta_usd cannot exceed gross inventory")


@dataclass(frozen=True, slots=True)
class StorageConfig:
    raw_dir: Path
    fsync: bool

    def __post_init__(self) -> None:
        if not str(self.raw_dir):
            raise ConfigurationError("raw_dir must not be empty")


@dataclass(frozen=True, slots=True)
class HyperBotConfig:
    mode: ModeConfig
    portfolio: PortfolioConfig
    outcome_maker: OutcomeMakerConfig
    growth_maker: GrowthMakerConfig
    storage: StorageConfig

    def __post_init__(self) -> None:
        allocated = (
            self.outcome_maker.research_allocation_usd
            + self.growth_maker.research_allocation_usd
        )
        if allocated > self.portfolio.reference_equity_usd:
            raise ConfigurationError("research allocations exceed reference equity")

    @property
    def reserve_usd(self) -> Decimal:
        return self.portfolio.reference_equity_usd - (
            self.outcome_maker.research_allocation_usd
            + self.growth_maker.research_allocation_usd
        )


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    config: HyperBotConfig
    source_path: Path
    sha256: str


def _section(document: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = document.get(name)
    if not isinstance(value, dict):
        raise ConfigurationError(f"missing or invalid [{name}] section")
    return cast(Mapping[str, Any], value)


def _exact_keys(section: Mapping[str, Any], name: str, expected: set[str]) -> None:
    actual = set(section)
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        raise ConfigurationError(
            f"[{name}] keys mismatch; missing={sorted(missing)}, extra={sorted(extra)}"
        )


def _bool(section: Mapping[str, Any], key: str) -> bool:
    value = section[key]
    if not isinstance(value, bool):
        raise ConfigurationError(f"{key} must be a boolean")
    return value


def _decimal(section: Mapping[str, Any], key: str) -> Decimal:
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        raise ConfigurationError(f"{key} must be numeric")
    try:
        return Decimal(str(value))
    except Exception as exc:
        raise ConfigurationError(f"{key} must be numeric") from exc


def _int(section: Mapping[str, Any], key: str) -> int:
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{key} must be an integer")
    return value


def _str(section: Mapping[str, Any], key: str) -> str:
    value = section[key]
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{key} must be a non-empty string")
    return value


def load_config(path: str | Path) -> LoadedConfig:
    """Load a strict TOML configuration and return its content hash."""

    source_path = Path(path)
    raw = source_path.read_bytes()
    try:
        document = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"cannot parse {source_path}") from exc

    expected_sections = {
        "mode",
        "portfolio",
        "outcome_maker",
        "growth_maker",
        "storage",
    }
    _exact_keys(document, "root", expected_sections)

    mode = _section(document, "mode")
    _exact_keys(mode, "mode", {"live_enabled", "shadow_only"})

    portfolio = _section(document, "portfolio")
    _exact_keys(
        portfolio,
        "portfolio",
        {
            "reference_equity_usd",
            "daily_loss_stop_pct",
            "soft_drawdown_pct",
            "hard_drawdown_pct",
            "unknown_loss_stop_usd",
        },
    )

    outcome = _section(document, "outcome_maker")
    _exact_keys(
        outcome,
        "outcome_maker",
        {
            "enabled",
            "research_allocation_usd",
            "order_usd",
            "max_inventory_per_market_usd",
            "max_settlement_loss_per_market_usd",
            "max_correlated_settlement_loss_usd",
            "max_total_settlement_loss_usd",
            "max_markets",
            "stale_book_ms",
            "entry_order_type",
        },
    )

    growth = _section(document, "growth_maker")
    _exact_keys(
        growth,
        "growth_maker",
        {
            "enabled",
            "research_allocation_usd",
            "order_usd",
            "max_inventory_per_symbol_usd",
            "max_gross_inventory_usd",
            "max_net_delta_usd",
            "min_median_spread_bps",
            "min_daily_volume_usd",
            "require_growth_mode",
            "entry_order_type",
        },
    )

    storage = _section(document, "storage")
    _exact_keys(storage, "storage", {"raw_dir", "fsync"})

    config = HyperBotConfig(
        mode=ModeConfig(
            live_enabled=_bool(mode, "live_enabled"),
            shadow_only=_bool(mode, "shadow_only"),
        ),
        portfolio=PortfolioConfig(
            reference_equity_usd=_decimal(portfolio, "reference_equity_usd"),
            daily_loss_stop_pct=_decimal(portfolio, "daily_loss_stop_pct"),
            soft_drawdown_pct=_decimal(portfolio, "soft_drawdown_pct"),
            hard_drawdown_pct=_decimal(portfolio, "hard_drawdown_pct"),
            unknown_loss_stop_usd=_decimal(portfolio, "unknown_loss_stop_usd"),
        ),
        outcome_maker=OutcomeMakerConfig(
            enabled=_bool(outcome, "enabled"),
            research_allocation_usd=_decimal(outcome, "research_allocation_usd"),
            order_usd=_decimal(outcome, "order_usd"),
            max_inventory_per_market_usd=_decimal(
                outcome, "max_inventory_per_market_usd"
            ),
            max_settlement_loss_per_market_usd=_decimal(
                outcome, "max_settlement_loss_per_market_usd"
            ),
            max_correlated_settlement_loss_usd=_decimal(
                outcome, "max_correlated_settlement_loss_usd"
            ),
            max_total_settlement_loss_usd=_decimal(
                outcome, "max_total_settlement_loss_usd"
            ),
            max_markets=_int(outcome, "max_markets"),
            stale_book_ms=_int(outcome, "stale_book_ms"),
            entry_order_type=_str(outcome, "entry_order_type"),
        ),
        growth_maker=GrowthMakerConfig(
            enabled=_bool(growth, "enabled"),
            research_allocation_usd=_decimal(growth, "research_allocation_usd"),
            order_usd=_decimal(growth, "order_usd"),
            max_inventory_per_symbol_usd=_decimal(
                growth, "max_inventory_per_symbol_usd"
            ),
            max_gross_inventory_usd=_decimal(
                growth, "max_gross_inventory_usd"
            ),
            max_net_delta_usd=_decimal(growth, "max_net_delta_usd"),
            min_median_spread_bps=_decimal(growth, "min_median_spread_bps"),
            min_daily_volume_usd=_decimal(growth, "min_daily_volume_usd"),
            require_growth_mode=_bool(growth, "require_growth_mode"),
            entry_order_type=_str(growth, "entry_order_type"),
        ),
        storage=StorageConfig(
            raw_dir=Path(_str(storage, "raw_dir")),
            fsync=_bool(storage, "fsync"),
        ),
    )
    return LoadedConfig(
        config=config,
        source_path=source_path.resolve(),
        sha256=hashlib.sha256(raw).hexdigest(),
    )
