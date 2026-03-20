"""Safety rails — exploration controls, rollback, alerts.

Every tenant has an ExplorationControls config that bounds what the
learning system is allowed to do.  SafetyRails enforces these controls
before any ranking, bandit, or evaluation action proceeds.

Design principles:
- No tenant should be over-experimented on
- Learning must improve content quality, not destabilize campaigns
- Operators can tune autonomy safely
- Every restriction has a clear, auditable reason
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ── Exploration controls ─────────────────────────────────────────


@dataclass
class ExplorationControls:
    """Per-tenant settings that govern learning autonomy.

    Stored in TenantModel.settings["exploration"] and loaded at the
    start of every learning workflow.
    """

    # Master switch
    enabled: bool = True

    # Exploration rate cap (overrides bandit epsilon if lower)
    max_exploration_rate: float = 0.15

    # Confidence threshold — bandit falls back to rule-based when
    # rank correlation with actuals is below this
    confidence_threshold: float = 0.3

    # Minimum sample size before the bandit activates
    min_sample_size: int = 30

    # Quiet hours — no exploration during these hours (UTC)
    quiet_hours_start: int | None = None   # 0-23, e.g. 22
    quiet_hours_end: int | None = None     # 0-23, e.g. 6

    # Weekly experiment budget — max distinct exploratory actions per week
    max_experiments_per_week: int = 50

    # Channel-specific limits — platform → max exploratory posts per week
    channel_limits: dict[str, int] = field(default_factory=lambda: {
        "instagram": 10,
        "tiktok": 10,
        "youtube": 5,
    })

    # Rollback threshold — if mean reward drops by this fraction vs
    # the pre-experiment baseline, auto-rollback triggers
    rollback_threshold: float = 0.20

    # Baseline window — how many recent non-exploratory observations
    # to use when computing the performance baseline
    baseline_window: int = 20

    # Approval gate — require manual approval for experiments when True
    require_approval_for_experiments: bool = False

    # Shadow-only mode — learning runs in parallel but never overrides
    shadow_only: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ExplorationControls:
        """Build from a JSON dict (e.g. from TenantModel.settings)."""
        if not data:
            return cls()
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "max_exploration_rate": self.max_exploration_rate,
            "confidence_threshold": self.confidence_threshold,
            "min_sample_size": self.min_sample_size,
            "quiet_hours_start": self.quiet_hours_start,
            "quiet_hours_end": self.quiet_hours_end,
            "max_experiments_per_week": self.max_experiments_per_week,
            "channel_limits": self.channel_limits,
            "rollback_threshold": self.rollback_threshold,
            "baseline_window": self.baseline_window,
            "require_approval_for_experiments": self.require_approval_for_experiments,
            "shadow_only": self.shadow_only,
        }

    def validate(self) -> list[str]:
        """Return validation errors (empty = valid)."""
        errors = []
        if not 0.0 <= self.max_exploration_rate <= 1.0:
            errors.append(
                f"max_exploration_rate must be in [0, 1], got {self.max_exploration_rate}"
            )
        if not 0.0 <= self.confidence_threshold <= 1.0:
            errors.append(
                f"confidence_threshold must be in [0, 1], got {self.confidence_threshold}"
            )
        if self.min_sample_size < 0:
            errors.append("min_sample_size must be >= 0")
        if self.max_experiments_per_week < 0:
            errors.append("max_experiments_per_week must be >= 0")
        if not 0.0 <= self.rollback_threshold <= 1.0:
            errors.append(
                f"rollback_threshold must be in [0, 1], got {self.rollback_threshold}"
            )
        for qh in (self.quiet_hours_start, self.quiet_hours_end):
            if qh is not None and not 0 <= qh <= 23:
                errors.append(f"quiet_hours must be 0-23, got {qh}")
        for platform, limit in self.channel_limits.items():
            if limit < 0:
                errors.append(f"channel_limits[{platform}] must be >= 0")
        return errors


# ── Safety check results ─────────────────────────────────────────


@dataclass
class SafetyCheckResult:
    """Result of a pre-action safety check."""

    allowed: bool
    reason: str = ""
    force_shadow: bool = False    # override to shadow mode
    force_fallback: bool = False  # override to rule-based ranking
    capped_epsilon: float | None = None  # reduced exploration rate
    alerts: list[Alert] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "force_shadow": self.force_shadow,
            "force_fallback": self.force_fallback,
            "capped_epsilon": self.capped_epsilon,
            "alerts": [a.to_dict() for a in self.alerts],
        }


# ── Alerts ───────────────────────────────────────────────────────


class AlertSeverity(enum.Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Alert:
    """An alert emitted by the safety rails."""

    severity: AlertSeverity
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "details": self.details,
            "timestamp": self.timestamp,
        }


# ── Rollback decision ───────────────────────────────────────────


@dataclass
class RollbackDecision:
    """Result of an automatic rollback evaluation."""

    should_rollback: bool
    reason: str = ""
    baseline_reward: float | None = None
    current_reward: float | None = None
    drop_fraction: float | None = None
    threshold: float | None = None
    alerts: list[Alert] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_rollback": self.should_rollback,
            "reason": self.reason,
            "baseline_reward": self.baseline_reward,
            "current_reward": self.current_reward,
            "drop_fraction": (
                round(self.drop_fraction, 6) if self.drop_fraction is not None else None
            ),
            "threshold": self.threshold,
            "alerts": [a.to_dict() for a in self.alerts],
        }


# ── Safety rails engine ─────────────────────────────────────────


class SafetyRails:
    """Enforces exploration controls across all learning workflows.

    Stateless — takes controls + context, returns a check result.
    The caller is responsible for acting on the result.
    """

    def check_exploration(
        self,
        controls: ExplorationControls,
        *,
        current_hour_utc: int | None = None,
        experiments_this_week: int = 0,
        platform: str = "",
        platform_experiments_this_week: int = 0,
        total_observations: int = 0,
        rank_correlation: float | None = None,
        requested_epsilon: float = 0.1,
        pending_approval: bool = False,
    ) -> SafetyCheckResult:
        """Pre-action safety check for an exploration decision.

        Called before every bandit selection or exploratory ranking.
        Returns whether the action is allowed and any modifications.
        """
        alerts: list[Alert] = []

        # 1. Master switch
        if not controls.enabled:
            return SafetyCheckResult(
                allowed=False,
                reason="Exploration disabled for this tenant",
            )

        # 2. Approval gate
        if controls.require_approval_for_experiments and not pending_approval:
            return SafetyCheckResult(
                allowed=False,
                reason="Experiment requires approval before proceeding",
                alerts=[Alert(
                    severity=AlertSeverity.INFO,
                    code="approval_required",
                    message="Experiment blocked pending operator approval",
                )],
            )

        # 3. Quiet hours
        if current_hour_utc is not None and self._in_quiet_hours(
            current_hour_utc, controls.quiet_hours_start, controls.quiet_hours_end
        ):
            return SafetyCheckResult(
                allowed=True,
                reason="Quiet hours — exploration suppressed",
                force_fallback=True,
                capped_epsilon=0.0,
                alerts=[Alert(
                    severity=AlertSeverity.INFO,
                    code="quiet_hours",
                    message=f"Hour {current_hour_utc} is within quiet hours "
                            f"({controls.quiet_hours_start}-{controls.quiet_hours_end})",
                )],
            )

        # 4. Weekly experiment budget
        if experiments_this_week >= controls.max_experiments_per_week:
            alerts.append(Alert(
                severity=AlertSeverity.WARNING,
                code="weekly_budget_exhausted",
                message=f"Weekly experiment budget exhausted "
                        f"({experiments_this_week}/{controls.max_experiments_per_week})",
                details={"used": experiments_this_week, "limit": controls.max_experiments_per_week},
            ))
            return SafetyCheckResult(
                allowed=True,
                reason="Weekly experiment budget exhausted — falling back to rule-based",
                force_fallback=True,
                capped_epsilon=0.0,
                alerts=alerts,
            )

        # 5. Channel-specific limits
        if platform and platform in controls.channel_limits:
            limit = controls.channel_limits[platform]
            if platform_experiments_this_week >= limit:
                alerts.append(Alert(
                    severity=AlertSeverity.WARNING,
                    code="channel_limit_reached",
                    message=f"{platform} weekly experiment limit reached "
                            f"({platform_experiments_this_week}/{limit})",
                    details={"platform": platform, "used": platform_experiments_this_week, "limit": limit},
                ))
                return SafetyCheckResult(
                    allowed=True,
                    reason=f"{platform} channel limit reached — falling back",
                    force_fallback=True,
                    capped_epsilon=0.0,
                    alerts=alerts,
                )

        # 6. Sparse data — force fallback
        if total_observations < controls.min_sample_size:
            return SafetyCheckResult(
                allowed=True,
                reason=f"Insufficient data ({total_observations}/{controls.min_sample_size}) "
                       f"— using rule-based ranking",
                force_fallback=True,
                capped_epsilon=0.0,
                alerts=[Alert(
                    severity=AlertSeverity.INFO,
                    code="sparse_data",
                    message=f"Only {total_observations} observations "
                            f"(need {controls.min_sample_size})",
                )],
            )

        # 7. Low confidence — force fallback
        if (
            rank_correlation is not None
            and rank_correlation < controls.confidence_threshold
        ):
            alerts.append(Alert(
                severity=AlertSeverity.WARNING,
                code="low_confidence",
                message=f"Rank correlation {rank_correlation:.3f} is below "
                        f"threshold {controls.confidence_threshold}",
                details={
                    "rank_correlation": rank_correlation,
                    "threshold": controls.confidence_threshold,
                },
            ))
            return SafetyCheckResult(
                allowed=True,
                reason="Low confidence — falling back to rule-based ranking",
                force_fallback=True,
                capped_epsilon=0.0,
                alerts=alerts,
            )

        # 8. Shadow-only mode
        force_shadow = controls.shadow_only

        # 9. Cap exploration rate
        capped = min(requested_epsilon, controls.max_exploration_rate)

        # 10. Aggressive exploration alert
        if requested_epsilon > 0.25:
            alerts.append(Alert(
                severity=AlertSeverity.WARNING,
                code="aggressive_exploration",
                message=f"Requested epsilon {requested_epsilon} is high; "
                        f"capped to {capped}",
                details={"requested": requested_epsilon, "capped": capped},
            ))

        return SafetyCheckResult(
            allowed=True,
            reason="Exploration permitted within safety bounds",
            force_shadow=force_shadow,
            capped_epsilon=capped,
            alerts=alerts,
        )

    def evaluate_rollback(
        self,
        controls: ExplorationControls,
        *,
        baseline_rewards: list[float],
        recent_rewards: list[float],
    ) -> RollbackDecision:
        """Check if performance has dropped enough to trigger automatic rollback.

        ``baseline_rewards`` — the last N non-exploratory observations.
        ``recent_rewards`` — the last N observations (exploratory + non).
        """
        if not baseline_rewards or not recent_rewards:
            return RollbackDecision(
                should_rollback=False,
                reason="Insufficient data for rollback evaluation",
            )

        baseline_mean = sum(baseline_rewards) / len(baseline_rewards)
        recent_mean = sum(recent_rewards) / len(recent_rewards)

        if baseline_mean <= 0:
            return RollbackDecision(
                should_rollback=False,
                reason="Baseline reward is zero or negative — cannot compute drop",
                baseline_reward=baseline_mean,
                current_reward=recent_mean,
            )

        drop = (baseline_mean - recent_mean) / baseline_mean
        threshold = controls.rollback_threshold

        alerts: list[Alert] = []

        if drop > threshold:
            alerts.append(Alert(
                severity=AlertSeverity.CRITICAL,
                code="performance_drop_rollback",
                message=f"Performance dropped {drop:.1%} vs baseline "
                        f"(threshold: {threshold:.1%}). Auto-rollback triggered.",
                details={
                    "baseline_mean": round(baseline_mean, 6),
                    "recent_mean": round(recent_mean, 6),
                    "drop_fraction": round(drop, 6),
                },
            ))
            return RollbackDecision(
                should_rollback=True,
                reason=f"Performance dropped {drop:.1%} (threshold: {threshold:.1%})",
                baseline_reward=baseline_mean,
                current_reward=recent_mean,
                drop_fraction=drop,
                threshold=threshold,
                alerts=alerts,
            )

        # Warn if approaching threshold
        if drop > threshold * 0.5:
            alerts.append(Alert(
                severity=AlertSeverity.WARNING,
                code="performance_declining",
                message=f"Performance declining: {drop:.1%} drop "
                        f"(rollback at {threshold:.1%})",
                details={
                    "baseline_mean": round(baseline_mean, 6),
                    "recent_mean": round(recent_mean, 6),
                    "drop_fraction": round(drop, 6),
                },
            ))

        return RollbackDecision(
            should_rollback=False,
            reason="Performance within acceptable bounds",
            baseline_reward=baseline_mean,
            current_reward=recent_mean,
            drop_fraction=drop,
            threshold=threshold,
            alerts=alerts,
        )

    def detect_anomalies(
        self,
        controls: ExplorationControls,
        *,
        experiments_this_week: int = 0,
        exploration_rate_actual: float = 0.0,
        reward_stddev: float | None = None,
        consecutive_exploratory: int = 0,
    ) -> list[Alert]:
        """Detect anomalous behavior in the learning system."""
        alerts: list[Alert] = []

        # Too many experiments (approaching limit)
        budget_usage = (
            experiments_this_week / controls.max_experiments_per_week
            if controls.max_experiments_per_week > 0
            else 0.0
        )
        if budget_usage > 0.8:
            alerts.append(Alert(
                severity=AlertSeverity.WARNING,
                code="budget_nearly_exhausted",
                message=f"Experiment budget {budget_usage:.0%} used "
                        f"({experiments_this_week}/{controls.max_experiments_per_week})",
            ))

        # Actual exploration rate exceeds configured limit
        if exploration_rate_actual > controls.max_exploration_rate * 1.5:
            alerts.append(Alert(
                severity=AlertSeverity.CRITICAL,
                code="exploration_rate_anomaly",
                message=f"Actual exploration rate {exploration_rate_actual:.1%} "
                        f"exceeds {controls.max_exploration_rate:.1%} by >50%",
                details={
                    "actual": exploration_rate_actual,
                    "configured_max": controls.max_exploration_rate,
                },
            ))

        # High reward variance — system may be unstable
        if reward_stddev is not None and reward_stddev > 0.4:
            alerts.append(Alert(
                severity=AlertSeverity.WARNING,
                code="high_reward_variance",
                message=f"Reward standard deviation is {reward_stddev:.3f} "
                        f"— outcomes are highly variable",
                details={"stddev": reward_stddev},
            ))

        # Too many consecutive exploratory decisions
        if consecutive_exploratory > 5:
            alerts.append(Alert(
                severity=AlertSeverity.WARNING,
                code="consecutive_exploration",
                message=f"{consecutive_exploratory} consecutive exploratory decisions "
                        f"— may indicate a stuck exploration loop",
            ))

        return alerts

    # ── Helpers ────────────────────────────────────────────────────

    @staticmethod
    def _in_quiet_hours(
        current_hour: int,
        start: int | None,
        end: int | None,
    ) -> bool:
        """Check if current_hour falls within quiet hours (handles midnight wrap)."""
        if start is None or end is None:
            return False
        if start <= end:
            # e.g. 9-17
            return start <= current_hour < end
        else:
            # Wraps midnight, e.g. 22-6
            return current_hour >= start or current_hour < end
