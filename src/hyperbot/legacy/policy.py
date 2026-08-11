"""Fail-closed evidence policy for replays that contain legacy data."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from hyperbot.models import DatasetTier


class ReplayUse(StrEnum):
    FAIR_VALUE = "fair_value"
    SPREAD_ANALYSIS = "spread_analysis"
    AGGREGATED_DEPTH = "aggregated_depth"
    MARKOUT_ANALYSIS = "markout_analysis"
    OUTCOME_PARITY = "outcome_parity"
    MARKET_SELECTION = "market_selection"
    STALE_DETECTION = "stale_detection"
    LEGACY_BOT_REPRODUCTION = "legacy_bot_reproduction"
    OPTIMISTIC_TOUCH = "optimistic_touch"
    EXACT_QUEUE_POSITION = "exact_queue_position"
    PARTIAL_MAKER_FILLS = "partial_maker_fills"
    CENTRAL_FILL_MODEL = "central_fill_model"
    PESSIMISTIC_FILL_MODEL = "pessimistic_fill_model"
    LIVE_PROFITABILITY_CLAIM = "live_profitability_claim"
    CANARY_PROMOTION = "canary_promotion"


_RESEARCH_ONLY_USES = {
    ReplayUse.FAIR_VALUE,
    ReplayUse.SPREAD_ANALYSIS,
    ReplayUse.AGGREGATED_DEPTH,
    ReplayUse.MARKOUT_ANALYSIS,
    ReplayUse.OUTCOME_PARITY,
    ReplayUse.MARKET_SELECTION,
    ReplayUse.STALE_DETECTION,
    ReplayUse.LEGACY_BOT_REPRODUCTION,
    ReplayUse.OPTIMISTIC_TOUCH,
}


@dataclass(frozen=True, slots=True)
class ReplayAuthorization:
    replay_use: ReplayUse
    dataset_tiers: tuple[DatasetTier, ...]
    allowed: bool
    required_label: str | None
    reason: str


class LegacyEvidenceError(RuntimeError):
    """Raised when B/C data is used beyond its evidence level."""


def authorize_replay_use(
    replay_use: ReplayUse, dataset_tiers: tuple[DatasetTier, ...]
) -> ReplayAuthorization:
    """Evaluate one use; any B/C input keeps the result research-only."""

    if not dataset_tiers:
        return ReplayAuthorization(
            replay_use=replay_use,
            dataset_tiers=(),
            allowed=False,
            required_label=None,
            reason="aucun niveau de données n'a été fourni",
        )
    tiers = tuple(sorted(set(dataset_tiers), key=lambda tier: tier.value))
    if replay_use in {ReplayUse.LIVE_PROFITABILITY_CLAIM, ReplayUse.CANARY_PROMOTION}:
        return ReplayAuthorization(
            replay_use=replay_use,
            dataset_tiers=tiers,
            allowed=False,
            required_label=None,
            reason=(
                "le niveau de données ne suffit pas : les gates OOS, risque et "
                "autorisation séparée restent obligatoires"
            ),
        )
    contains_legacy = DatasetTier.B in tiers or DatasetTier.C in tiers
    if not contains_legacy:
        return ReplayAuthorization(
            replay_use=replay_use,
            dataset_tiers=tiers,
            allowed=True,
            required_label=None,
            reason="la requête contient uniquement des données de niveau A",
        )
    if replay_use in _RESEARCH_ONLY_USES:
        label = (
            "legacy_research_only_optimistic_touch"
            if replay_use is ReplayUse.OPTIMISTIC_TOUCH
            else "legacy_research_only"
        )
        return ReplayAuthorization(
            replay_use=replay_use,
            dataset_tiers=tiers,
            allowed=True,
            required_label=label,
            reason=(
                "les données B/C sont admises uniquement pour la recherche ou "
                "comme borne optimiste"
            ),
        )
    return ReplayAuthorization(
        replay_use=replay_use,
        dataset_tiers=tiers,
        allowed=False,
        required_label=None,
        reason=(
            "les données B/C ne prouvent ni file exacte, ni fills centraux/"
            "pessimistes, ni rentabilité live, ni promotion"
        ),
    )


def require_replay_use(
    replay_use: ReplayUse, dataset_tiers: tuple[DatasetTier, ...]
) -> ReplayAuthorization:
    """Return the authorization or fail closed with a stable error."""

    decision = authorize_replay_use(replay_use, dataset_tiers)
    if not decision.allowed:
        raise LegacyEvidenceError(f"{replay_use.value}: {decision.reason}")
    return decision


def legacy_policy_matrix() -> tuple[ReplayAuthorization, ...]:
    """Return every policy decision for a representative B/C input."""

    tiers = (DatasetTier.B, DatasetTier.C)
    return tuple(authorize_replay_use(replay_use, tiers) for replay_use in ReplayUse)
