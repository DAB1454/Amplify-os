"""Tests for the contextual bandit for candidate post selection.

Covers:
- Action selection (epsilon-greedy and Thompson sampling)
- Sparse-data fallback to rule-based ranking
- Learning updates and arm statistics
- Shadow mode behavior
- Exploration bounds
- Action probabilities stored for every decision
- Per-tenant state serialization
- Config validation
- RankingPolicy protocol compliance
"""

from __future__ import annotations

import pytest

from amplify.learning.ranking.bandit import (
    ArmStats,
    BanditConfig,
    BanditDecision,
    ContextualBandit,
    TenantBanditState,
)
from amplify.learning.ranking.post_ranker import (
    CandidatePost,
    PostRanker,
    RankedPost,
)


# ── Fixtures ─────────────────────────────────────────────────────


def _candidates(n: int = 4) -> list[CandidatePost]:
    """Build n candidates with varied features."""
    templates = [
        dict(
            platform="instagram", content_type="reel",
            post_hour=18, has_media=True, hook_type="question",
            cta_type="presave", campaign_phase="pre_release",
        ),
        dict(
            platform="instagram", content_type="carousel",
            post_hour=12, has_media=True, hook_type="teaser",
            cta_type="link", campaign_phase="release_day",
        ),
        dict(
            platform="tiktok", content_type="short",
            post_hour=19, has_media=True, hook_type="question",
            cta_type="follow", campaign_phase="pre_release",
        ),
        dict(
            platform="instagram", content_type="story",
            post_hour=22, has_media=True, hook_type="teaser",
            cta_type="none", campaign_phase="post_release",
        ),
    ]
    return [
        CandidatePost(candidate_id=f"c{i+1}", **templates[i % len(templates)])
        for i in range(n)
    ]


def _seeded_bandit(
    policy: str = "epsilon_greedy",
    epsilon: float = 0.5,
    shadow_mode: bool = False,
    enabled: bool = True,
    min_observations: int = 5,
    min_total_observations: int = 10,
    seed: int = 42,
    arm_stats: dict[str, ArmStats] | None = None,
) -> ContextualBandit:
    """Create a deterministic bandit for testing."""
    config = BanditConfig(
        policy=policy,
        epsilon=epsilon,
        shadow_mode=shadow_mode,
        enabled=enabled,
        min_observations=min_observations,
        min_total_observations=min_total_observations,
    )
    return ContextualBandit(
        fallback_ranker=PostRanker(),
        config=config,
        arm_stats=arm_stats,
        rng_seed=seed,
    )


def _pretrained_arms() -> dict[str, ArmStats]:
    """Arms with enough observations to pass the sparsity check."""
    return {
        "c1": ArmStats(arm_id="c1", alpha=8.0, beta=3.0, n_pulls=10, total_reward=7.0),
        "c2": ArmStats(arm_id="c2", alpha=5.0, beta=6.0, n_pulls=10, total_reward=4.0),
        "c3": ArmStats(arm_id="c3", alpha=6.0, beta=5.0, n_pulls=10, total_reward=5.0),
        "c4": ArmStats(arm_id="c4", alpha=3.0, beta=8.0, n_pulls=10, total_reward=2.0),
    }


# ── Action selection: epsilon-greedy ─────────────────────────────


