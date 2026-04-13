"""Content generation agent.

Generates captions, hooks, short video scripts, CTA variants, hashtag
sets, and comment reply drafts.  Always produces 3 variants per idea
and respects per-artist brand voice constraints.

All external actions go through adapter interfaces via tools.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from pydantic import BaseModel, Field

from amplify.agents.runtime.agent_runner import AgentContext, AgentResult, AgentRunner
from amplify.agents.runtime.tool_registry import ToolRegistry
from amplify.agents.learning_context import AgentDecisionRecord, LearningContext

# ── Structured output models ────────────────────────────────────


class CopyVariant(BaseModel):
    """A single copy variant (A, B, or C)."""

    variant_id: str = "A"
    id: str = ""  # alias the AI sometimes uses for variant_id
    headline: str = ""
    body: str = ""
    copy: str = ""  # alias the AI sometimes uses for body
    hashtags: list[str] | str = Field(default_factory=list)
    cta: str = ""
    tone: str = ""
    platform: str = ""
    content_type: str = ""  # caption, hook, script, cta, reply
    char_count: int = 0

    model_config = {"extra": "ignore"}

    @property
    def resolved_body(self) -> str:
        """Return body text regardless of which field the AI populated."""
        return self.body or self.copy or ""

    @property
    def resolved_id(self) -> str:
        return self.variant_id or self.id or "A"


class ContentOutput(BaseModel):
    """Standard output for any content generation request."""

    action_id: str = ""
    channel: str = ""
    content_type: str = ""  # caption, hook, video_script, cta, hashtags
    variants: list[CopyVariant] = Field(default_factory=list)
    brand_voice_applied: str = ""
    notes: str = ""
    # IDs of learned recommendations that influenced this content
    recommendations_applied: list[str] = Field(default_factory=list)


class CommentReply(BaseModel):
    """A single comment reply draft."""

    comment_id: str = ""
    original_comment: str = ""
    reply: str = ""
    tone: str = ""  # warm, playful, grateful, informative
    requires_approval: bool = False
    approval_reason: str = ""


class CommentReplyOutput(BaseModel):
    """Output for comment reply generation."""

    replies: list[CommentReply] = Field(default_factory=list)
    flagged_comments: list[str] = Field(default_factory=list)
    notes: str = ""


class HashtagSet(BaseModel):
    """A categorized hashtag set."""

    category: str = ""  # discovery, niche, campaign
    tags: list[str] = Field(default_factory=list)


class HashtagOutput(BaseModel):
    """Output for hashtag generation."""

    sets: list[HashtagSet] = Field(default_factory=list)
    notes: str = ""


class EmailCopy(BaseModel):
    """Email marketing copy."""

    subject: str = ""
    preview_text: str = ""
    body: str = ""
    cta_text: str = ""


# ── System prompt ───────────────────────────────────────────────


def _load_system_prompt() -> str:
    try:
        from amplify.core.prompts.copywriter import SYSTEM_PROMPT
        return SYSTEM_PROMPT
    except Exception:
        return (
            "You are an expert music marketing copywriter. Craft engaging, "
            "platform-appropriate content for artists. Always produce 3 variants."
        )


def _load_template(name: str) -> str:
    """Load a named template from the copywriter prompts module."""
    try:
        import core.prompts.copywriter as mod
        return getattr(mod, name)
    except Exception:
        return ""


RUNTIME_INSTRUCTIONS = """

