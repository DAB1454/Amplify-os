"""Ranker — scores and ranks candidate actions.

The ranker implements a simple weighted scoring model:
1. For each candidate, compute feature-based scores from tenant observations.
2. Blend with global priors (weighted by cold-start threshold).
3. Optionally inject exploration noise.
4. Return ranked list with full explanations.

No opaque ML — every score component is named and weighted.
"""

from __future__ import annotations

import random
import uuid
from typing import Any

from amplify.learning.config import LearningConfig, RankingConfig
from amplify.learning.global_priors.prior import GlobalPrior
from amplify.learning.schemas.observation import Observation
from amplify.learning.schemas.score import (
    RankingExplanation,
    ScoreComponent,
    ScoredAction,
)


class Ranker:
    """Scores candidate actions using tenant data and global priors."""

    def __init__(self, config: LearningConfig | None = None) -> None:
        self.config = config or LearningConfig()
        self._ranking = self.config.ranking

    def rank(
        self,
        candidates: list[dict[str, Any]],
        tenant_observations: list[Observation],
        global_priors: list[GlobalPrior] | None = None,
    ) -> list[ScoredAction]:
        """Score and rank a list of candidate actions.

        Each candidate is a dict with at least 'action_type' and feature
        keys matching the observation features.

        Returns candidates sorted by score (descending), each with a
        full explanation.
        """
        global_priors = global_priors or []
        obs_count = len(tenant_observations)

        # Determine blend weight — shifts toward tenant as data grows
        tenant_weight = self._compute_tenant_weight(obs_count)
        prior_weight = 1.0 - tenant_weight

        # Build lookup for tenant feature averages
        tenant_avg = self._tenant_feature_averages(tenant_observations)

        # Build lookup for global priors
        prior_lookup = self._prior_lookup(global_priors)

        # Score each candidate
        scored: list[ScoredAction] = []
        for candidate in candidates[: self._ranking.max_candidates]:
            score, explanation = self._score_candidate(
                candidate,
                tenant_avg=tenant_avg,
                prior_lookup=prior_lookup,
                tenant_weight=tenant_weight,
                prior_weight=prior_weight,
                obs_count=obs_count,
            )
            scored.append(ScoredAction(
                action_id=candidate.get("action_id"),
                action_type=candidate.get("action_type", ""),
                action_params=candidate,
                score=score,
                explanation=explanation,
            ))

        # Sort by score descending
        scored.sort(key=lambda s: s.score, reverse=True)

        # Assign ranks
        for i, s in enumerate(scored, 1):
            s.rank = i

        return scored

    def _compute_tenant_weight(self, obs_count: int) -> float:
        """Blend weight for tenant-specific vs global prior.

        Returns 0.0 when no tenant data, ramps up to (1 - tenant_prior_blend)
        as observations pass the cold-start threshold.
        """
        threshold = self._ranking.cold_start_threshold
        if obs_count == 0:
            return 0.0
        if obs_count >= threshold:
            return 1.0 - self._ranking.tenant_prior_blend
        return (obs_count / threshold) * (1.0 - self._ranking.tenant_prior_blend)

    def _tenant_feature_averages(
        self,
        observations: list[Observation],
    ) -> dict[str, dict[str, float]]:
        """Compute average reward per feature value from tenant data.

        Returns {feature_name: {feature_value: avg_reward}}.
        """
        sums: dict[str, dict[str, list[float]]] = {}
        for obs in observations:
            if obs.reward is None:
                continue
            for feat_name, feat_value in obs.features.items():
                sums.setdefault(feat_name, {})
                key = str(feat_value)
                sums[feat_name].setdefault(key, [])
                sums[feat_name][key].append(obs.reward)

        averages: dict[str, dict[str, float]] = {}
        for feat_name, value_rewards in sums.items():
            averages[feat_name] = {
                val: sum(rews) / len(rews)
                for val, rews in value_rewards.items()
            }
        return averages

    def _prior_lookup(
        self,
        priors: list[GlobalPrior],
    ) -> dict[str, dict[str, float]]:
        """Build {feature_name: {feature_value: mean_reward}} from priors."""
        lookup: dict[str, dict[str, float]] = {}
        for p in priors:
            if not p.is_reliable:
                continue
            lookup.setdefault(p.feature_name, {})
            lookup[p.feature_name][p.feature_value] = p.mean_reward
        return lookup

    def _score_candidate(
        self,
        candidate: dict[str, Any],
        *,
        tenant_avg: dict[str, dict[str, float]],
        prior_lookup: dict[str, dict[str, float]],
        tenant_weight: float,
        prior_weight: float,
        obs_count: int,
    ) -> tuple[float, RankingExplanation]:
        """Score a single candidate action."""
        components: list[ScoreComponent] = []

        for feat_name, feat_value in candidate.items():
            if feat_name in ("action_id", "action_type"):
                continue
            str_value = str(feat_value)

            # Tenant score for this feature value
            t_score = tenant_avg.get(feat_name, {}).get(str_value)
            # Global prior for this feature value
            p_score = prior_lookup.get(feat_name, {}).get(str_value)

            if t_score is None and p_score is None:
                continue

            # Blend
            if t_score is not None and p_score is not None:
                blended = t_score * tenant_weight + p_score * prior_weight
                source = "blended"
            elif t_score is not None:
                blended = t_score
                source = "tenant"
            else:
                blended = p_score  # type: ignore[assignment]
                source = "global_prior"

            components.append(ScoreComponent(
                name=feat_name,
                weight=1.0,
                raw_value=blended,
                weighted_value=blended,
                explanation=f"{feat_name}={str_value} → {blended:.3f} ({source})",
            ))

        total = sum(c.weighted_value for c in components) / max(len(components), 1)

        # Exploration: small chance of random boost
        is_exploratory = random.random() < self._ranking.exploration_rate
        if is_exploratory:
            total += random.uniform(0.0, 0.2)

        explanation = RankingExplanation(
            components=components,
            tenant_weight=tenant_weight,
            prior_weight=prior_weight,
            is_exploratory=is_exploratory,
            observation_count=obs_count,
        )

        return total, explanation
