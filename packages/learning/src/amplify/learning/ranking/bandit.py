"""Contextual bandit for candidate post selection.

Wraps the rule-based PostRanker with adaptive exploration.  Two policies:
- **epsilon-greedy** — with probability ε pick uniformly, otherwise exploit
- **thompson sampling** — sample from per-arm Beta posteriors

Safety guarantees:
- Falls back to rule-based PostRanker when data is sparse or confidence low
- Exploration is bounded by configurable epsilon / prior strength
- Every decision logs context, chosen action, action probability, and reward
- Shadow mode runs the bandit alongside the rule-based ranker without
  overriding the live selection
- Per-tenant enablement via BanditConfig flags

The bandit satisfies the ``RankingPolicy`` protocol from the evaluation
replay engine, so it can be evaluated offline with ``ReplayEngine``.

Usage::

    bandit = ContextualBandit(
        fallback_ranker=PostRanker(patterns=..., profile="pre_release"),
        config=BanditConfig(policy="thompson_sampling", shadow_mode=True),
    )
    decision = bandit.select(candidates)
    # decision.chosen is a RankedPost, decision.action_probabilities logged

    # Later, when reward arrives:
    bandit.update(decision.chosen.candidate_id, context_key, reward=0.72)
"""

from __future__ import annotations

import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from amplify.learning.ranking.post_ranker import (
    CandidatePost,
    PostRanker,
    RankedPost,
    ScoreFactor,
)


# ── Configuration ────────────────────────────────────────────────


