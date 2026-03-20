"""Global priors — privacy-safe cross-tenant aggregate knowledge.

Global priors answer "what generally works" by aggregating observations
across tenants after privacy filtering.  They are used to bootstrap
new tenants (cold-start) and to supplement sparse tenant data.

No individual tenant's data is identifiable in a global prior — all
priors are aggregated statistics that meet the k-anonymity threshold.
"""

from amplify.learning.global_priors.aggregator import PriorAggregator
from amplify.learning.global_priors.prior import GlobalPrior
from amplify.learning.global_priors.service import (
    CATEGORY_FEATURE_MAP,
    PATTERN_CATEGORIES,
    GlobalPriorService,
    GlobalRecommendation,
    ThresholdConfig,
    compute_confidence,
    passes_threshold,
    redact_observation,
    validate_no_pii,
)

__all__ = [
    "CATEGORY_FEATURE_MAP",
    "PATTERN_CATEGORIES",
    "GlobalPrior",
    "GlobalPriorService",
    "GlobalRecommendation",
    "PriorAggregator",
    "ThresholdConfig",
    "compute_confidence",
    "passes_threshold",
    "redact_observation",
    "validate_no_pii",
]