## Runtime constraints
- Follow the artist brand_voice config for all copy generation.
- Produce exactly 3 variants per idea (A, B, C).
- Vary tone across variants: e.g. casual, hype, emotional.
- Keep captions platform-native — adapt format and length to each channel.
- Every variant MUST include a CTA.
- Never use placeholder text — all copy must be publish-ready.
- Reference actual song titles, album name, artist name, and release date.
- Respect per-platform character limits.
- Comment replies must sound human and match the artist's voice.
- Flag comments that touch sensitive topics, legal risk, or harassment.
"""


# ── Agent ───────────────────────────────────────────────────────


class ContentAgent:
    """Generates marketing copy across platforms.

    Returns structured ContentOutput with 3 variants per request.
    Supports captions, hooks, video scripts, CTAs, hashtag sets,
    and comment reply drafts.
    """

    AGENT_NAME = "content"

    def __init__(
        self,
        runner: AgentRunner,
        tools: ToolRegistry | None = None,
    ) -> None:
        self.runner = runner
        self.tools = tools or ToolRegistry()
        self.system_prompt = _load_system_prompt() + RUNTIME_INSTRUCTIONS

    async def generate_caption(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        artist_name: str,
        platform: str,
        content_type: str,
        release_title: str,
        key_message: str,
        track_title: str = "",
        cta_destination: str = "",
        tone: str = "authentic",
        brand_voice: str = "",
        hashtags: list[str] | None = None,
        learning_context: LearningContext | None = None,
    ) -> AgentResult:
        """Generate platform-specific captions with 3 variants."""
        effective_track = track_title or release_title
        template = _load_template("CAPTION_TEMPLATE")
        if template:
            user_message = template.format(
                artist_name=artist_name,
                platform=platform,
                content_type=content_type,
                release_title=release_title,
                track_title=effective_track,
                key_message=key_message,
                cta_destination=cta_destination or "link in bio",
                tone=tone,
                brand_voice=brand_voice or "No specific brand voice — be authentic.",
                hashtags=", ".join(hashtags) if hashtags else "none provided",
            )
        else:
            user_message = (
                f"Write 3 caption variants for {platform}.\n"
                f"Artist: {artist_name}\nRelease: {release_title}\n"
                f"Track: {effective_track}\n"
                f"Message: {key_message}\nTone: {tone}\n"
                f"CTA: {cta_destination}\nBrand voice: {brand_voice}\n"
                f"Respond with JSON matching ContentOutput schema."
            )

        return await self._run(
            tenant_id, user_id, user_message, ContentOutput,
            learning_context=learning_context,
        )

    async def generate_hook(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        artist_name: str,
        platform: str,
        track_title: str,
        hook_concept: str,
        cta: str = "",
        brand_voice: str = "",
        learning_context: LearningContext | None = None,
    ) -> AgentResult:
        """Generate short-form video hooks with 3 variants."""
        template = _load_template("HOOK_TEMPLATE")
        if template:
            user_message = template.format(
                artist_name=artist_name,
                platform=platform,
                track_title=track_title,
                hook_concept=hook_concept,
                cta=cta or "link in bio",
                brand_voice=brand_voice or "No specific brand voice.",
            )
        else:
            user_message = (
                f"Write 3 video hook variants for {platform}.\n"
                f"Artist: {artist_name}\nTrack: {track_title}\n"
                f"Concept: {hook_concept}\nCTA: {cta}\n"
                f"Respond with JSON matching ContentOutput schema."
            )

        return await self._run(
            tenant_id, user_id, user_message, ContentOutput,
            learning_context=learning_context,
        )

    async def generate_video_script(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        artist_name: str,
        platform: str,
        concept: str,
        track_title: str = "",
        visual_style: str = "",
        cta_destination: str = "",
        brand_voice: str = "",
    ) -> AgentResult:
        """Generate a short video script (15-60 seconds) with 3 variants."""
        template = _load_template("VIDEO_SCRIPT_TEMPLATE")
        if template:
            user_message = template.format(
                artist_name=artist_name,
                platform=platform,
                concept=concept,
                track_title=track_title or "not specified",
                visual_style=visual_style or "authentic, natural",
                cta_destination=cta_destination or "link in bio",
                brand_voice=brand_voice or "No specific brand voice.",
            )
        else:
            user_message = (
                f"Write 3 video script variants for {platform}.\n"
                f"Artist: {artist_name}\nConcept: {concept}\n"
                f"Track: {track_title}\nCTA: {cta_destination}\n"
                f"Respond with JSON matching ContentOutput schema."
            )

        return await self._run(tenant_id, user_id, user_message, ContentOutput)

    async def generate_hashtags(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        artist_name: str,
        genre: str,
        platform: str,
        release_title: str,
        campaign_hashtags: list[str] | None = None,
    ) -> AgentResult:
        """Generate categorized hashtag sets (discovery, niche, campaign)."""
        template = _load_template("HASHTAG_TEMPLATE")
        if template:
            user_message = template.format(
                artist_name=artist_name,
                genre=genre,
                platform=platform,
                release_title=release_title,
                campaign_hashtags=", ".join(campaign_hashtags) if campaign_hashtags else "none",
            )
        else:
            user_message = (
                f"Generate hashtag sets for {platform}.\n"
                f"Artist: {artist_name}\nGenre: {genre}\n"
                f"Release: {release_title}\n"
                f"Respond with JSON matching HashtagOutput schema."
            )

        return await self._run(tenant_id, user_id, user_message, HashtagOutput)

    async def generate_comment_replies(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        artist_name: str,
        comments: list[dict[str, str]],
        brand_voice: str = "",
    ) -> AgentResult:
        """Draft replies to fan comments."""
        template = _load_template("COMMENT_REPLY_TEMPLATE")
        comments_text = "\n".join(
            f"- [{c.get('id', 'unknown')}] {c.get('text', '')}"
            for c in comments
        )
        if template:
            user_message = template.format(
                artist_name=artist_name,
                brand_voice=brand_voice or "Warm, grateful, human.",
                comments=comments_text,
            )
        else:
            user_message = (
                f"Draft replies for {artist_name}.\n"
                f"Comments:\n{comments_text}\n"
                f"Brand voice: {brand_voice}\n"
                f"Respond with JSON matching CommentReplyOutput schema."
            )

        return await self._run(tenant_id, user_id, user_message, CommentReplyOutput)

    async def generate_email(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        context: dict[str, Any],
    ) -> AgentResult:
        """Generate email copy (subject, preview, body, CTA)."""
        user_message = (
            f"Write a marketing email.\n"
            f"Context: {json.dumps(context)}\n\n"
            f"Respond with JSON: subject, preview_text, body, cta_text."
        )
        return await self._run(tenant_id, user_id, user_message, EmailCopy)

    async def generate_variants(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None = None,
        content: str,
        platform: str = "",
        count: int = 3,
    ) -> AgentResult:
        """Generate A/B/C variants of existing content."""
        user_message = (
            f"Generate {count} variants of this content for {platform or 'any platform'}. "
            f"Maintain the core message but vary tone, length, and CTA.\n\n"
            f"Original:\n{content}\n\n"
            f"Respond with a JSON object matching the ContentOutput schema."
        )
        return await self._run(tenant_id, user_id, user_message, ContentOutput)

    # ── Internal helper ───────────────────────────────────────────

    async def _run(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None,
        user_message: str,
        output_type: type,
        learning_context: LearningContext | None = None,
    ) -> AgentResult:
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
            output_type=output_type,
        )

        # Build decision record for audit
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

    text = result.text or ""
    for rid in available:
        if rid in text:
            applied.append(rid)

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
