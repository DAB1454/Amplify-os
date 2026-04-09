"""Goal mix defaults and post-generation rebalancer.

A campaign's `goal_mix` is the target distribution of marketing intents
across all generated posts. The planner LLM is asked to set a `goal` on
every action, but it doesn't always honor the requested distribution —
this rebalancer enforces it deterministically after generation.

Goals: awareness, follow, engage, save, stream, purchase.

The defaults are phase-aware because the right intent mix shifts with
the release date:
- Far pre-release: build audience first, don't push streams that don't exist.
- Imminent pre-release: shift toward saves and pre-release teases.
- Release week: push streams hard while keeping engagement and awareness.
- Post-release: sustain streams + engagement, light purchase pressure.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Iterable

GOALS: tuple[str, ...] = (
    "awareness",
    "follow",
    "engage",
    "save",
    "stream",
    "purchase",
)


def default_goal_mix(
    *,
    phase: str | None,
    days_to_release: int | None,
) -> dict[str, float]:
    """Return a phase-aware default goal mix.

    `phase` is the campaign.phase column ("pre_release", "release_week",
    "post_release") and overrides days_to_release if both are provided.
    `days_to_release` lets us split pre_release into "far" and "near".
    Negative days_to_release means the release is already out.
    """
    p = (phase or "").lower().strip()

    if p == "release_week" or (days_to_release is not None and -1 <= days_to_release <= 7):
        return {
            "stream": 0.40,
            "save": 0.20,
            "engage": 0.20,
            "awareness": 0.15,
            "purchase": 0.05,
            "follow": 0.0,
        }

    if p == "post_release" or (days_to_release is not None and days_to_release < -1):
        return {
            "stream": 0.35,
            "engage": 0.25,
            "awareness": 0.20,
            "follow": 0.10,
            "purchase": 0.10,
            "save": 0.0,
        }

    # pre_release — split by proximity
    if days_to_release is not None and days_to_release <= 14:
        return {
            "awareness": 0.30,
            "save": 0.25,
            "engage": 0.20,
            "follow": 0.15,
            "stream": 0.10,
            "purchase": 0.0,
        }

    # default: far pre-release (or unknown)
    return {
        "awareness": 0.50,
        "engage": 0.25,
        "follow": 0.15,
        "save": 0.10,
        "stream": 0.0,
        "purchase": 0.0,
    }


def normalize_mix(mix: dict[str, float]) -> dict[str, float]:
    """Clamp to known goals, drop negatives, and renormalize to sum to 1.0.

    Unknown keys are silently dropped. If everything is zero, returns the
    default far-pre-release mix to avoid divide-by-zero downstream.
    """
    cleaned = {g: max(0.0, float(mix.get(g, 0.0) or 0.0)) for g in GOALS}
    total = sum(cleaned.values())
    if total <= 0:
        return default_goal_mix(phase="pre_release", days_to_release=None)
    return {g: v / total for g, v in cleaned.items()}


def rebalance_goals(
    actions: list,
    target_mix: dict[str, float],
) -> dict[str, int]:
    """Reassign action.goal in-place so the distribution matches target_mix.

    Strategy:
      1. Compute target counts from the normalized mix and len(actions).
      2. For each goal that is OVER its target, mark the surplus actions
         as "available for reassignment" — preferring actions whose goal
         was empty in the first place.
      3. Walk under-represented goals in order of biggest deficit and
         reassign available actions to them, mutating action.goal.
      4. Actions whose original goal was already at or below its target
         are preserved unchanged.

    This deliberately does NOT rewrite content_brief — copy guidance lives
    in the planner prompt and is the LLM's job. The rebalancer only fixes
    the LABEL so that downstream CTA selection and analytics see a sane
    distribution. (If the LLM mislabels copy as e.g. "stream" when the
    text actually says "tap follow", that's a separate quality issue —
    we'd rather have correct intent metadata than blindly trust labels.)

    Returns a dict counting how many actions were reassigned per goal.
    """
    if not actions:
        return {}

    target_mix = normalize_mix(target_mix)
    n = len(actions)

    # Target counts per goal — round nearest, then absorb remainder into
    # the largest-weight goal so the totals match exactly.
    target_counts: dict[str, int] = {g: round(target_mix[g] * n) for g in GOALS}
    diff = n - sum(target_counts.values())
    if diff != 0:
        biggest = max(GOALS, key=lambda g: target_mix[g])
        target_counts[biggest] = max(0, target_counts[biggest] + diff)

    # Current distribution + index by goal (preserve original order)
    by_goal: dict[str, list[int]] = {g: [] for g in GOALS}
    untagged: list[int] = []
    for i, a in enumerate(actions):
        g = (getattr(a, "goal", "") or "").lower().strip()
        if g in by_goal:
            by_goal[g].append(i)
        else:
            untagged.append(i)

    # Build the reassignment pool: untagged actions first (they have no
    # author intent to honor), then surplus from over-target goals.
    pool: list[int] = list(untagged)
    for g in GOALS:
        surplus = len(by_goal[g]) - target_counts[g]
        if surplus > 0:
            pool.extend(by_goal[g][-surplus:])
            by_goal[g] = by_goal[g][:-surplus]

    # Walk deficits and assign
    reassigned: Counter[str] = Counter()
    for g in sorted(GOALS, key=lambda g: target_counts[g] - len(by_goal[g]), reverse=True):
        deficit = target_counts[g] - len(by_goal[g])
        while deficit > 0 and pool:
            idx = pool.pop(0)
            actions[idx].goal = g
            by_goal[g].append(idx)
            reassigned[g] += 1
            deficit -= 1

    return dict(reassigned)
