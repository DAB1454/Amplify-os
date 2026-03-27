"""Campaign planner agent.

Generates campaign plans from release metadata, existing
calendar items, and prior performance metrics.  Outputs publish
recommendations, experiments, and content gaps.

All external data access goes through registered tools.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from amplify.agents.runtime.agent_runner import AgentContext, AgentResult, AgentRunner
from amplify.agents.runtime.config import AgentConfig
from amplify.agents.runtime.tool_registry import ToolRegistry
from amplify.agents.learning_context import AgentDecisionRecord, LearningContext

# ── Structured output models ────────────────────────────────────


class DailyAction(BaseModel):
    """A single action to take on a specific day and platform."""

    day: str = Field(default="", alias="date")  # ISO date — accepts "date" or "day"
    platform: str = ""
    action_type: str = ""  # post, story, reel, short, live, engagement, email
    content_brief: str = ""
    cta_destination: str = ""
    assets_required: list[str] = Field(default_factory=list, alias="asset_requirements")
    priority: str = "medium"  # critical, high, medium, low
    track_reference: str = ""  # which track this action features, if any

    model_config = {"populate_by_name": True}  # accept both field name and alias


class ExperimentProposal(BaseModel):
    """An A/B test to run during the plan window."""

    name: str = ""
    hypothesis: str = ""
    variants: list[str] = Field(default_factory=list)
    metric: str = ""  # primary metric to compare
    platform: str = ""
    duration_days: int = 7


class ContentGap(BaseModel):
    """A gap in the content calendar that should be filled."""

    platform: str = ""
    date_range: str = ""  # e.g. "2026-03-20 to 2026-03-22"
    gap_type: str = ""  # no_content, low_frequency, missing_format, no_cta
    suggestion: str = ""


class PublishRecommendation(BaseModel):
    """Recommendation for an existing draft or scheduled post."""

    post_id: str = ""
    title: str = ""
    action: str = ""  # publish, reschedule, cut, edit
    reason: str = ""


class KPI(BaseModel):
    """A measurable goal for the plan window."""

    metric: str = ""
    target: str = ""
    platform: str = ""


class PlannerOutput(BaseModel):
    """Full output of the planner agent — a campaign plan."""

    campaign_name: str = ""
    artist_id: str = ""
    release_id: str = ""
    plan_start: str = ""  # ISO date
    plan_end: str = ""  # ISO date
    daily_actions: list[DailyAction] = Field(default_factory=list)
    experiments: list[ExperimentProposal] = Field(default_factory=list)
    content_gaps: list[ContentGap] = Field(default_factory=list)
    publish_recommendations: list[PublishRecommendation] = Field(default_factory=list)
    kpis: list[KPI] = Field(default_factory=list)
    budget_allocation: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""
    # IDs of learned recommendations that influenced this plan
    recommendations_applied: list[str] = Field(default_factory=list)


# ── System prompt ───────────────────────────────────────────────


def _load_system_prompt() -> str:
    try:
        from amplify.core.prompts.planner import SYSTEM_PROMPT
        return SYSTEM_PROMPT
    except Exception:
        return (
            "You are a music marketing campaign planner. Given an artist, release, "
            "genre, channels, date range, and optional budget, produce a detailed campaign "
            "plan covering every day in the specified date range as valid JSON."
        )


def _load_plan_template() -> str:
    try:
        from amplify.core.prompts.planner import PLAN_CAMPAIGN_TEMPLATE
        return PLAN_CAMPAIGN_TEMPLATE
    except Exception:
        return (
            "Create a campaign plan for {artist_name} releasing "
            "{release_title} on {release_date}.\n\n"
            "Campaign date range: {campaign_start} to {campaign_end}\n"
            "Plan MUST cover every day from {campaign_start} through {campaign_end}.\n"
            "Do NOT default to 14 days — use the exact date range provided.\n\n"
            "Respond with valid JSON."
        )


def _load_adjust_template() -> str:
    try:
        from amplify.core.prompts.planner import ADJUST_PLAN_TEMPLATE
        return ADJUST_PLAN_TEMPLATE
    except Exception:
        return (
            "Adjust the following plan:\n{current_plan}\n\n"
            "Feedback: {feedback}\n\nRespond with valid JSON."
        )


RUNTIME_INSTRUCTIONS = """