@dataclass(frozen=True)
class BanditConfig:
    """Configuration for the contextual bandit."""

    # Policy type: "epsilon_greedy" or "thompson_sampling"
    policy: str = "epsilon_greedy"

    # Epsilon-greedy parameters
    epsilon: float = 0.1            # exploration probability
    epsilon_min: float = 0.01       # floor after decay
    epsilon_decay: float = 0.995    # per-decision decay multiplier

    # Thompson sampling prior parameters (Beta distribution)
    prior_alpha: float = 1.0        # initial alpha (successes + 1)
    prior_beta: float = 1.0         # initial beta (failures + 1)

    # Sparse-data fallback
    min_observations: int = 20      # per arm, before bandit kicks in
    min_total_observations: int = 50  # across all arms
    confidence_threshold: float = 0.3  # min rank correlation to trust bandit

    # Exploration bounds
    max_exploration_fraction: float = 0.3  # hard cap on exploration actions

    # Shadow mode: bandit runs but does not override rule-based selection
    shadow_mode: bool = True

    # Per-tenant enablement
    enabled: bool = True

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty = valid)."""
        errors = []
        if self.policy not in ("epsilon_greedy", "thompson_sampling"):
            errors.append(f"Unknown policy: {self.policy}")
        if not 0.0 <= self.epsilon <= 1.0:
            errors.append(f"epsilon must be in [0, 1], got {self.epsilon}")
        if not 0.0 <= self.max_exploration_fraction <= 1.0:
            errors.append(f"max_exploration_fraction must be in [0, 1]")
        if self.min_observations < 0:
            errors.append("min_observations must be >= 0")
        return errors


# ── Arm statistics ───────────────────────────────────────────────


@dataclass
class ArmStats:
    """Sufficient statistics for one bandit arm (one candidate context key)."""

    arm_id: str
    alpha: float = 1.0   # Beta posterior α (successes + prior)
    beta: float = 1.0     # Beta posterior β (failures + prior)
    n_pulls: int = 0
    total_reward: float = 0.0
    last_pulled_at: str = ""

    @property
    def mean_reward(self) -> float:
        return self.total_reward / self.n_pulls if self.n_pulls > 0 else 0.0

    @property
    def observation_count(self) -> int:
        return self.n_pulls

    def sample_thompson(self, rng: random.Random | None = None) -> float:
        """Draw from the Beta posterior."""
        r = rng or random
        return r.betavariate(max(self.alpha, 0.01), max(self.beta, 0.01))

    def update(self, reward: float) -> None:
        """Update arm statistics with observed reward.

        Reward is expected to be in [0, 1].  Values outside this range
        are clamped.
        """
        clamped = max(0.0, min(1.0, reward))
        self.n_pulls += 1
        self.total_reward += clamped
        self.alpha += clamped
        self.beta += (1.0 - clamped)
        self.last_pulled_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "arm_id": self.arm_id,
            "alpha": round(self.alpha, 4),
            "beta": round(self.beta, 4),
            "n_pulls": self.n_pulls,
            "mean_reward": round(self.mean_reward, 6),
        }


# ── Decision record ─────────────────────────────────────────────


@dataclass
class BanditDecision:
    """Record of a single bandit decision — everything needed for analysis."""

    decision_id: str
    chosen: RankedPost
    ranking: list[RankedPost]
    action_probabilities: dict[str, float]  # candidate_id → selection prob
    is_exploratory: bool
    is_shadow: bool                          # True if bandit didn't override
    fallback_choice: RankedPost | None       # what rule-based would have picked
    context: dict[str, Any]
    policy_used: str
    epsilon_at_decision: float
    timestamp: str

    @property
    def chosen_probability(self) -> float:
        return self.action_probabilities.get(self.chosen.candidate_id, 0.0)

    def to_log_dict(self) -> dict[str, Any]:
        """Serializable log entry with all required fields for later analysis."""
        return {
            "decision_id": self.decision_id,
            "chosen_id": self.chosen.candidate_id,
            "chosen_score": self.chosen.score,
            "chosen_rank": self.chosen.rank,
            "chosen_probability": round(self.chosen_probability, 6),
            "action_probabilities": {
                k: round(v, 6) for k, v in self.action_probabilities.items()
            },
            "is_exploratory": self.is_exploratory,
            "is_shadow": self.is_shadow,
            "fallback_choice_id": (
                self.fallback_choice.candidate_id if self.fallback_choice else None
            ),
            "policy_used": self.policy_used,
            "epsilon": round(self.epsilon_at_decision, 6),
            "n_candidates": len(self.ranking),
            "timestamp": self.timestamp,
            "context": self.context,
        }


# ── Contextual bandit ───────────────────────────────────────────


class ContextualBandit:
    """Contextual bandit policy for candidate post selection.

    Implements the ``RankingPolicy`` protocol (``rank()`` method) so it
    can be plugged into the evaluation replay engine.
    """

    def __init__(
        self,
        fallback_ranker: PostRanker | None = None,
        config: BanditConfig | None = None,
        arm_stats: dict[str, ArmStats] | None = None,
        rng_seed: int | None = None,
    ) -> None:
        self.config = config or BanditConfig()
        self.fallback = fallback_ranker or PostRanker()
        self._arms: dict[str, ArmStats] = arm_stats or {}
        self._rng = random.Random(rng_seed)
        self._current_epsilon = self.config.epsilon
        self._decisions: list[BanditDecision] = []
        self._total_decisions = 0
        self._exploratory_decisions = 0

    # ── RankingPolicy protocol ─────────────────────────────────

    def rank(self, candidates: list[CandidatePost]) -> list[RankedPost]:
        """Score and rank candidates — satisfies RankingPolicy protocol.

        In non-shadow mode, the top-ranked candidate is the bandit's choice.
        In shadow mode, the ranking is identical to the fallback ranker.
        """
        decision = self.select(candidates)
        if decision.is_shadow:
            return decision.ranking  # fallback ordering
        # Re-rank: chosen candidate moves to rank 1
        reranked = []
        for i, rp in enumerate(decision.ranking):
            if rp.candidate_id == decision.chosen.candidate_id:
                reranked.insert(0, RankedPost(
                    candidate_id=rp.candidate_id,
                    rank=1,
                    score=rp.score,
                    factors=rp.factors,
                    profile=rp.profile,
                    warnings=rp.warnings,
                ))
            else:
                reranked.append(rp)
        # Fix ranks
        for i, rp in enumerate(reranked):
            if i > 0:
                reranked[i] = RankedPost(
                    candidate_id=rp.candidate_id,
                    rank=i + 1,
                    score=rp.score,
                    factors=rp.factors,
                    profile=rp.profile,
                    warnings=rp.warnings,
                )
        return reranked

    # ── Core selection ─────────────────────────────────────────

    def select(
        self,
        candidates: list[CandidatePost],
        context: dict[str, Any] | None = None,
    ) -> BanditDecision:
        """Select a candidate post.

        Returns a BanditDecision containing the chosen post, action
        probabilities for all candidates, and metadata.
        """
        if not candidates:
            raise ValueError("Cannot select from empty candidate list")

        ctx = context or {}
        fallback_ranking = self.fallback.rank(candidates)
        fallback_choice = fallback_ranking[0] if fallback_ranking else None

        # Check if we should fall back to rule-based
        should_fallback = self._should_fallback(candidates)

        if should_fallback or not self.config.enabled:
            probs = self._uniform_probabilities(fallback_ranking)
            # Give all probability to the top-ranked
            probs = {cid: 0.0 for cid in probs}
            if fallback_choice:
                probs[fallback_choice.candidate_id] = 1.0

            return BanditDecision(
                decision_id=uuid.uuid4().hex[:16],
                chosen=fallback_choice,
                ranking=fallback_ranking,
                action_probabilities=probs,
                is_exploratory=False,
                is_shadow=False,
                fallback_choice=fallback_choice,
                context={"fallback_reason": "sparse_data", **ctx},
                policy_used="fallback",
                epsilon_at_decision=self._current_epsilon,
                timestamp=datetime.now(timezone.utc).isoformat(),
            )

        # Compute action probabilities
        if self.config.policy == "epsilon_greedy":
            probs, is_exploratory, chosen = self._epsilon_greedy(fallback_ranking)
        else:
            probs, is_exploratory, chosen = self._thompson_sampling(
                candidates, fallback_ranking,
            )

        # Shadow mode: log but don't override
        if self.config.shadow_mode:
            effective_chosen = fallback_choice
        else:
            effective_chosen = chosen

        self._total_decisions += 1
        if is_exploratory:
            self._exploratory_decisions += 1

        # Decay epsilon
        self._current_epsilon = max(
            self.config.epsilon_min,
            self._current_epsilon * self.config.epsilon_decay,
        )

        decision = BanditDecision(
            decision_id=uuid.uuid4().hex[:16],
            chosen=effective_chosen,
            ranking=fallback_ranking,
            action_probabilities=probs,
            is_exploratory=is_exploratory,
            is_shadow=self.config.shadow_mode,
            fallback_choice=fallback_choice,
            context=ctx,
            policy_used=self.config.policy,
            epsilon_at_decision=self._current_epsilon,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )
        self._decisions.append(decision)
        return decision

    # ── Learning update ────────────────────────────────────────

    def update(
        self,
        candidate_id: str,
        context_key: str,
        reward: float,
    ) -> ArmStats:
        """Update arm statistics after observing a reward.

        ``context_key`` groups observations into arms.  For simple bandits,
        use ``candidate_id``.  For contextual bandits, use a composite key
        like ``f"{platform}:{content_type}:{cta_type}"``.
        """
        arm = self._get_or_create_arm(context_key)
        arm.update(reward)
        return arm

    # ── Introspection ──────────────────────────────────────────

    @property
    def total_observations(self) -> int:
        return sum(a.n_pulls for a in self._arms.values())

    @property
    def exploration_rate_actual(self) -> float:
        if self._total_decisions == 0:
            return 0.0
        return self._exploratory_decisions / self._total_decisions

    @property
    def arm_count(self) -> int:
        return len(self._arms)

    def get_arm_stats(self) -> dict[str, ArmStats]:
        return dict(self._arms)

    def get_arm(self, context_key: str) -> ArmStats | None:
        return self._arms.get(context_key)

    @property
    def decisions(self) -> list[BanditDecision]:
        return list(self._decisions)

    def summary(self) -> dict[str, Any]:
        """Diagnostic summary for dashboard display."""
        return {
            "policy": self.config.policy,
            "shadow_mode": self.config.shadow_mode,
            "enabled": self.config.enabled,
            "total_decisions": self._total_decisions,
            "exploratory_decisions": self._exploratory_decisions,
            "exploration_rate_actual": round(self.exploration_rate_actual, 4),
            "current_epsilon": round(self._current_epsilon, 6),
            "total_observations": self.total_observations,
            "arm_count": self.arm_count,
            "arms": {k: v.to_dict() for k, v in self._arms.items()},
        }

    # ── Policy implementations ─────────────────────────────────

    def _epsilon_greedy(
        self,
        fallback_ranking: list[RankedPost],
    ) -> tuple[dict[str, float], bool, RankedPost]:
        """Epsilon-greedy: exploit with probability 1-ε, explore with ε."""
        n = len(fallback_ranking)
        eps = min(self._current_epsilon, self.config.max_exploration_fraction)

        # Action probabilities
        probs: dict[str, float] = {}
        best = fallback_ranking[0]
        for rp in fallback_ranking:
            if rp.candidate_id == best.candidate_id:
                probs[rp.candidate_id] = (1.0 - eps) + (eps / n)
            else:
                probs[rp.candidate_id] = eps / n

        # Sample
        is_exploratory = self._rng.random() < eps
        if is_exploratory:
            chosen = self._rng.choice(fallback_ranking)
        else:
            chosen = best

        return probs, is_exploratory, chosen

    def _thompson_sampling(
        self,
        candidates: list[CandidatePost],
        fallback_ranking: list[RankedPost],
    ) -> tuple[dict[str, float], bool, RankedPost]:
        """Thompson sampling using per-arm Beta posteriors."""
        # Sample from each arm's posterior
        samples: dict[str, float] = {}
        for rp in fallback_ranking:
            arm = self._get_or_create_arm(rp.candidate_id)
            samples[rp.candidate_id] = arm.sample_thompson(self._rng)

        # Normalize to approximate action probabilities
        total = sum(samples.values()) or 1.0
        probs = {cid: s / total for cid, s in samples.items()}

        # Choose the arm with the highest Thompson sample
        best_cid = max(samples, key=samples.get)
        chosen = next(
            rp for rp in fallback_ranking if rp.candidate_id == best_cid
        )

        # Is this exploratory? (not the fallback's top pick)
        is_exploratory = chosen.candidate_id != fallback_ranking[0].candidate_id

        return probs, is_exploratory, chosen

    # ── Helpers ────────────────────────────────────────────────

    def _should_fallback(self, candidates: list[CandidatePost]) -> bool:
        """Determine if we should fall back to rule-based ranking."""
        if self.total_observations < self.config.min_total_observations:
            return True

        # Check per-arm sparsity: if most arms have too few observations
        observed_arms = 0
        for c in candidates:
            arm = self._arms.get(c.candidate_id)
            if arm and arm.n_pulls >= self.config.min_observations:
                observed_arms += 1

        # Need at least 2 well-observed arms to make bandit useful
        if observed_arms < 2:
            return True

        return False

    def _get_or_create_arm(self, arm_id: str) -> ArmStats:
        if arm_id not in self._arms:
            self._arms[arm_id] = ArmStats(
                arm_id=arm_id,
                alpha=self.config.prior_alpha,
                beta=self.config.prior_beta,
            )
        return self._arms[arm_id]

    @staticmethod
    def _uniform_probabilities(ranking: list[RankedPost]) -> dict[str, float]:
        n = len(ranking)
        if n == 0:
            return {}
        p = 1.0 / n
        return {rp.candidate_id: p for rp in ranking}


# ── Per-tenant bandit management ─────────────────────────────────


@dataclass
class TenantBanditState:
    """Serializable state for a tenant's bandit, for DB persistence."""

    tenant_id: str
    config: dict[str, Any]
    arms: dict[str, dict[str, Any]]
    total_decisions: int = 0
    exploratory_decisions: int = 0
    current_epsilon: float = 0.1
    updated_at: str = ""

    @classmethod
    def from_bandit(cls, tenant_id: str, bandit: ContextualBandit) -> TenantBanditState:
        return cls(
            tenant_id=tenant_id,
            config={
                "policy": bandit.config.policy,
                "epsilon": bandit.config.epsilon,
                "shadow_mode": bandit.config.shadow_mode,
                "enabled": bandit.config.enabled,
                "min_observations": bandit.config.min_observations,
                "min_total_observations": bandit.config.min_total_observations,
            },
            arms={k: v.to_dict() for k, v in bandit._arms.items()},
            total_decisions=bandit._total_decisions,
            exploratory_decisions=bandit._exploratory_decisions,
            current_epsilon=bandit._current_epsilon,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    def to_bandit(self, fallback_ranker: PostRanker | None = None) -> ContextualBandit:
        """Reconstruct a ContextualBandit from persisted state."""
        config = BanditConfig(
            policy=self.config.get("policy", "epsilon_greedy"),
            epsilon=self.config.get("epsilon", 0.1),
            shadow_mode=self.config.get("shadow_mode", True),
            enabled=self.config.get("enabled", True),
            min_observations=self.config.get("min_observations", 20),
            min_total_observations=self.config.get("min_total_observations", 50),
        )

        arm_stats: dict[str, ArmStats] = {}
        for arm_id, data in self.arms.items():
            arm_stats[arm_id] = ArmStats(
                arm_id=arm_id,
                alpha=data.get("alpha", 1.0),
                beta=data.get("beta", 1.0),
                n_pulls=data.get("n_pulls", 0),
                total_reward=data.get("n_pulls", 0) * data.get("mean_reward", 0.0),
            )

        bandit = ContextualBandit(
            fallback_ranker=fallback_ranker,
            config=config,
            arm_stats=arm_stats,
        )
        bandit._current_epsilon = self.current_epsilon
        bandit._total_decisions = self.total_decisions
        bandit._exploratory_decisions = self.exploratory_decisions
        return bandit
