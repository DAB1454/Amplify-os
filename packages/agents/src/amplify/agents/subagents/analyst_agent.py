"""Analytics agent.

Analyzes campaign performance, detects anomalies, and generates reports
with keep/remix/stop recommendations.  All metric data access goes
through adapter tools.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from amplify.agents.runtime.agent_runner import AgentContext, AgentResult, AgentRunner
from amplify.agents.runtime.tool_registry import ToolRegistry

# ── Structured output models ────────────────────────────────────


class ContentRecommendation(BaseModel):
    content_id: str = ""
    action: str = "keep"  # keep | remix | stop
    reason: str = ""
    priority: str = "medium"  # high | medium | low


class CampaignInsight(BaseModel):
    """Structured campaign analysis output."""

    summary: str = ""
    score: float = 0.0
    insights: list[str] = Field(default_factory=list)
    recommendations: list[ContentRecommendation] = Field(default_factory=list)
    anomalies: list[dict[str, Any]] = Field(default_factory=list)


class PerformanceReport(BaseModel):
    """Structured performance report."""

    title: str = ""
    period: str = ""
    sections: list[dict[str, Any]] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    content_verdicts: list[ContentRecommendation] = Field(default_factory=list)


# ── System prompt ───────────────────────────────────────────────


def _load_system_prompt() -> str:
    try:
        from amplify.core.prompts.analyst import SYSTEM_PROMPT
        return SYSTEM_PROMPT
    except Exception:
        return (
            "You are a music marketing analytics expert. Analyze campaign metrics, "
            "detect trends and anomalies, and provide actionable recommendations."
        )


RUNTIME_INSTRUCTIONS = """
Additional runtime rules:
- Compare hooks, formats, channels, and CTAs across content performance
- For each content pattern, recommend: keep (double down), remix (adapt), or stop (retire)
- Surface anomalies and failures early — don't bury bad news in summaries
- Always compare current vs previous period (WoW, MoM)
- Flag metric changes > 20% as noteworthy
- Distinguish organic vs paid when data is available
- Never extrapolate beyond available data — state confidence levels
- Respect data freshness — note last sync timestamp per platform
"""


# ── Agent ───────────────────────────────────────────────────────


class AnalystAgent:
    """Analyzes campaigns, generates reports, and detects anomalies.

    All metric data comes through adapter tools.  Returns structured
    insights with keep/remix/stop verdicts.
    """

    AGENT_NAME = "analyst"

    def __init__(
        self,
        runner: AgentRunner,
        tools: ToolRegistry | None = None,
    ) -> None:
        self.runner = runner
        self.tools = tools or ToolRegistry()
        self.system_prompt = _load_system_prompt() + RUNTIME_INSTRUCTIONS

    async def analyze_campaign(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        campaign_id: str,
        metrics: dict[str, Any],
    ) -> AgentResult:
        """Analyze campaign metrics and return insights with keep/remix/stop."""
        import json

        user_message = (
            f"Analyze campaign {campaign_id}.\n\n"
            f"Metrics:\n{json.dumps(metrics, indent=2)}\n\n"
            f"Respond with JSON: summary, score (0-100), insights (array), "
            f"recommendations (array of {{content_id, action: keep|remix|stop, reason, priority}}), "
            f"anomalies (array)."
        )

        ctx = AgentContext(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_name=self.AGENT_NAME,
            run_id=uuid.uuid4(),
        )

        return await self.runner.run(
            agent_name=self.AGENT_NAME,
            system_prompt=self.system_prompt,
            user_message=user_message,
            context=ctx,
            tools=self.tools,
            output_type=CampaignInsight,
        )

    async def generate_report(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        artist_id: str,
        period: str,
        metrics: dict[str, Any],
    ) -> AgentResult:
        """Generate a structured performance report."""
        import json

        user_message = (
            f"Generate a performance report for artist {artist_id}, "
            f"period: {period}.\n\n"
            f"Metrics:\n{json.dumps(metrics, indent=2)}\n\n"
            f"Respond with JSON: title, period, sections (array of {{heading, body, data}}), "
            f"highlights (array), content_verdicts (array of keep/remix/stop)."
        )

        ctx = AgentContext(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_name=self.AGENT_NAME,
            run_id=uuid.uuid4(),
        )

        return await self.runner.run(
            agent_name=self.AGENT_NAME,
            system_prompt=self.system_prompt,
            user_message=user_message,
            context=ctx,
            tools=self.tools,
            output_type=PerformanceReport,
        )

    async def detect_anomalies(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        metric_history: list[dict[str, Any]],
    ) -> AgentResult:
        """Identify unusual patterns in time-series metrics."""
        import json

        user_message = (
            f"Analyze this metric history and identify anomalies.\n\n"
            f"Data ({len(metric_history)} points):\n"
            f"{json.dumps(metric_history[:200], indent=2)}\n\n"
            f"Respond with JSON: {{\"anomalies\": [{{timestamp, metric_name, value, "
            f"expected_range, severity: low|medium|high, description}}, ...]}}"
        )

        ctx = AgentContext(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_name=self.AGENT_NAME,
            run_id=uuid.uuid4(),
        )

        return await self.runner.run(
            agent_name=self.AGENT_NAME,
            system_prompt=self.system_prompt,
            user_message=user_message,
            context=ctx,
            tools=self.tools,
        )
