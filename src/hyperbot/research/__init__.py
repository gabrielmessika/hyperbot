"""Falsifiable outcome and HIP-3 research components."""

from hyperbot.research.gates import ResearchGateDecision, evaluate_research_gate
from hyperbot.research.growth import (
    GrowthMarketObservation,
    GrowthMarketScanner,
    GrowthScanDecision,
    GrowthScannerConfig,
)
from hyperbot.research.outcomes import (
    DigitalOutcomeBenchmark,
    FoldWindow,
    IsotonicCalibrator,
    OutcomeResearchEvaluator,
    OutcomeResearchResult,
    OutcomeSample,
    cluster_bootstrap_pnl,
    empirical_volatility,
)

__all__ = [
    "DigitalOutcomeBenchmark",
    "FoldWindow",
    "GrowthMarketObservation",
    "GrowthMarketScanner",
    "GrowthScanDecision",
    "GrowthScannerConfig",
    "IsotonicCalibrator",
    "OutcomeResearchEvaluator",
    "OutcomeResearchResult",
    "OutcomeSample",
    "ResearchGateDecision",
    "cluster_bootstrap_pnl",
    "empirical_volatility",
    "evaluate_research_gate",
]
