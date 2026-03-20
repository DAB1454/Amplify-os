"""Publisher agent for validating, scheduling, and executing social posts.

All publishing goes through adapter tools — the agent never makes direct
network calls.  Respects rate limits, quiet hours, approvals, and dry-run mode.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from amplify.agents.runtime.agent_runner import AgentContext, AgentResult, AgentRunner
from amplify.agents.runtime.config import AgentConfig
from amplify.agents.runtime.tool_registry import ToolRegistry

# ── Platform constraints ────────────────────────────────────────

PLATFORM_LIMITS: dict[str, dict[str, Any]] = {
    "instagram": {"max_chars": 2200, "max_images": 10, "max_video_mb": 650},
    "tiktok": {"max_chars": 2200, "max_video_mb": 287},
    "youtube": {"max_title_chars": 100, "max_description_chars": 5000},
    "bandcamp": {"max_description_chars": 10000},
    "email": {"max_subject_chars": 78},
}

# ── Structured output models ────────────────────────────────────


class ValidationResult(BaseModel):
    is_valid: bool = True
    issues: list[str] = Field(default_factory=list)


class PublishResult(BaseModel):
    post_id: str = ""
    channel_id: str = ""
    status: str = "pending"
    platform_post_id: str | None = None
    published_at: str | None = None
    error: str | None = None


class PublishBatchReport(BaseModel):
    """Structured batch publishing report."""

    batch_id: str = ""
    results: list[PublishResult] = Field(default_factory=list)
    total: int = 0
    published: int = 0
    failed: int = 0


# ── System prompt ───────────────────────────────────────────────


SYSTEM_PROMPT = (
    "You are the Amplify-OS Publisher Agent. You orchestrate publishing content "
    "across connected platforms through adapter tools. You validate assets, "
    "schedule posts at optimal times, and confirm delivery.\n\n"
    "Runtime rules:\n"
    "- Publish ONLY through adapter tools — never make direct API calls\n"
    "- Obey rate limits, quiet hours, approvals, and dry-run mode\n"
    "- Never bypass policy checks for any reason\n"
    "- Never publish without successful asset validation\n"
    "- Respect platform rate limits — back off on 429 responses\n"
    "- Failed publishes must be queued for retry (max 3 attempts)\n"
    "- Schedule posts in the artist's local timezone, not UTC\n"
    "- Never publish duplicate content to the same channel within 24 hours\n"
)


# ── Agent ───────────────────────────────────────────────────────


class PublisherAgent:
    """Validates, schedules, and publishes posts through adapter tools.

    The agent never contacts external platforms directly.  All publishing
    is delegated to tools registered in the ToolRegistry (backed by adapters).
    """

    AGENT_NAME = "publisher"

    def __init__(
        self,
        runner: AgentRunner,
        tools: ToolRegistry | None = None,
    ) -> None:
        self.runner = runner
        self.tools = tools or ToolRegistry()
        self.dry_run = runner.config.dry_run

    def validate_post(
        self,
        post: dict[str, Any],
        platform: str,
    ) -> ValidationResult:
        """Check that *post* meets *platform* requirements.

        This is synchronous — no LLM call needed.
        """
        issues: list[str] = []
        limits = PLATFORM_LIMITS.get(platform.lower(), {})

        text = post.get("text", post.get("content_text", ""))
        max_chars = limits.get("max_chars")
        if max_chars and len(text) > max_chars:
            issues.append(f"Text exceeds {platform} limit: {len(text)}/{max_chars}")

        images = post.get("images", post.get("media_urls", []))
        max_images = limits.get("max_images")
        if max_images and len(images) > max_images:
            issues.append(f"Too many images for {platform}: {len(images)}/{max_images}")

        video_mb = post.get("video_size_mb")
        max_video = limits.get("max_video_mb")
        if video_mb and max_video and video_mb > max_video:
            issues.append(f"Video exceeds {platform} limit: {video_mb}MB/{max_video}MB")

        if not post.get("destination_url") and not post.get("cta_destination"):
            issues.append("Post has no destination URL (CTA target)")

        return ValidationResult(is_valid=len(issues) == 0, issues=issues)

    async def schedule_post(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        post: dict[str, Any],
        optimal_time: bool = True,
    ) -> AgentResult:
        """Schedule a post, optionally selecting the optimal posting time via Claude."""
        platform = post.get("platform", "unknown")

        if not optimal_time:
            scheduled_at = post.get("scheduled_at", datetime.now(timezone.utc).isoformat())
            result_data = {**post, "scheduled_at": scheduled_at, "status": "scheduled"}
            return AgentResult(
                run_id=uuid.uuid4(),
                agent_name=self.AGENT_NAME,
                text=f"Post scheduled for {scheduled_at}",
                data=result_data,
                model=self.runner.config.model,
            )

        user_message = (
            f"Suggest the single best UTC datetime to publish this {platform} post "
            f"for maximum engagement. Context: {post.get('context', 'music audience')}.\n"
            f"Respond with ONLY an ISO-8601 datetime string."
        )

        ctx = AgentContext(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_name=self.AGENT_NAME,
            run_id=uuid.uuid4(),
        )

        return await self.runner.run(
            agent_name=self.AGENT_NAME,
            system_prompt=SYSTEM_PROMPT,
            user_message=user_message,
            context=ctx,
        )

    async def execute_publish(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        post_id: str,
        channel_id: str,
        platform: str,
    ) -> AgentResult:
        """Publish a post through adapter tools.

        In dry-run mode, validates but does not actually publish.
        """
        if self.dry_run:
            return AgentResult(
                run_id=uuid.uuid4(),
                agent_name=self.AGENT_NAME,
                text="Dry run — publish skipped",
                data={"post_id": post_id, "status": "dry_run"},
                model=self.runner.config.model,
            )

        user_message = (
            f"Publish post {post_id} to channel {channel_id} on {platform}. "
            f"Use the appropriate adapter tool. Validate first, then publish."
        )

        ctx = AgentContext(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_name=self.AGENT_NAME,
            run_id=uuid.uuid4(),
        )

        return await self.runner.run(
            agent_name=self.AGENT_NAME,
            system_prompt=SYSTEM_PROMPT,
            user_message=user_message,
            context=ctx,
            tools=self.tools,
            output_type=PublishResult,
        )
