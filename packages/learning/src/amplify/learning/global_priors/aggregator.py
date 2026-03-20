"""Prior aggregator — builds global priors from tenant observations.

This is the only component that reads observations from multiple tenants.
It enforces privacy constraints (k-anonymity, field redaction) before
producing any cross-tenant statistics.
"""

from __future__ import annotations

import math
import uuid
from collections import defaultdict
from typing import Any

from amplify.learning.config import LearningConfig, PrivacyConfig
from amplify.learning.global_priors.prior import GlobalPrior
from amplify.learning.schemas.observation import Observation


class PriorAggregator:
    """Aggregates tenant observations into global priors.

    All privacy enforcement happens here.  Callers pass in observations
    from multiple tenants; the aggregator strips PII, checks k-anonymity,
    and produces only aggregate statistics.
    """

    def __init__(self, config: LearningConfig | None = None) -> None:
        self.config = config or LearningConfig()

    def aggregate(
        self,
        observations: list[Observation],
        feature_name: str,
        action_type: str = "",
    ) -> list[GlobalPrior]:
        """Build global priors for a specific feature across tenants.

        Returns one GlobalPrior per distinct value of the feature.
        Priors that don't meet the k-anonymity threshold are excluded.
        """
        if not self.config.privacy.cross_tenant_enabled:
            return []

        # Group by feature value
        buckets: dict[str, list[_Bucket]] = defaultdict(list)
        for obs in observations:
            if action_type and obs.action_type != action_type:
                continue
            value = obs.features.get(feature_name)
            if value is None or obs.reward is None:
                continue
            buckets[str(value)].append(_Bucket(
                tenant_id=obs.tenant_id,
                reward=obs.reward,
            ))

        # Build priors, enforcing k-anonymity
        priors: list[GlobalPrior] = []
        k = self.config.privacy.k_anonymity_threshold

        for value, bucket_list in buckets.items():
            tenant_ids = {b.tenant_id for b in bucket_list}
            if len(tenant_ids) < k:
                continue  # doesn't meet k-anonymity threshold

            rewards = [b.reward for b in bucket_list]
            mean = sum(rewards) / len(rewards)
            std = _std_dev(rewards) if len(rewards) > 1 else None

            priors.append(GlobalPrior(
                feature_name=feature_name,
                feature_value=value,
                action_type=action_type,
                mean_reward=mean,
                observation_count=len(rewards),
                tenant_count=len(tenant_ids),
                std_dev=std,
                confidence_lower=mean - 1.96 * (std / math.sqrt(len(rewards))) if std else None,
                confidence_upper=mean + 1.96 * (std / math.sqrt(len(rewards))) if std else None,
            ))

        return sorted(priors, key=lambda p: p.mean_reward, reverse=True)


class _Bucket:
    """Internal grouping structure."""

    __slots__ = ("tenant_id", "reward")

    def __init__(self, tenant_id: uuid.UUID, reward: float) -> None:
        self.tenant_id = tenant_id
        self.reward = reward


def _std_dev(values: list[float]) -> float:
    n = len(values)
    if n < 2:
        return 0.0
    mean = sum(values) / n
    variance = sum((x - mean) ** 2 for x in values) / (n - 1)
    return math.sqrt(variance)
