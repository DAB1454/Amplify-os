"""Evaluation metrics — retrospective accuracy of learning predictions.

These are offline metrics computed periodically.  They answer "is the
learning system producing useful rankings?" by comparing predicted
scores against actual observed rewards.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from amplify.learning.schemas.observation import Observation
from amplify.learning.schemas.score import ScoredAction


@dataclass
class EvaluationReport:
    """Summary of how well predictions matched reality."""

    # How many prediction-outcome pairs were evaluated
    sample_size: int = 0

    # Mean Absolute Error between predicted scores and actual rewards
    mae: float | None = None

    # Spearman rank correlation: do higher-scored actions get higher rewards?
    rank_correlation: float | None = None

    # What fraction of top-3 predictions ended up in the top-3 outcomes
    top_k_precision: float | None = None
    top_k: int = 3

    evaluated_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def is_useful(self) -> bool:
        """Heuristic: is the learning system adding value?"""
        if self.rank_correlation is not None and self.rank_correlation > 0.3:
            return True
        if self.top_k_precision is not None and self.top_k_precision > 0.33:
            return True  # better than random
        return False


def evaluate_predictions(
    predictions: list[ScoredAction],
    actuals: list[Observation],
) -> EvaluationReport:
    """Compare predicted scores against actual observed rewards.

    Matches predictions to actuals by action_id.  Only matched pairs
    are included in the evaluation.
    """
    actual_map = {obs.action_id: obs for obs in actuals if obs.action_id and obs.reward is not None}

    matched_pred: list[float] = []
    matched_actual: list[float] = []

    for pred in predictions:
        if pred.action_id and pred.action_id in actual_map:
            matched_pred.append(pred.score)
            matched_actual.append(actual_map[pred.action_id].reward)  # type: ignore[arg-type]

    n = len(matched_pred)
    if n == 0:
        return EvaluationReport(sample_size=0)

    # MAE
    mae = sum(abs(p - a) for p, a in zip(matched_pred, matched_actual)) / n

    # Rank correlation (Spearman)
    rank_corr = _spearman(matched_pred, matched_actual) if n >= 3 else None

    # Top-K precision
    k = min(3, n)
    top_pred_ids = set(
        predictions[i].action_id
        for i in sorted(range(len(predictions)), key=lambda i: predictions[i].score, reverse=True)[:k]
        if predictions[i].action_id
    )
    top_actual_ids = set(
        actuals[i].action_id
        for i in sorted(
            range(len(actuals)),
            key=lambda i: actuals[i].reward if actuals[i].reward is not None else -1,
            reverse=True,
        )[:k]
        if actuals[i].action_id
    )
    top_k_precision = len(top_pred_ids & top_actual_ids) / k if k > 0 else None

    return EvaluationReport(
        sample_size=n,
        mae=mae,
        rank_correlation=rank_corr,
        top_k_precision=top_k_precision,
        top_k=k,
    )


def _spearman(x: list[float], y: list[float]) -> float:
    """Compute Spearman rank correlation coefficient."""
    n = len(x)
    if n < 3:
        return 0.0

    def _ranks(values: list[float]) -> list[float]:
        sorted_indices = sorted(range(n), key=lambda i: values[i])
        ranks = [0.0] * n
        for rank, idx in enumerate(sorted_indices, 1):
            ranks[idx] = float(rank)
        return ranks

    rx = _ranks(x)
    ry = _ranks(y)
    d_sq = sum((a - b) ** 2 for a, b in zip(rx, ry))
    return 1 - (6 * d_sq) / (n * (n * n - 1))
