"""Privacy filter — strips PII and enforces data boundaries.

Every observation that crosses a tenant boundary passes through
PrivacyFilter.sanitize() first.  This is an explicit checkpoint,
not an implicit pipeline step — callers must opt in.
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter

from amplify.learning.config import PrivacyConfig
from amplify.learning.schemas.observation import Observation

logger = logging.getLogger(__name__)


class PrivacyFilter:
    """Sanitizes observations for cross-tenant use."""

    def __init__(self, config: PrivacyConfig | None = None) -> None:
        self.config = config or PrivacyConfig()

    def sanitize(self, observation: Observation) -> Observation | None:
        """Strip PII fields from an observation for cross-tenant use.

        Returns a sanitized copy, or None if the observation cannot be
        safely shared (e.g. features are too unique to anonymize).
        """
        if not self.config.cross_tenant_enabled:
            return None

        # Strip redacted fields from features
        clean_features = {
            k: v
            for k, v in observation.features.items()
            if k not in self.config.redacted_fields
        }

        # Build a sanitized copy with tenant_id zeroed out
        return Observation(
            id=uuid.uuid4(),  # new ID — no link back to original
            tenant_id=uuid.UUID(int=0),  # anonymized
            artist_id=None,  # stripped
            action_type=observation.action_type,
            action_id=None,  # stripped
            features=clean_features,
            outcomes=observation.outcomes,
            reward=observation.reward,
            observed_at=observation.observed_at,
            outcome_measured_at=observation.outcome_measured_at,
            source=observation.source,
            confidence=observation.confidence,
        )

    def check_cardinality(
        self,
        observations: list[Observation],
        feature_name: str,
    ) -> bool:
        """Check if a feature's cardinality is safe for cross-tenant use.

        Returns True if the number of distinct values is within the
        configured limit.
        """
        values = Counter(
            obs.features.get(feature_name)
            for obs in observations
            if feature_name in obs.features
        )
        cardinality = len(values)
        if cardinality > self.config.max_categorical_cardinality:
            logger.warning(
                "Feature %s has cardinality %d (limit %d) — excluded from priors",
                feature_name,
                cardinality,
                self.config.max_categorical_cardinality,
            )
            return False
        return True
