"""Learning data schemas — the shared vocabulary for all learning components.

These are plain data objects (Pydantic models and dataclasses), not ORM models.
They define the contract between feature extraction, reward computation,
memory, ranking, and evaluation.
"""

from amplify.learning.schemas.observation import Observation, OutcomeMetrics
from amplify.learning.schemas.feature import FeatureVector, FeatureSchema
from amplify.learning.schemas.insight import Insight, InsightType
from amplify.learning.schemas.score import ScoredAction, RankingExplanation

__all__ = [
    "Observation",
    "OutcomeMetrics",
    "FeatureVector",
    "FeatureSchema",
    "Insight",
    "InsightType",
    "ScoredAction",
    "RankingExplanation",
]