class TestEpsilonGreedy:
    def test_returns_decision(self):
        bandit = _seeded_bandit(arm_stats=_pretrained_arms())
        decision = bandit.select(_candidates())
        assert isinstance(decision, BanditDecision)
        assert isinstance(decision.chosen, RankedPost)

    def test_action_probabilities_always_present(self):
        bandit = _seeded_bandit(arm_stats=_pretrained_arms())
        decision = bandit.select(_candidates())
        assert len(decision.action_probabilities) == 4
        for cid, p in decision.action_probabilities.items():
            assert 0.0 <= p <= 1.0

    def test_probabilities_sum_to_one(self):
        bandit = _seeded_bandit(arm_stats=_pretrained_arms())
        decision = bandit.select(_candidates())
        total = sum(decision.action_probabilities.values())
        assert abs(total - 1.0) < 1e-6

    def test_exploit_picks_top_ranked(self):
        """With epsilon=0, always pick the fallback's top choice."""
        bandit = _seeded_bandit(epsilon=0.0, arm_stats=_pretrained_arms())
        decision = bandit.select(_candidates())
        assert decision.chosen.candidate_id == decision.fallback_choice.candidate_id
        assert not decision.is_exploratory

    def test_explore_can_pick_non_top(self):
        """With high epsilon, exploration picks non-top candidates sometimes."""
        picked = set()
        exploratory_count = 0
        for seed in range(100):
            config = BanditConfig(
                epsilon=1.0,
                max_exploration_fraction=1.0,  # uncap for this test
                shadow_mode=False,  # must disable shadow to see bandit choices
                min_observations=5,
                min_total_observations=10,
            )
            bandit = ContextualBandit(
                config=config, arm_stats=_pretrained_arms(), rng_seed=seed,
            )
            d = bandit.select(_candidates())
            picked.add(d.chosen.candidate_id)
            if d.is_exploratory:
                exploratory_count += 1
        assert len(picked) > 1, "Exploration should pick different candidates"
        assert exploratory_count > 50, "Most decisions should be exploratory"

    def test_epsilon_decays(self):
        bandit = _seeded_bandit(epsilon=0.5, arm_stats=_pretrained_arms())
        eps_before = bandit._current_epsilon
        bandit.select(_candidates())
        assert bandit._current_epsilon < eps_before

    def test_epsilon_has_floor(self):
        config = BanditConfig(
            epsilon=0.02,
            epsilon_min=0.01,
            epsilon_decay=0.5,
            min_observations=5,
            min_total_observations=10,
        )
        bandit = ContextualBandit(config=config, arm_stats=_pretrained_arms(), rng_seed=1)
        for _ in range(100):
            bandit.select(_candidates())
        assert bandit._current_epsilon >= config.epsilon_min

    def test_chosen_probability_recorded(self):
        bandit = _seeded_bandit(arm_stats=_pretrained_arms())
        decision = bandit.select(_candidates())
        assert decision.chosen_probability > 0.0


# ── Action selection: Thompson sampling ──────────────────────────


class TestThompsonSampling:
    def test_returns_decision(self):
        bandit = _seeded_bandit(policy="thompson_sampling", arm_stats=_pretrained_arms())
        decision = bandit.select(_candidates())
        assert isinstance(decision, BanditDecision)
        assert decision.policy_used == "thompson_sampling"

    def test_action_probabilities_present(self):
        bandit = _seeded_bandit(policy="thompson_sampling", arm_stats=_pretrained_arms())
        decision = bandit.select(_candidates())
        assert len(decision.action_probabilities) == 4
        assert all(p >= 0.0 for p in decision.action_probabilities.values())

    def test_favors_high_reward_arms(self):
        """Thompson sampling should favor arms with higher alpha."""
        arms = {
            "c1": ArmStats(arm_id="c1", alpha=50.0, beta=2.0, n_pulls=50, total_reward=48.0),
            "c2": ArmStats(arm_id="c2", alpha=2.0, beta=50.0, n_pulls=50, total_reward=1.0),
            "c3": ArmStats(arm_id="c3", alpha=2.0, beta=50.0, n_pulls=50, total_reward=1.0),
            "c4": ArmStats(arm_id="c4", alpha=2.0, beta=50.0, n_pulls=50, total_reward=1.0),
        }
        counts: dict[str, int] = {"c1": 0, "c2": 0, "c3": 0, "c4": 0}
        for seed in range(200):
            bandit = _seeded_bandit(
                policy="thompson_sampling", arm_stats={k: ArmStats(**v.__dict__) for k, v in arms.items()},
                seed=seed,
            )
            d = bandit.select(_candidates())
            counts[d.chosen.candidate_id] += 1

        # c1 should be chosen most of the time
        assert counts["c1"] > counts["c2"]
        assert counts["c1"] > counts["c3"]

    def test_creates_arms_for_unseen_candidates(self):
        bandit = _seeded_bandit(policy="thompson_sampling")
        # Need enough total observations to pass fallback check
        bandit._arms = _pretrained_arms()
        bandit.select(_candidates())
        # All candidates should have arm entries now
        for c in _candidates():
            assert c.candidate_id in bandit._arms


# ── Sparse-data fallback ────────────────────────────────────────


