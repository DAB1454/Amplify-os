"""Tests for exploration controls and safety rails.

Covers:
- ExplorationControls validation and serialization
- SafetyRails.check_exploration — all enforcement paths
- SafetyRails.evaluate_rollback — threshold enforcement and rollback logic
- SafetyRails.detect_anomalies — anomaly detection
- Quiet hours (including midnight wrap)
- Channel-specific limits
- Approval gate
- Shadow-only enforcement
- Edge cases
"""

from __future__ import annotations

import pytest

from amplify.learning.safety.rails import (
    Alert,
    AlertSeverity,
    ExplorationControls,
    RollbackDecision,
    SafetyCheckResult,
    SafetyRails,
)


# ── Fixtures ─────────────────────────────────────────────────────


def _default_controls(**overrides) -> ExplorationControls:
    defaults = dict(
        enabled=True,
        max_exploration_rate=0.15,
        confidence_threshold=0.3,
        min_sample_size=30,
        max_experiments_per_week=50,
        rollback_threshold=0.20,
        shadow_only=False,
    )
    defaults.update(overrides)
    return ExplorationControls(**defaults)


def _rails() -> SafetyRails:
    return SafetyRails()


# ── ExplorationControls ─────────────────────────────────────────


class TestExplorationControls:
    def test_defaults(self):
        c = ExplorationControls()
        assert c.enabled is True
        assert c.max_exploration_rate == 0.15
        assert c.shadow_only is True

    def test_from_dict(self):
        c = ExplorationControls.from_dict({
            "enabled": False,
            "max_exploration_rate": 0.05,
            "unknown_field": "ignored",
        })
        assert c.enabled is False
        assert c.max_exploration_rate == 0.05

    def test_from_empty_dict(self):
        c = ExplorationControls.from_dict({})
        assert c == ExplorationControls()

    def test_to_dict_roundtrip(self):
        c = _default_controls(quiet_hours_start=22, quiet_hours_end=6)
        d = c.to_dict()
        c2 = ExplorationControls.from_dict(d)
        assert c2.quiet_hours_start == 22
        assert c2.quiet_hours_end == 6
        assert c2.max_exploration_rate == c.max_exploration_rate

    def test_validate_valid(self):
        assert _default_controls().validate() == []

    def test_validate_bad_exploration_rate(self):
        errors = ExplorationControls(max_exploration_rate=1.5).validate()
        assert any("max_exploration_rate" in e for e in errors)

    def test_validate_bad_confidence(self):
        errors = ExplorationControls(confidence_threshold=-0.1).validate()
        assert any("confidence_threshold" in e for e in errors)

    def test_validate_bad_quiet_hours(self):
        errors = ExplorationControls(quiet_hours_start=25).validate()
        assert any("quiet_hours" in e for e in errors)

    def test_validate_bad_channel_limit(self):
        errors = ExplorationControls(channel_limits={"instagram": -1}).validate()
        assert any("channel_limits" in e for e in errors)

    def test_validate_bad_rollback_threshold(self):
        errors = ExplorationControls(rollback_threshold=2.0).validate()
        assert any("rollback_threshold" in e for e in errors)


# ── check_exploration: master switch ─────────────────────────────


class TestMasterSwitch:
    def test_disabled_blocks_all(self):
        result = _rails().check_exploration(
            _default_controls(enabled=False),
        )
        assert result.allowed is False
        assert "disabled" in result.reason.lower()

    def test_enabled_allows(self):
        result = _rails().check_exploration(
            _default_controls(enabled=True),
            total_observations=100,
        )
        assert result.allowed is True


# ── check_exploration: approval gate ─────────────────────────────


class TestApprovalGate:
    def test_requires_approval_blocks(self):
        result = _rails().check_exploration(
            _default_controls(require_approval_for_experiments=True),
            pending_approval=False,
            total_observations=100,
        )
        assert result.allowed is False
        assert "approval" in result.reason.lower()

    def test_approved_allows(self):
        result = _rails().check_exploration(
            _default_controls(require_approval_for_experiments=True),
            pending_approval=True,
            total_observations=100,
        )
        assert result.allowed is True

    def test_no_gate_no_block(self):
        result = _rails().check_exploration(
            _default_controls(require_approval_for_experiments=False),
            pending_approval=False,
            total_observations=100,
        )
        assert result.allowed is True


