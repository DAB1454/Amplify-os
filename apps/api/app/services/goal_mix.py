"""Re-export shim — goal_mix moved to amplify.core.services."""

from amplify.core.services.goal_mix import *  # noqa: F401,F403
from amplify.core.services.goal_mix import (  # noqa: F401
    default_goal_mix,
    normalize_mix,
    rebalance_goals,
)