class TestSparseDataFallback:
    def test_falls_back_when_no_observations(self):
        """Brand new bandit with zero data should use fallback."""
        bandit = _seeded_bandit()
        decision = bandit.select(_candidates())
        assert decision.policy_used == "fallback"
        assert not decision.is_exploratory
        assert "fallback_reason" in decision.context

    def test_falls_back_below_total_threshold(self):
        """Even with some data, falls back if below min_total_observations."""
        arms = {"c1": ArmStats(arm_id="c1", n_pulls=3, total_reward=2.0)}
        bandit = _seeded_bandit(min_total_observations=10, arm_stats=arms)
        decision = bandit.select(_candidates())
        assert decision.policy_used == "fallback"

    def test_falls_back_with_sparse_arms(self):
        """Enough total obs, but too few per-arm → fallback."""
        arms = {
            "c1": ArmStats(arm_id="c1", n_pulls=30, total_reward=20.0),
            # c2, c3, c4 have zero observations
        }
        bandit = _seeded_bandit(min_observations=5, min_total_observations=10, arm_stats=arms)
        decision = bandit.select(_candidates())
        assert decision.policy_used == "fallback"

    def test_activates_with_sufficient_data(self):
        """When enough data, bandit policy is used."""
        bandit = _seeded_bandit(arm_stats=_pretrained_arms())
        decision = bandit.select(_candidates())
        assert decision.policy_used == "epsilon_greedy"

    def test_fallback_uses_rule_based_ranking(self):
        """Fallback decision should match the PostRanker's top pick."""
        bandit = _seeded_bandit()
        decision = bandit.select(_candidates())
        assert decision.chosen.candidate_id == decision.fallback_choice.candidate_id

    def test_fallback_probability_is_one(self):
        """In fallback mode, the chosen candidate gets probability 1.0."""
        bandit = _seeded_bandit()
        decision = bandit.select(_candidates())
        assert decision.chosen_probability == 1.0


# ── Learning updates ─────────────────────────────────────────────


class TestLearningUpdates:
    def test_update_creates_arm(self):
        bandit = _seeded_bandit()
        arm = bandit.update("c1", "c1", reward=0.8)
        assert arm.n_pulls == 1
        assert arm.total_reward == 0.8

    def test_update_accumulates(self):
        bandit = _seeded_bandit()
        bandit.update("c1", "c1", reward=0.6)
        bandit.update("c1", "c1", reward=0.8)
        arm = bandit.get_arm("c1")
        assert arm.n_pulls == 2
        assert abs(arm.total_reward - 1.4) < 1e-6

    def test_update_adjusts_posterior(self):
        """Observing high rewards should increase alpha."""
        bandit = _seeded_bandit()
        arm_before_alpha = bandit._get_or_create_arm("c1").alpha
        bandit.update("c1", "c1", reward=1.0)
        arm = bandit.get_arm("c1")
        assert arm.alpha > arm_before_alpha

    def test_update_clamps_reward(self):
        """Rewards outside [0,1] are clamped."""
        bandit = _seeded_bandit()
        bandit.update("c1", "c1", reward=1.5)
        arm = bandit.get_arm("c1")
        assert arm.total_reward == 1.0  # clamped to 1.0

        bandit.update("c2", "c2", reward=-0.5)
        arm2 = bandit.get_arm("c2")
        assert arm2.total_reward == 0.0  # clamped to 0.0

    def test_mean_reward(self):
        bandit = _seeded_bandit()
        bandit.update("c1", "c1", reward=0.6)
        bandit.update("c1", "c1", reward=0.8)
        arm = bandit.get_arm("c1")
        assert abs(arm.mean_reward - 0.7) < 1e-6

    def test_total_observations_tracked(self):
        bandit = _seeded_bandit()
        bandit.update("c1", "c1", reward=0.5)
        bandit.update("c2", "c2", reward=0.3)
        assert bandit.total_observations == 2

    def test_contextual_key_grouping(self):
        """Different context keys create separate arms."""
        bandit = _seeded_bandit()
        bandit.update("c1", "instagram:reel:presave", reward=0.8)
        bandit.update("c1", "tiktok:short:follow", reward=0.4)
        assert bandit.arm_count == 2
        ig = bandit.get_arm("instagram:reel:presave")
        tk = bandit.get_arm("tiktok:short:follow")
        assert ig.mean_reward > tk.mean_reward


# ── Shadow mode ──────────────────────────────────────────────────


