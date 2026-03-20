"""Reward computation — converts outcome metrics into a single scalar reward.

The reward module is deliberately simple: a weighted sum of normalized
outcome metrics with temporal decay.  No hidden ML — every weight is
in the config, every computation is traceable.

Phase-aware presets optimize for what matters at each campaign stage:
- Pre-release: presaves, email captures, follower growth
- Release day: clicks to streaming, shares, conversion
- Post-release: sustained saves, engagement depth
- Evergreen: follower growth, community engagement
"""

from amplify.learning.rewards.calculator import (
    FORMULA_VERSION,
    PHASE_PRESETS,
    OutcomeData,
    RewardBreakdown,
    RewardCalculator,
    RewardWeights,
    evergreen_weights,
    post_release_weights,
    pre_release_weights,
    release_day_weights,
    weights_for_phase,
)

__all__ = [
    "FORMULA_VERSION",
    "PHASE_PRESETS",
    "OutcomeData",
    "RewardBreakdown",
    "RewardCalculator",
    "RewardWeights",
    "evergreen_weights",
    "post_release_weights",
    "pre_release_weights",
    "release_day_weights",
    "weights_for_phase",
]
