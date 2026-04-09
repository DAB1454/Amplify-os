"""Re-export shim — bandit_service moved to amplify.core.services."""

from amplify.core.services.bandit_service import *  # noqa: F401,F403
from amplify.core.services.bandit_service import (  # noqa: F401
    load_bandit,
    log_decision,
    save_bandit,
)