# ── check_exploration: quiet hours ───────────────────────────────


class TestQuietHours:
    def test_within_quiet_hours_forces_fallback(self):
        result = _rails().check_exploration(
            _default_controls(quiet_hours_start=22, quiet_hours_end=6),
            current_hour_utc=23,
            total_observations=100,
        )
        assert result.allowed is True
        assert result.force_fallback is True
        assert result.capped_epsilon == 0.0

    def test_outside_quiet_hours_no_fallback(self):
        result = _rails().check_exploration(
            _default_controls(quiet_hours_start=22, quiet_hours_end=6),
            current_hour_utc=14,
            total_observations=100,
        )
        assert result.force_fallback is False

    def test_midnight_wrap_before_midnight(self):
        result = _rails().check_exploration(
            _default_controls(quiet_hours_start=22, quiet_hours_end=6),
            current_hour_utc=22,
            total_observations=100,
        )
        assert result.force_fallback is True

    def test_midnight_wrap_after_midnight(self):
        result = _rails().check_exploration(
            _default_controls(quiet_hours_start=22, quiet_hours_end=6),
            current_hour_utc=3,
            total_observations=100,
        )
        assert result.force_fallback is True

    def test_midnight_wrap_boundary_end(self):
        """Hour 6 is NOT within quiet hours 22-6."""
        result = _rails().check_exploration(
            _default_controls(quiet_hours_start=22, quiet_hours_end=6),
            current_hour_utc=6,
            total_observations=100,
        )
        assert result.force_fallback is False

    def test_same_day_range(self):
        result = _rails().check_exploration(
            _default_controls(quiet_hours_start=9, quiet_hours_end=17),
            current_hour_utc=12,
            total_observations=100,
        )
        assert result.force_fallback is True

    def test_no_quiet_hours(self):
        result = _rails().check_exploration(
            _default_controls(quiet_hours_start=None, quiet_hours_end=None),
            current_hour_utc=3,
            total_observations=100,
        )
        assert result.force_fallback is False


# ── check_exploration: weekly budget ─────────────────────────────


class TestWeeklyBudget:
    def test_budget_exhausted(self):
        result = _rails().check_exploration(
            _default_controls(max_experiments_per_week=10),
            experiments_this_week=10,
            total_observations=100,
        )
        assert result.force_fallback is True
        assert result.capped_epsilon == 0.0
        assert any(a.code == "weekly_budget_exhausted" for a in result.alerts)

    def test_budget_remaining(self):
        result = _rails().check_exploration(
            _default_controls(max_experiments_per_week=50),
            experiments_this_week=5,
            total_observations=100,
        )
        assert result.force_fallback is False


# ── check_exploration: channel limits ────────────────────────────


class TestChannelLimits:
    def test_channel_limit_reached(self):
        result = _rails().check_exploration(
            _default_controls(channel_limits={"instagram": 5}),
            platform="instagram",
            platform_experiments_this_week=5,
            total_observations=100,
        )
        assert result.force_fallback is True
        assert any(a.code == "channel_limit_reached" for a in result.alerts)

    def test_channel_limit_not_reached(self):
        result = _rails().check_exploration(
            _default_controls(channel_limits={"instagram": 10}),
            platform="instagram",
            platform_experiments_this_week=3,
            total_observations=100,
        )
        assert result.force_fallback is False

    def test_unknown_platform_no_limit(self):
        result = _rails().check_exploration(
            _default_controls(channel_limits={"instagram": 5}),
            platform="twitter",
            platform_experiments_this_week=100,
            total_observations=100,
        )
        assert result.force_fallback is False


# ── check_exploration: sparse data ───────────────────────────────


class TestSparseData:
    def test_insufficient_observations(self):
        result = _rails().check_exploration(
            _default_controls(min_sample_size=30),
            total_observations=10,
        )
        assert result.force_fallback is True
        assert any(a.code == "sparse_data" for a in result.alerts)

    def test_sufficient_observations(self):
        result = _rails().check_exploration(
            _default_controls(min_sample_size=30),
            total_observations=50,
        )
        assert result.force_fallback is False


# ── check_exploration: confidence threshold ──────────────────────


