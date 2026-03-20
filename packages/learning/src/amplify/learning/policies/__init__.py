"""Learning-informed policy rules.

Bridges the learning system with the existing policy engine
(amplify.core.policies).  Provides PolicyRule implementations that
use learned insights and rankings to inform action decisions.

These rules are advisory — they produce ALLOW or REQUIRE_APPROVAL
verdicts, never BLOCK.  The policy engine aggregates them with
other rules to reach a final decision.
"""

from amplify.learning.policies.rules import (
    TimingAdvisoryRule,
    ContentTypeAdvisoryRule,
)

__all__ = [
    "TimingAdvisoryRule",
    "ContentTypeAdvisoryRule",
]
