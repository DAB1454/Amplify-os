"""Community management agent.

Analyzes comments, moderates content, and drafts replies in the artist's
voice.  All comment fetching and reply posting goes through adapter tools.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from amplify.agents.runtime.agent_runner import AgentContext, AgentResult, AgentRunner
from amplify.agents.runtime.tool_registry import ToolRegistry

# ── Structured output models ────────────────────────────────────


class CommentAnalysis(BaseModel):
    sentiment: str = "neutral"
    category: str = "general"
    suggested_action: str = "ignore"
    draft_reply: str | None = None
    confidence: float = 0.0
    requires_approval: bool = True


class ModerationResult(BaseModel):
    comment_id: str = ""
    flagged: bool = False
    reason: str = ""
    action: str = "approve"


class CommunityReport(BaseModel):
    """Structured community management output."""

    scan_id: str = ""
    comments_processed: int = 0
    analyses: list[CommentAnalysis] = Field(default_factory=list)
    moderations: list[ModerationResult] = Field(default_factory=list)
    sentiment_breakdown: dict[str, int] = Field(default_factory=dict)


# ── System prompt ───────────────────────────────────────────────


def _load_system_prompt() -> str:
    try:
        from amplify.core.prompts.community import SYSTEM_PROMPT
        return SYSTEM_PROMPT
    except Exception:
        return (
            "You are a community manager for a music artist. You analyze fan "
            "comments, moderate content, and draft authentic replies that match "
            "the artist's voice."
        )


RUNTIME_INSTRUCTIONS = """
Additional runtime rules:
- Answer like the artist, but do NOT impersonate private relationships
- Draft uncertain replies for approval — never auto-send when confidence < 0.95
- Block unsafe, harassing, or legal-risk replies immediately
- Flag brand safety violations with critical severity
- Spam detection must have < 1% false positive rate
- Do not engage with trolls — flag for human review
- Respect platform-specific comment threading
"""


# ── Agent ───────────────────────────────────────────────────────


class CommunityAgent:
    """Analyzes comments, moderates content, and drafts replies.

    All comment data access goes through adapter tools.  The agent never
    posts replies directly — uncertain replies are queued for approval.
    """

    AGENT_NAME = "community"

    def __init__(
        self,
        runner: AgentRunner,
        tools: ToolRegistry | None = None,
    ) -> None:
        self.runner = runner
        self.tools = tools or ToolRegistry()
        self.system_prompt = _load_system_prompt() + RUNTIME_INSTRUCTIONS

    async def analyze_comment(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        comment: dict[str, Any],
        artist_context: dict[str, Any],
    ) -> AgentResult:
        """Analyze a single comment for sentiment and suggest an action."""
        import json

        user_message = (
            f"Analyze this fan comment and decide how to respond.\n\n"
            f"Comment: {json.dumps(comment)}\n"
            f"Artist context: {json.dumps(artist_context)}\n\n"
            f"Respond with JSON: sentiment, category, suggested_action, "
            f"draft_reply (null if not needed), confidence (0-1), requires_approval."
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
            output_type=CommentAnalysis,
        )

    async def batch_moderate(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        comments: list[dict[str, Any]],
        policies: dict[str, Any],
    ) -> AgentResult:
        """Moderate a batch of comments against content policies."""
        import json

        user_message = (
            f"Moderate the following comments according to these policies.\n\n"
            f"Policies: {json.dumps(policies)}\n\n"
            f"Comments:\n{json.dumps(comments, indent=2)}\n\n"
            f"Respond with JSON: {{\"moderations\": [{{comment_id, flagged, reason, action}}, ...]}}"
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

    async def generate_reply(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        comment: dict[str, Any],
        tone: str,
        context: dict[str, Any],
    ) -> AgentResult:
        """Draft a reply to a fan comment in the given tone."""
        import json

        user_message = (
            f"Write a reply to this fan comment.\n\n"
            f"Comment: {json.dumps(comment)}\n"
            f"Desired tone: {tone}\n"
            f"Context: {json.dumps(context)}\n\n"
            f"Return the reply text. Do NOT impersonate private relationships."
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
        )
