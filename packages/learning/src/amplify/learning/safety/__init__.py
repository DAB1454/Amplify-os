"""Safety rails for the learning system.

Enforces per-tenant exploration limits, automatic rollback on performance
drops, approval gates for high-risk experiments, and anomaly alerts.
"""

from amplify.learning.safety.rails import (
    Alert,
    AlertSeverity,
    ExplorationControls,
    RollbackDecision,
    SafetyCheckResult,
    SafetyRails,
)

__all__ = [
    "Alert",
    "AlertSeverity",
    "ExplorationControls",
    "RollbackDecision",
    "SafetyCheckResult",
    "SafetyRails",
]
