from decimal import Decimal
from pathlib import Path

import pytest

from hyperbot.config import UnsafeConfigurationError, load_config

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "hyperbot_research.toml"


def test_default_research_config_is_safe() -> None:
    loaded = load_config(DEFAULT_CONFIG)

    assert loaded.config.mode.live_enabled is False
    assert loaded.config.mode.shadow_only is True
    assert loaded.config.outcome_maker.enabled is False
    assert loaded.config.growth_maker.enabled is False
    assert loaded.config.reserve_usd == Decimal("50.0")
    assert len(loaded.sha256) == 64


def test_live_mode_is_rejected_even_if_present_in_toml(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe.toml"
    text = DEFAULT_CONFIG.read_text(encoding="utf-8")
    unsafe.write_text(
        text.replace("live_enabled = false", "live_enabled = true"),
        encoding="utf-8",
    )

    with pytest.raises(UnsafeConfigurationError, match="not implemented"):
        load_config(unsafe)


def test_unknown_key_is_rejected(tmp_path: Path) -> None:
    invalid = tmp_path / "invalid.toml"
    invalid.write_text(
        DEFAULT_CONFIG.read_text(encoding="utf-8") + "\nunknown = true\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="keys mismatch"):
        load_config(invalid)
