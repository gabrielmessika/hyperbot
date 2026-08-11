"""Chronological digital-outcome benchmark and purged OOS evaluation."""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from hyperbot.legacy.policy import ReplayUse, require_replay_use
from hyperbot.models import DatasetTier


@dataclass(frozen=True, slots=True)
class OutcomeSample:
    sample_id: str
    observed_ts_ms: int
    settlement_ts_ms: int
    market: str
    underlying: str
    spot: Decimal
    strike: Decimal
    realized_volatility_annualized: Decimal
    outcome: int
    maker_pnl_usd: Decimal
    legacy_bot_pnl_usd: Decimal
    volatility_regime: str
    expiry_bucket: str

    def __post_init__(self) -> None:
        if (
            not self.sample_id.strip()
            or not self.market.strip()
            or not self.underlying.strip()
            or not self.volatility_regime.strip()
            or not self.expiry_bucket.strip()
        ):
            raise ValueError("sample identifiers must not be empty")
        if self.observed_ts_ms < 0 or self.settlement_ts_ms <= self.observed_ts_ms:
            raise ValueError("sample timestamps must be chronological")
        for name, value in (
            ("spot", self.spot),
            ("strike", self.strike),
            ("realized_volatility_annualized", self.realized_volatility_annualized),
        ):
            if not value.is_finite() or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.outcome not in {0, 1}:
            raise ValueError("outcome must be zero or one")
        if (
            not self.maker_pnl_usd.is_finite()
            or not self.legacy_bot_pnl_usd.is_finite()
        ):
            raise ValueError("PnL values must be finite")


@dataclass(frozen=True, slots=True)
class FoldWindow:
    fold_id: str
    train_begin_ms: int
    train_end_ms: int
    calibration_begin_ms: int
    calibration_end_ms: int
    test_begin_ms: int
    test_end_ms: int
    purge_ms: int

    def __post_init__(self) -> None:
        if not self.fold_id.strip():
            raise ValueError("fold_id must not be empty")
        if not (
            0 <= self.train_begin_ms < self.train_end_ms
            <= self.calibration_begin_ms < self.calibration_end_ms
            <= self.test_begin_ms < self.test_end_ms
        ):
            raise ValueError("fold windows must be strictly chronological")
        if self.purge_ms < 0:
            raise ValueError("purge_ms must be non-negative")


@dataclass(frozen=True, slots=True)
class IsotonicCalibrator:
    upper_probability_bounds: tuple[Decimal, ...]
    calibrated_probabilities: tuple[Decimal, ...]

    @classmethod
    def fit(cls, observations: tuple[tuple[Decimal, int], ...]) -> IsotonicCalibrator:
        if not observations:
            raise ValueError("isotonic calibration requires observations")
        ordered = sorted(observations, key=lambda item: item[0])
        blocks: list[list[Decimal | int]] = []
        for probability, outcome in ordered:
            if not Decimal(0) <= probability <= Decimal(1) or outcome not in {0, 1}:
                raise ValueError("invalid calibration observation")
            blocks.append([probability, Decimal(outcome), 1])
            while len(blocks) >= 2:
                previous_mean = cast_decimal(blocks[-2][1]) / int(blocks[-2][2])
                current_mean = cast_decimal(blocks[-1][1]) / int(blocks[-1][2])
                if previous_mean <= current_mean:
                    break
                right = blocks.pop()
                left = blocks.pop()
                blocks.append(
                    [
                        cast_decimal(right[0]),
                        cast_decimal(left[1]) + cast_decimal(right[1]),
                        int(left[2]) + int(right[2]),
                    ]
                )
        return cls(
            upper_probability_bounds=tuple(cast_decimal(block[0]) for block in blocks),
            calibrated_probabilities=tuple(
                cast_decimal(block[1]) / int(block[2]) for block in blocks
            ),
        )

    def predict(self, probability: Decimal) -> Decimal:
        if not Decimal(0) <= probability <= Decimal(1):
            raise ValueError("probability must be between zero and one")
        for bound, calibrated in zip(
            self.upper_probability_bounds,
            self.calibrated_probabilities,
            strict=True,
        ):
            if probability <= bound:
                return calibrated
        return self.calibrated_probabilities[-1]


def cast_decimal(value: Decimal | int) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(value)


class DigitalOutcomeBenchmark:
    """Zero-drift digital benchmark under empirical annualized volatility."""

    def probability(self, sample: OutcomeSample) -> Decimal:
        seconds_left = (sample.settlement_ts_ms - sample.observed_ts_ms) / 1000
        years_left = seconds_left / (365.25 * 24 * 60 * 60)
        denominator = float(sample.realized_volatility_annualized) * math.sqrt(
            years_left
        )
        if denominator <= 0:
            return Decimal(1 if sample.spot > sample.strike else 0)
        z_score = math.log(float(sample.spot / sample.strike)) / denominator
        probability = 0.5 * (1 + math.erf(z_score / math.sqrt(2)))
        bounded = min(max(probability, 1e-12), 1 - 1e-12)
        return Decimal(str(bounded))