class TestConfidenceThreshold:
    def test_low_confidence_forces_fallback(self):
        result = _rails().check_exploration(
            _default_controls(confidence_threshold=0.3),
            rank_correlation=0.1,
            total_observations=100,
        )
        assert result.force_fallback is True
        assert any(a.code == "low_confidence" for a in result.alerts)

    def test_high_confidence_allows(self):
        result = _rails().check_exploration(
            _default_controls(confidence_threshold=0.3),
            rank_correlation=0.5,
            total_observations=100,
        )
        assert result.force_fallback is False

    def test_no_correlation_allows(self):
        """When rank_correlation is None, don't block."""
        result = _rails().check_exploration(
            _default_controls(confidence_threshold=0.3),
            rank_correlation=None,
            total_observations=100,
        )
        assert result.force_fallback is False


# ── check_exploration: epsilon capping ───────────────────────────


class TestEpsilonCapping:
    def test_epsilon_capped_to_max(self):
        result = _rails().check_exploration(
            _default_controls(max_exploration_rate=0.1),
            requested_epsilon=0.5,
            total_observations=100,
        )
        assert result.capped_epsilon == 0.1

    def test_epsilon_below_max_unchanged(self):
        result = _rails().check_exploration(
            _default_controls(max_exploration_rate=0.15),
            requested_epsilon=0.05,
            total_observations=100,
        )
        assert result.capped_epsilon == 0.05

    def test_aggressive_exploration_alert(self):
        result = _rails().check_exploration(
            _default_controls(max_exploration_rate=0.15),
            requested_epsilon=0.5,
            total_observations=100,
        )
        assert any(a.code == "aggressive_exploration" for a in result.alerts)


# ── check_exploration: shadow-only ───────────────────────────────


class TestShadowOnly:
    def test_shadow_only_forces_shadow(self):
        result = _rails().check_exploration(
            _default_controls(shadow_only=True),
            total_observations=100,
        )
        assert result.force_shadow is True

    def test_non_shadow_allows_live(self):
        result = _rails().check_exploration(
            _default_controls(shadow_only=False),
            total_observations=100,
        )
        assert result.force_shadow is False


# ── evaluate_rollback ────────────────────────────────────────────


class TestRollbackEvaluation:
    def test_performance_drop_triggers_rollback(self):
        decision = _rails().evaluate_rollback(
            _default_controls(rollback_threshold=0.20),
            baseline_rewards=[0.8, 0.7, 0.9, 0.75, 0.85],
            recent_rewards=[0.4, 0.5, 0.3, 0.45, 0.35],
        )
        assert decision.should_rollback is True
        assert decision.drop_fraction > 0.20
        assert any(a.code == "performance_drop_rollback" for a in decision.alerts)

    def test_no_drop_no_rollback(self):
        decision = _rails().evaluate_rollback(
            _default_controls(rollback_threshold=0.20),
            baseline_rewards=[0.6, 0.7, 0.65],
            recent_rewards=[0.65, 0.7, 0.68],
        )
        assert decision.should_rollback is False

    def test_small_drop_warns(self):
        """Drop > 50% of threshold should trigger a warning."""
        decision = _rails().evaluate_rollback(
            _default_controls(rollback_threshold=0.20),
            baseline_rewards=[0.8, 0.8, 0.8, 0.8],
            recent_rewards=[0.7, 0.7, 0.7, 0.7],  # 12.5% drop
        )
        assert decision.should_rollback is False
        assert any(a.code == "performance_declining" for a in decision.alerts)

    def test_empty_baseline_no_rollback(self):
        decision = _rails().evaluate_rollback(
            _default_controls(),
            baseline_rewards=[],
            recent_rewards=[0.5, 0.6],
        )
        assert decision.should_rollback is False

    def test_zero_baseline_no_rollback(self):
        decision = _rails().evaluate_rollback(
            _default_controls(),
            baseline_rewards=[0.0, 0.0],
            recent_rewards=[0.5, 0.6],
        )
        assert decision.should_rollback is False

    def test_improvement_no_rollback(self):
        """If performance improved, drop is negative — no rollback."""
        decision = _rails().evaluate_rollback(
            _default_controls(rollback_threshold=0.20),
            baseline_rewards=[0.5, 0.5],
            recent_rewards=[0.8, 0.9],
        )
        assert decision.should_rollback is False
        assert decision.drop_fraction < 0

    def test_rollback_records_metrics(self):
        decision = _rails().evaluate_rollback(
            _default_controls(rollback_threshold=0.10),
            baseline_rewards=[0.8, 0.8],
            recent_rewards=[0.4, 0.4],
        )
        assert decision.baseline_reward == 0.8
        assert decision.current_reward == 0.4
        assert decision.threshold == 0.10

    def test_exact_threshold_no_rollback(self):
        """Drop exactly at threshold should NOT trigger (strictly greater)."""
        decision = _rails().evaluate_rollback(
            _default_controls(rollback_threshold=0.20),
            baseline_rewards=[1.0, 1.0],
            recent_rewards=[0.8, 0.8],
        )
        assert decision.should_rollback is False