## Runtime constraints
- Optimize for real audience growth, not vanity metrics.
- Prefer owned audience capture and repeatable content systems.
- Every daily action MUST have a cta_destination URL.
- Never recommend spammy or deceptive growth tactics.
- Max 3 posts per channel per day.
- Always include at least one experiment proposal.
- Flag content gaps where a platform has no content for 2+ consecutive days.
- Reference specific tracks by name when proposing content.
- ONLY propose content the artist can actually create with their available assets.
- If the artist has no video footage, do NOT suggest behind-the-scenes or live videos.
- If the artist has no alternate versions, do NOT suggest acoustic or remix content.
- Read the "Available Assets" and "Artist Content Constraints" sections carefully.
- For AI-generated music artists: focus on album art, lyric graphics, audio snippets,
  track teasers, playlist placements, and text-based engagement — NOT personal photos or video.

## CRITICAL: content_brief = the actual post caption
- The content_brief field is posted DIRECTLY to social media as the caption.
- Write compelling, audience-facing copy — NOT production notes or video descriptions.
- Include hashtags, emojis, and calls-to-action naturally in the caption.
- Use asset_requirements to tell the system what media to attach (e.g. ["album art", "audio snippet"]).
- Do NOT describe video edits, text overlays, or produced content in the caption.
"""


# ── Agent ───────────────────────────────────────────────────────


class PlannerAgent:
    """Plans campaign windows around music releases.

    Returns a structured PlannerOutput via the AgentRunner framework.
    All data access goes through tools registered in the ToolRegistry.
    """

    AGENT_NAME = "planner"

    def __init__(
        self,
        runner: AgentRunner,
        tools: ToolRegistry | None = None,
    ) -> None:
        self.runner = runner
        self.tools = tools or ToolRegistry()
        self.system_prompt = _load_system_prompt() + RUNTIME_INSTRUCTIONS

    async def plan_campaign(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        artist_name: str,
        release_title: str,
        release_type: str = "album",
        release_date: str | date,
        genre: str,
        channels: list[str],
        track_listing: list[str] | None = None,
        destination_urls: dict[str, str] | None = None,
        calendar_items: list[dict] | None = None,
        prior_metrics: dict[str, Any] | None = None,
        budget: float | None = None,
        learning_context: LearningContext | None = None,
        available_assets: list[dict[str, str]] | None = None,
        content_notes: str = "",
        campaign_start: str | date = "",
        campaign_end: str | date = "",
        campaign_phase: str = "",
    ) -> AgentResult:
        """Generate a structured campaign plan for the given date range.

        Parameters
        ----------
        learning_context : LearningContext | None
            Tenant patterns and recommendations. If provided, they are
            injected into the prompt as structured guidance. The agent
            is instructed to reference recommendation IDs in its output.
        """
        template = _load_plan_template()
        budget_str = f"${budget:,.2f}" if budget is not None else "No budget — organic only"

        # Compute campaign date range — fall back to 14 days from today
        # only if no dates were provided
        cs = str(campaign_start) if campaign_start else ""
        ce = str(campaign_end) if campaign_end else ""
        if not cs:
            cs = str(date.today())
        if not ce:
            from datetime import timedelta
            try:
                start_dt = date.fromisoformat(cs)
            except (ValueError, TypeError):
                start_dt = date.today()
            ce = str(start_dt + timedelta(days=13))

        user_message = template.format(
            artist_name=artist_name,
            release_title=release_title,
            release_type=release_type,
            release_date=str(release_date),
            genre=genre,
            track_listing=json.dumps(track_listing or [], indent=2),
            destination_urls=json.dumps(destination_urls or {}, indent=2),
            channels="\n".join(f"- {ch}" for ch in channels),
            calendar_items=json.dumps(calendar_items or [], indent=2),
            prior_metrics=json.dumps(prior_metrics or {}, indent=2),
            budget=budget_str,
            today=str(date.today()),
            campaign_start=cs,
            campaign_end=ce,
        )

        # Inject campaign phase so the planner respects pre-release vs release vs sustain
        if campaign_phase:
            phase_label = campaign_phase.replace("_", " ").title()
            phase_guidance = {
                "pre_release": (
                    "This is a PRE-RELEASE campaign. The music has NOT been released yet. "
                    "Do NOT include release day announcements, 'out now' posts, streaming "
                    "links, or any content that implies the music is already available. "
                    "Focus on: building anticipation, teasers, countdowns, pre-save links, "
                    "behind-the-concept content, and audience engagement."
                ),
                "release_week": (
                    "This is a RELEASE WEEK campaign. The music drops during this window. "
                    "Build to the release day, then maximize reach on release day and sustain "
                    "momentum for the remaining days."
                ),
                "sustain": (
                    "This is a POST-RELEASE SUSTAIN campaign. The music is already out. "
                    "Focus on: continued engagement, playlist pitching, fan content, "
                    "deep cuts, milestone celebrations, and converting listeners to fans."
                ),
                "evergreen": (
                    "This is an EVERGREEN campaign. Focus on ongoing discovery, playlist "
                    "placement, catalog highlights, and steady audience growth."
                ),
            }
            guidance = phase_guidance.get(campaign_phase, f"Campaign phase: {phase_label}")
            user_message += f"\n\n## Campaign Phase: {phase_label}\n{guidance}\n"

        # Inject available assets into the prompt
        if available_assets:
            asset_block = "\n## Available Assets in Library\n"
            asset_block += "ONLY propose content that can be created with these assets:\n"
            for a in available_assets:
                asset_block += f"- {a.get('name', 'unnamed')} ({a.get('asset_type', 'unknown')})"
                if a.get('tags'):
                    asset_block += f" [tags: {a['tags']}]"
                asset_block += "\n"
            user_message += "\n\n" + asset_block

        # Inject content constraints/notes
        if content_notes:
            user_message += (
                "\n\n## Artist Content Constraints\n"
                "IMPORTANT — respect these constraints when planning:\n"
                + content_notes + "\n"
            )

        # Inject learning context into the prompt
        lc = learning_context or LearningContext()
        learning_block = lc.format_for_prompt()
        system = self.system_prompt + "\n\n" + learning_block

        ctx = AgentContext(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_name=self.AGENT_NAME,
            run_id=uuid.uuid4(),
        )

        result = await self.runner.run(
            agent_name=self.AGENT_NAME,
            system_prompt=system,
            user_message=user_message,
            context=ctx,
            tools=self.tools,
            output_type=PlannerOutput,
        )

        # Build decision record for audit
        result.learning_decision = _build_decision_record(lc, result)
        result.learning_snapshot = lc.to_audit_snapshot()

        return result

    async def adjust_plan(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        current_plan: dict[str, Any],
        feedback: str,
        updated_metrics: dict[str, Any] | None = None,
        learning_context: LearningContext | None = None,
    ) -> AgentResult:
        """Adjust an existing plan based on feedback."""
        template = _load_adjust_template()

        user_message = template.format(
            current_plan=json.dumps(current_plan, indent=2),
            feedback=feedback,
            updated_metrics=json.dumps(updated_metrics or {}, indent=2),
        )

        lc = learning_context or LearningContext()
        learning_block = lc.format_for_prompt()
        system = self.system_prompt + "\n\n" + learning_block

        ctx = AgentContext(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_name=self.AGENT_NAME,
            run_id=uuid.uuid4(),
        )

        result = await self.runner.run(
            agent_name=self.AGENT_NAME,
            system_prompt=system,
            user_message=user_message,
            context=ctx,
            tools=self.tools,
            output_type=PlannerOutput,
        )

        result.learning_decision = _build_decision_record(lc, result)
        result.learning_snapshot = lc.to_audit_snapshot()

        return result


# ── Helpers ────────────────────────────────────────────────────────


def _build_decision_record(
    lc: LearningContext, result: AgentResult
) -> AgentDecisionRecord:
    """Scan the agent output to determine which recommendations were used."""
    available = lc.recommendation_ids() + lc.avoid_ids()
    applied: list[str] = []

    # Check if the agent referenced any recommendation IDs in its text
    text = result.text or ""
    for rid in available:
        if rid in text:
            applied.append(rid)

    # Also check structured output's recommendations_applied field
    if result.structured and hasattr(result.structured, "recommendations_applied"):
        for rid in result.structured.recommendations_applied:
            if rid not in applied:
                applied.append(rid)

    ignored = [r for r in lc.recommendation_ids() if r not in applied]

    return AgentDecisionRecord(
        learning_snapshot_id=lc.snapshot_id,
        recommendations_available=available,
        recommendations_applied=applied,
        recommendations_ignored=ignored,
        fallback_used=lc.is_empty(),
    )
