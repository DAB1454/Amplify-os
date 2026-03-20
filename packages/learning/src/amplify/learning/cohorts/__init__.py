"""Cross-tenant cohort modeling.

Groups tenants by marketing-relevant traits (genre, audience size, content
style, release type, channel mix) and aggregates normalized outcomes at
the cohort level.  Provides cold-start priors for new or low-data tenants
without exposing any raw tenant identifiers or private content.

All cohort outputs are privacy-safe aggregate statistics that meet
k-anonymity thresholds.
"""

from amplify.learning.cohorts.engine import (
    CohortAssignment,
    CohortDefinition,
    CohortEngine,
    CohortPrior,
    CohortStats,
    TenantProfile,
)

__all__ = [
    "CohortAssignment",
    "CohortDefinition",
    "CohortEngine",
    "CohortPrior",
    "CohortStats",
    "TenantProfile",
]
