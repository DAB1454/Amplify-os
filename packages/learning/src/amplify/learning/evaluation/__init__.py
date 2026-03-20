"""Evaluation — measures whether the learning system is actually helping.

Provides offline metrics to answer:
- Are recommendations improving outcomes over time?
- Are insights accurate (do they predict future outcomes)?
- Is there detectable drift (are old patterns stale)?

All evaluation is retrospective — no live A/B testing framework yet.
"""

from amplify.learning.evaluation.metrics import EvaluationReport, evaluate_predictions
from amplify.learning.evaluation.replay import (
    ComparisonReport,
    PolicyEvalReport,
    PostEvalResult,
    PostOpportunity,
    RankingPolicy,
    RankingPolicySpec,
    ReplayDataset,
    ReplayEngine,
    build_sample_dataset,
)

__all__ = [
    "ComparisonReport",
    "EvaluationReport",
    "PolicyEvalReport",
    "PostEvalResult",
    "PostOpportunity",
    "RankingPolicy",
    "RankingPolicySpec",
    "ReplayDataset",
    "ReplayEngine",
    "build_sample_dataset",
    "evaluate_predictions",
]
