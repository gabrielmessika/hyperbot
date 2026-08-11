import pytest

from hyperbot.legacy.policy import (
    LegacyEvidenceError,
    ReplayUse,
    authorize_replay_use,
    require_replay_use,
)
from hyperbot.models import DatasetTier


def test_legacy_markouts_are_allowed_but_research_only() -> None:
    decision = authorize_replay_use(
        ReplayUse.MARKOUT_ANALYSIS, (DatasetTier.B, DatasetTier.C)
    )

    assert decision.allowed is True
    assert decision.required_label == "legacy_research_only"


def test_touch_fill_is_explicitly_optimistic() -> None:
    decision = require_replay_use(ReplayUse.OPTIMISTIC_TOUCH, (DatasetTier.B,))

    assert decision.required_label == "legacy_research_only_optimistic_touch"


@pytest.mark.parametrize(
    "replay_use",
    [
        ReplayUse.EXACT_QUEUE_POSITION,
        ReplayUse.PARTIAL_MAKER_FILLS,
        ReplayUse.CENTRAL_FILL_MODEL,
        ReplayUse.PESSIMISTIC_FILL_MODEL,
        ReplayUse.LIVE_PROFITABILITY_CLAIM,
        ReplayUse.CANARY_PROMOTION,
    ],
)
def test_legacy_evidence_uses_fail_closed(replay_use: ReplayUse) -> None:
    with pytest.raises(LegacyEvidenceError):
        require_replay_use(replay_use, (DatasetTier.C,))


def test_level_a_only_is_not_restricted_by_legacy_policy() -> None:
    decision = authorize_replay_use(
        ReplayUse.CENTRAL_FILL_MODEL, (DatasetTier.A,)
    )

    assert decision.allowed is True
    assert decision.required_label is None


def test_level_a_alone_never_authorizes_canary() -> None:
    decision = authorize_replay_use(
        ReplayUse.CANARY_PROMOTION, (DatasetTier.A,)
    )

    assert decision.allowed is False
    assert "autorisation séparée" in decision.reason