def empirical_volatility(
    prices: tuple[Decimal, ...],
    *,
    periods_per_year: int,
) -> Decimal:
    if len(prices) < 3 or periods_per_year <= 0:
        raise ValueError("volatility requires three prices and a positive frequency")
    if any(not price.is_finite() or price <= 0 for price in prices):
        raise ValueError("prices must be finite and positive")
    returns = [
        math.log(float(current / previous))
        for previous, current in zip(prices, prices[1:], strict=False)
    ]
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return Decimal(str(math.sqrt(variance * periods_per_year)))


@dataclass(frozen=True, slots=True)
class OutcomeFoldResult:
    fold_id: str
    train_count: int
    calibration_count: int
    purged_calibration_count: int
    test_count: int
    brier_raw: Decimal
    brier_calibrated: Decimal
    log_loss_raw: Decimal
    log_loss_calibrated: Decimal
    maker_pnl_usd: Decimal
    legacy_bot_pnl_usd: Decimal
    do_nothing_pnl_usd: Decimal
    profit_factor: Decimal | None
    max_drawdown_usd: Decimal


@dataclass(frozen=True, slots=True)
class BootstrapInterval:
    iterations: int
    seed: int
    p05_usd: Decimal
    p50_usd: Decimal
    p95_usd: Decimal


@dataclass(frozen=True, slots=True)
class OutcomeResearchResult:
    dataset_tiers: tuple[DatasetTier, ...]
    evidence_label: str | None
    folds: tuple[OutcomeFoldResult, ...]
    pnl_by_underlying: tuple[tuple[str, Decimal], ...]
    pnl_by_market: tuple[tuple[str, Decimal], ...]
    pnl_by_volatility_regime: tuple[tuple[str, Decimal], ...]
    pnl_by_expiry_bucket: tuple[tuple[str, Decimal], ...]
    bootstrap: BootstrapInterval


def _brier(probabilities: tuple[Decimal, ...], outcomes: tuple[int, ...]) -> Decimal:
    return sum(
        (
            (probability - outcome) ** 2
            for probability, outcome in zip(
                probabilities, outcomes, strict=True
            )
        ),
        Decimal(0),
    ) / len(outcomes)


def _log_loss(probabilities: tuple[Decimal, ...], outcomes: tuple[int, ...]) -> Decimal:
    epsilon = Decimal("1e-12")
    total = 0.0
    for probability, outcome in zip(probabilities, outcomes, strict=True):
        bounded = min(max(probability, epsilon), Decimal(1) - epsilon)
        total -= outcome * math.log(float(bounded))
        total -= (1 - outcome) * math.log(float(Decimal(1) - bounded))
    return Decimal(str(total / len(outcomes)))


def _profit_factor(pnls: tuple[Decimal, ...]) -> Decimal | None:
    gains = sum((value for value in pnls if value > 0), Decimal(0))
    losses = -sum((value for value in pnls if value < 0), Decimal(0))
    return gains / losses if losses > 0 else None


def _max_drawdown(pnls: tuple[Decimal, ...]) -> Decimal:
    equity = Decimal(0)
    peak = Decimal(0)
    drawdown = Decimal(0)
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = max(drawdown, peak - equity)
    return drawdown


def cluster_bootstrap_pnl(
    samples: tuple[OutcomeSample, ...],
    *,
    iterations: int = 1_000,
    seed: int = 0,
) -> BootstrapInterval:
    if not samples or iterations <= 0:
        raise ValueError("bootstrap requires samples and positive iterations")
    clusters: dict[tuple[str, str], Decimal] = {}
    for sample in samples:
        day = datetime.fromtimestamp(sample.observed_ts_ms / 1000, tz=UTC).date()
        key = (day.isoformat(), sample.market)
        clusters[key] = clusters.get(key, Decimal(0)) + sample.maker_pnl_usd
    values = tuple(clusters[key] for key in sorted(clusters))
    generator = random.Random(seed)
    totals = sorted(
        sum(
            (values[generator.randrange(len(values))] for _ in values),
            Decimal(0),
        )
        for _ in range(iterations)
    )

    def percentile(fraction: float) -> Decimal:
        return totals[round((len(totals) - 1) * fraction)]

    return BootstrapInterval(
        iterations=iterations,
        seed=seed,
        p05_usd=percentile(0.05),
        p50_usd=percentile(0.50),
        p95_usd=percentile(0.95),
    )