# ── detect_anomalies ─────────────────────────────────────────────


class TestAnomalyDetection:
    def test_budget_nearly_exhausted(self):
        alerts = _rails().detect_anomalies(
            _default_controls(max_experiments_per_week=10),
            experiments_this_week=9,
        )
        assert any(a.code == "budget_nearly_exhausted" for a in alerts)

    def test_exploration_rate_anomaly(self):
        alerts = _rails().detect_anomalies(
            _default_controls(max_exploration_rate=0.1),
            exploration_rate_actual=0.25,
        )
        assert any(a.code == "exploration_rate_anomaly" for a in alerts)

    def test_high_reward_variance(self):
        alerts = _rails().detect_anomalies(
            _default_controls(),
            reward_stddev=0.5,
        )
        assert any(a.code == "high_reward_variance" for a in alerts)

    def test_consecutive_exploration(self):
        alerts = _rails().detect_anomalies(
            _default_controls(),
            consecutive_exploratory=8,
        )
        assert any(a.code == "consecutive_exploration" for a in alerts)

    def test_no_anomalies(self):
        alerts = _rails().detect_anomalies(
            _default_controls(max_experiments_per_week=50),
            experiments_this_week=5,
            exploration_rate_actual=0.05,
            reward_stddev=0.1,
        )
        assert alerts == []


# ── Alert structure ──────────────────────────────────────────────


class TestAlert:
    def test_to_dict(self):
        a = Alert(
            severity=AlertSeverity.WARNING,
            code="test_alert",
            message="Something happened",
            details={"key": "value"},
        )
        d = a.to_dict()
        assert d["severity"] == "warning"
        assert d["code"] == "test_alert"
        assert d["timestamp"] != ""

    def test_severity_levels(self):
        assert AlertSeverity.INFO.value == "info"
        assert AlertSeverity.WARNING.value == "warning"
        assert AlertSeverity.CRITICAL.value == "critical"


# ── SafetyCheckResult ────────────────────────────────────────────


class TestSafetyCheckResult:
    def test_to_dict(self):
        result = SafetyCheckResult(
            allowed=True,
            reason="OK",
            force_shadow=True,
            capped_epsilon=0.05,
            alerts=[Alert(
                severity=AlertSeverity.INFO,
                code="test",
                message="test",
            )],
        )
        d = result.to_dict()
        assert d["allowed"] is True
        assert d["force_shadow"] is True
        assert len(d["alerts"]) == 1


# ── Priority ordering ───────────────────────────────────────────


class TestCheckPriority:
    """Checks are evaluated in priority order. Earlier checks take precedence."""

    def test_disabled_beats_quiet_hours(self):
        result = _rails().check_exploration(
            _default_controls(
                enabled=False,
                quiet_hours_start=22,
                quiet_hours_end=6,
            ),
            current_hour_utc=23,
        )
        assert result.allowed is False
        assert "disabled" in result.reason.lower()

    def test_approval_beats_budget(self):
        result = _rails().check_exploration(
            _default_controls(require_approval_for_experiments=True),
            pending_approval=False,
            experiments_this_week=100,
            total_observations=100,
        )
        assert result.allowed is False
        assert "approval" in result.reason.lower()

    def test_quiet_hours_beats_budget(self):
        result = _rails().check_exploration(
            _default_controls(
                quiet_hours_start=22,
                quiet_hours_end=6,
                max_experiments_per_week=50,
            ),
            current_hour_utc=23,
            experiments_this_week=100,
            total_observations=100,
        )
        assert "quiet" in result.reason.lower()

    def test_budget_beats_sparse_data(self):
        result = _rails().check_exploration(
            _default_controls(max_experiments_per_week=10),
            experiments_this_week=10,
            total_observations=5,
        )
        assert "budget" in result.reason.lower()