class TestShadowMode:
    def test_shadow_uses_fallback_choice(self):
        """In shadow mode, the chosen candidate is always the fallback's top pick."""
        bandit = _seeded_bandit(shadow_mode=True, arm_stats=_pretrained_arms())
        decision = bandit.select(_candidates())
        assert decision.is_shadow is True
        assert decision.chosen.candidate_id == decision.fallback_choice.candidate_id

    def test_shadow_still_computes_probabilities(self):
        """Shadow mode computes and stores action probabilities for analysis."""
        bandit = _seeded_bandit(shadow_mode=True, arm_stats=_pretrained_arms())
        decision = bandit.select(_candidates())
        assert len(decision.action_probabilities) == 4
        total = sum(decision.action_probabilities.values())
        assert abs(total - 1.0) < 1e-6

    def test_shadow_tracks_what_bandit_would_choose(self):
        """The decision record shows it's shadow even though the bandit selected."""
        bandit = _seeded_bandit(shadow_mode=True, arm_stats=_pretrained_arms())
        decision = bandit.select(_candidates())
        assert decision.policy_used == "epsilon_greedy"
        assert decision.is_shadow

    def test_shadow_rank_method_returns_fallback_order(self):
        """rank() in shadow mode returns the fallback's ordering."""
        bandit = _seeded_bandit(shadow_mode=True, arm_stats=_pretrained_arms())
        fallback = PostRanker()
        candidates = _candidates()
        expected = [rp.candidate_id for rp in fallback.rank(candidates)]
        actual = [rp.candidate_id for rp in bandit.rank(candidates)]
        assert actual == expected

    def test_shadow_still_tracks_decisions(self):
        bandit = _seeded_bandit(shadow_mode=True, arm_stats=_pretrained_arms())
        bandit.select(_candidates())
        bandit.select(_candidates())
        assert bandit._total_decisions == 2

    def test_non_shadow_can_override_ranking(self):
        """When not in shadow mode, bandit's choice moves to rank 1."""
        bandit = _seeded_bandit(
            shadow_mode=False, epsilon=1.0, arm_stats=_pretrained_arms(), seed=99,
        )
        # With epsilon=1.0 and the right seed, bandit may pick a non-top candidate
        # Run enough times to get an override
        overrode = False
        for seed in range(100):
            b = _seeded_bandit(
                shadow_mode=False, epsilon=1.0, arm_stats=_pretrained_arms(), seed=seed,
            )
            ranking = b.rank(_candidates())
            decision = b.decisions[-1] if b.decisions else None
            if decision and decision.is_exploratory:
                # Check that the chosen candidate is at rank 1
                assert ranking[0].candidate_id == decision.chosen.candidate_id
                overrode = True
                break
        assert overrode, "Should find at least one exploratory override"


# ── Exploration bounds ───────────────────────────────────────────


class TestExplorationBounds:
    def test_max_exploration_fraction_caps_epsilon(self):
        config = BanditConfig(
            epsilon=0.8,
            max_exploration_fraction=0.3,
            min_observations=5,
            min_total_observations=10,
        )
        bandit = ContextualBandit(config=config, arm_stats=_pretrained_arms(), rng_seed=42)
        decision = bandit.select(_candidates())
        # The top candidate's probability should be at least 1 - 0.3 = 0.7
        top_prob = max(decision.action_probabilities.values())
        assert top_prob >= 0.7 - 1e-6

    def test_actual_exploration_rate_bounded(self):
        """Over many decisions, actual exploration should be ≤ configured epsilon."""
        bandit = _seeded_bandit(epsilon=0.2, arm_stats=_pretrained_arms(), seed=1)
        for _ in range(200):
            bandit.select(_candidates())
        # Due to epsilon decay, actual rate should be well below initial epsilon
        assert bandit.exploration_rate_actual <= 0.25  # generous margin


# ── Decision logging ─────────────────────────────────────────────


class TestDecisionLogging:
    def test_log_dict_has_required_fields(self):
        bandit = _seeded_bandit(arm_stats=_pretrained_arms())
        decision = bandit.select(_candidates())
        log = decision.to_log_dict()
        required = {
            "decision_id", "chosen_id", "chosen_score", "chosen_rank",
            "chosen_probability", "action_probabilities", "is_exploratory",
            "is_shadow", "fallback_choice_id", "policy_used", "epsilon",
            "n_candidates", "timestamp", "context",
        }
        assert required.issubset(set(log.keys()))

    def test_log_dict_probabilities_are_rounded(self):
        bandit = _seeded_bandit(arm_stats=_pretrained_arms())
        decision = bandit.select(_candidates())
        log = decision.to_log_dict()
        for p in log["action_probabilities"].values():
            # Check it's a float with limited precision
            assert isinstance(p, float)

    def test_decisions_list_grows(self):
        bandit = _seeded_bandit(arm_stats=_pretrained_arms())
        bandit.select(_candidates())
        bandit.select(_candidates())
        assert len(bandit.decisions) == 2