class OutcomeResearchEvaluator:
    def __init__(self, benchmark: DigitalOutcomeBenchmark | None = None) -> None:
        self.benchmark = benchmark or DigitalOutcomeBenchmark()

    def evaluate(
        self,
        *,
        samples: tuple[OutcomeSample, ...],
        windows: tuple[FoldWindow, ...],
        dataset_tiers: tuple[DatasetTier, ...],
        bootstrap_iterations: int = 1_000,
        bootstrap_seed: int = 0,
    ) -> OutcomeResearchResult:
        authorization = require_replay_use(ReplayUse.FAIR_VALUE, dataset_tiers)
        ordered = tuple(
            sorted(samples, key=lambda item: (item.observed_ts_ms, item.sample_id))
        )
        fold_results: list[OutcomeFoldResult] = []
        all_test_samples: list[OutcomeSample] = []
        seen_test_ids: set[str] = set()
        for window in windows:
            train = tuple(
                sample
                for sample in ordered
                if window.train_begin_ms <= sample.observed_ts_ms < window.train_end_ms
            )
            calibration_all = tuple(
                sample
                for sample in ordered
                if window.calibration_begin_ms
                <= sample.observed_ts_ms
                < window.calibration_end_ms
            )
            calibration = tuple(
                sample
                for sample in calibration_all
                if sample.settlement_ts_ms <= window.test_begin_ms - window.purge_ms
            )
            test = tuple(
                sample
                for sample in ordered
                if window.test_begin_ms <= sample.observed_ts_ms < window.test_end_ms
            )
            if not train or not calibration or not test:
                raise ValueError(f"fold {window.fold_id} has an empty partition")
            duplicate_test = seen_test_ids.intersection(
                sample.sample_id for sample in test
            )
            if duplicate_test:
                raise ValueError("test folds must not overlap")
            seen_test_ids.update(sample.sample_id for sample in test)
            raw_calibration = tuple(
                (self.benchmark.probability(sample), sample.outcome)
                for sample in calibration
            )
            calibrator = IsotonicCalibrator.fit(raw_calibration)
            raw_test = tuple(self.benchmark.probability(sample) for sample in test)
            calibrated_test = tuple(calibrator.predict(value) for value in raw_test)
            outcomes = tuple(sample.outcome for sample in test)
            pnls = tuple(sample.maker_pnl_usd for sample in test)
            fold_results.append(
                OutcomeFoldResult(
                    fold_id=window.fold_id,
                    train_count=len(train),
                    calibration_count=len(calibration),
                    purged_calibration_count=len(calibration_all) - len(calibration),
                    test_count=len(test),
                    brier_raw=_brier(raw_test, outcomes),
                    brier_calibrated=_brier(calibrated_test, outcomes),
                    log_loss_raw=_log_loss(raw_test, outcomes),
                    log_loss_calibrated=_log_loss(calibrated_test, outcomes),
                    maker_pnl_usd=sum(pnls, Decimal(0)),
                    legacy_bot_pnl_usd=sum(
                        (sample.legacy_bot_pnl_usd for sample in test), Decimal(0)
                    ),
                    do_nothing_pnl_usd=Decimal(0),
                    profit_factor=_profit_factor(pnls),
                    max_drawdown_usd=_max_drawdown(pnls),
                )
            )
            all_test_samples.extend(test)
        pnl_by_underlying: dict[str, Decimal] = {}
        pnl_by_market: dict[str, Decimal] = {}
        pnl_by_regime: dict[str, Decimal] = {}
        pnl_by_expiry: dict[str, Decimal] = {}
        for sample in all_test_samples:
            pnl_by_underlying[sample.underlying] = (
                pnl_by_underlying.get(sample.underlying, Decimal(0))
                + sample.maker_pnl_usd
            )
            pnl_by_market[sample.market] = (
                pnl_by_market.get(sample.market, Decimal(0)) + sample.maker_pnl_usd
            )
            pnl_by_regime[sample.volatility_regime] = (
                pnl_by_regime.get(sample.volatility_regime, Decimal(0))
                + sample.maker_pnl_usd
            )
            pnl_by_expiry[sample.expiry_bucket] = (
                pnl_by_expiry.get(sample.expiry_bucket, Decimal(0))
                + sample.maker_pnl_usd
            )
        return OutcomeResearchResult(
            dataset_tiers=tuple(
                sorted(set(dataset_tiers), key=lambda item: item.value)
            ),
            evidence_label=authorization.required_label,
            folds=tuple(fold_results),
            pnl_by_underlying=tuple(sorted(pnl_by_underlying.items())),
            pnl_by_market=tuple(sorted(pnl_by_market.items())),
            pnl_by_volatility_regime=tuple(sorted(pnl_by_regime.items())),
            pnl_by_expiry_bucket=tuple(sorted(pnl_by_expiry.items())),
            bootstrap=cluster_bootstrap_pnl(
                tuple(all_test_samples),
                iterations=bootstrap_iterations,
                seed=bootstrap_seed,
            ),
        )