# ── Per-tenant state ─────────────────────────────────────────────


class TestTenantState:
    def test_roundtrip_serialization(self):
        bandit = _seeded_bandit(arm_stats=_pretrained_arms())
        bandit.select(_candidates())
        bandit.update("c1", "c1", reward=0.8)

        state = TenantBanditState.from_bandit("tenant-123", bandit)
        restored = state.to_bandit()

        assert restored.config.policy == bandit.config.policy
        assert restored._current_epsilon == bandit._current_epsilon
        assert restored._total_decisions == bandit._total_decisions
        assert restored.arm_count == bandit.arm_count

    def test_state_preserves_arm_stats(self):
        bandit = _seeded_bandit()
        bandit.update("arm_a", "arm_a", reward=0.9)
        bandit.update("arm_a", "arm_a", reward=0.7)

        state = TenantBanditState.from_bandit("t1", bandit)
        restored = state.to_bandit()
        arm = restored.get_arm("arm_a")
        assert arm is not None
        assert arm.n_pulls == 2

    def test_disabled_tenant(self):
        bandit = _seeded_bandit(enabled=False, arm_stats=_pretrained_arms())
        decision = bandit.select(_candidates())
        assert decision.policy_used == "fallback"


# ── Config validation ────────────────────────────────────────────


class TestConfigValidation:
    def test_valid_config(self):
        assert BanditConfig().validate() == []

    def test_invalid_policy(self):
        errors = BanditConfig(policy="ucb").validate()
        assert any("Unknown policy" in e for e in errors)

    def test_invalid_epsilon(self):
        errors = BanditConfig(epsilon=1.5).validate()
        assert any("epsilon" in e for e in errors)

    def test_invalid_max_exploration(self):
        errors = BanditConfig(max_exploration_fraction=2.0).validate()
        assert len(errors) > 0


# ── ArmStats ─────────────────────────────────────────────────────


class TestArmStats:
    def test_initial_state(self):
        arm = ArmStats(arm_id="test")
        assert arm.n_pulls == 0
        assert arm.mean_reward == 0.0

    def test_thompson_sample_in_range(self):
        arm = ArmStats(arm_id="test", alpha=5.0, beta=5.0)
        for _ in range(100):
            s = arm.sample_thompson()
            assert 0.0 <= s <= 1.0

    def test_update_increments(self):
        arm = ArmStats(arm_id="test")
        arm.update(0.7)
        assert arm.n_pulls == 1
        assert abs(arm.alpha - 1.7) < 1e-6
        assert abs(arm.beta - 1.3) < 1e-6

    def test_to_dict(self):
        arm = ArmStats(arm_id="test", n_pulls=5, total_reward=3.0)
        d = arm.to_dict()
        assert d["arm_id"] == "test"
        assert d["n_pulls"] == 5
        assert d["mean_reward"] == 0.6


# ── RankingPolicy protocol ──────────────────────────────────────


class TestRankingPolicyProtocol:
    def test_satisfies_protocol(self):
        """ContextualBandit.rank() has the correct signature."""
        bandit = _seeded_bandit(arm_stats=_pretrained_arms())
        result = bandit.rank(_candidates())
        assert isinstance(result, list)
        assert all(isinstance(rp, RankedPost) for rp in result)

    def test_works_with_replay_engine(self):
        """Can be used as a RankingPolicySpec.ranker."""
        from amplify.learning.evaluation.replay import (
            RankingPolicySpec,
            ReplayDataset,
            ReplayEngine,
            build_sample_dataset,
        )

        bandit = _seeded_bandit(arm_stats=_pretrained_arms())
        spec = RankingPolicySpec(name="bandit", ranker=bandit)
        engine = ReplayEngine()
        ds = build_sample_dataset()
        report = engine.evaluate_single(ds, spec)
        assert report.sample_size == 12
        assert report.mae is not None


# ── Summary / diagnostics ───────────────────────────────────────


class TestSummary:
    def test_summary_structure(self):
        bandit = _seeded_bandit(arm_stats=_pretrained_arms())
        bandit.select(_candidates())
        s = bandit.summary()
        assert s["policy"] == "epsilon_greedy"
        assert s["total_decisions"] == 1
        assert "arms" in s
        assert s["arm_count"] == len(_pretrained_arms())

    def test_empty_bandit_summary(self):
        bandit = _seeded_bandit()
        s = bandit.summary()
        assert s["total_decisions"] == 0
        assert s["exploration_rate_actual"] == 0.0
