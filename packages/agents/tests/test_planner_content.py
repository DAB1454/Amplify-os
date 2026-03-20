"""Tests for planner and content agents — output schemas and agent methods.

Tests validate:
1. All output Pydantic models serialize/deserialize correctly
2. Agents produce AgentResult with structured output when Claude responds
3. Prompt templates render without errors
4. Brand voice constraints are passed through to the content agent
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from amplify.agents.runtime.agent_runner import AgentContext, AgentResult, AgentRunner
from amplify.agents.runtime.config import AgentConfig
from amplify.agents.runtime.tool_registry import ToolRegistry

# ── Planner models ────────────────────────────────────────────────

from amplify.agents.subagents.planner_agent import (
    DailyAction,
    ExperimentProposal,
    ContentGap,
    PublishRecommendation,
    KPI,
    PlannerOutput,
    PlannerAgent,
)

# ── Content models ────────────────────────────────────────────────

from amplify.agents.subagents.content_agent import (
    CopyVariant,
    ContentOutput,
    CommentReply,
    CommentReplyOutput,
    HashtagSet,
    HashtagOutput,
    EmailCopy,
    ContentAgent,
)


# ═══════════════════════════════════════════════════════════════════
# PLANNER OUTPUT SCHEMA TESTS
# ═══════════════════════════════════════════════════════════════════


class TestDailyAction:
    def test_defaults(self):
        a = DailyAction()
        assert a.day == ""
        assert a.platform == ""
        assert a.priority == "medium"
        assert a.assets_required == []
        assert a.track_reference == ""

    def test_full_construction(self):
        a = DailyAction(
            day="2026-03-20",
            platform="instagram",
            action_type="reel",
            content_brief="15s clip of chorus from Whiskey Scars",
            cta_destination="https://linktr.ee/dbmusic26",
            assets_required=["album_artwork_square.jpg"],
            priority="high",
            track_reference="Whiskey Scars",
        )
        assert a.platform == "instagram"
        assert a.priority == "high"
        d = a.model_dump()
        assert d["track_reference"] == "Whiskey Scars"

    def test_json_roundtrip(self):
        a = DailyAction(day="2026-03-20", platform="tiktok", action_type="post")
        raw = a.model_dump_json()
        parsed = DailyAction.model_validate_json(raw)
        assert parsed.day == "2026-03-20"


class TestExperimentProposal:
    def test_defaults(self):
        e = ExperimentProposal()
        assert e.duration_days == 7
        assert e.variants == []

    def test_full(self):
        e = ExperimentProposal(
            name="Hook Style A/B",
            hypothesis="Storytelling hooks outperform direct hooks on TikTok",
            variants=["storytelling", "direct"],
            metric="engagement_rate",
            platform="tiktok",
            duration_days=14,
        )
        assert len(e.variants) == 2
        assert e.duration_days == 14


class TestContentGap:
    def test_construction(self):
        g = ContentGap(
            platform="youtube",
            date_range="2026-03-22 to 2026-03-24",
            gap_type="no_content",
            suggestion="Post a YouTube Short with track 3 preview",
        )
        assert g.gap_type == "no_content"


class TestPublishRecommendation:
    def test_construction(self):
        r = PublishRecommendation(
            post_id="abc-123",
            title="Teaser post",
            action="publish",
            reason="Release day is tomorrow — this should go out now",
        )
        assert r.action == "publish"


class TestKPI:
    def test_construction(self):
        k = KPI(metric="pre_saves", target="500", platform="spotify")
        assert k.metric == "pre_saves"


class TestPlannerOutput:
    def test_empty(self):
        p = PlannerOutput()
        assert p.daily_actions == []
        assert p.experiments == []
        assert p.content_gaps == []
        assert p.kpis == []

    def test_full_plan(self):
        p = PlannerOutput(
            campaign_name="For Love of Country Launch",
            artist_id="00000001",
            release_id="00000002",
            plan_start="2026-03-18",
            plan_end="2026-04-01",
            daily_actions=[
                DailyAction(
                    day="2026-03-18",
                    platform="instagram",
                    action_type="reel",
                    content_brief="Lead track preview",
                    cta_destination="https://linktr.ee/dbmusic26",
                    priority="high",
                ),
                DailyAction(
                    day="2026-03-18",
                    platform="tiktok",
                    action_type="post",
                    content_brief="Hook clip",
                    cta_destination="https://linktr.ee/dbmusic26",
                ),
            ],
            experiments=[
                ExperimentProposal(
                    name="Hook A/B",
                    hypothesis="Story hooks > direct",
                    variants=["story", "direct"],
                    metric="engagement_rate",
                    platform="tiktok",
                ),
            ],
            content_gaps=[
                ContentGap(
                    platform="youtube",
                    date_range="2026-03-22 to 2026-03-24",
                    gap_type="no_content",
                    suggestion="Post a Short",
                ),
            ],
            kpis=[
                KPI(metric="followers", target="+200", platform="instagram"),
            ],
            notes="Focus on countdown content this week.",
        )
        assert len(p.daily_actions) == 2
        assert len(p.experiments) == 1
        assert len(p.content_gaps) == 1

    def test_json_roundtrip(self):
        p = PlannerOutput(
            campaign_name="Test",
            daily_actions=[
                DailyAction(day="2026-03-20", platform="instagram", action_type="post"),
            ],
        )
        raw = p.model_dump_json()
        parsed = PlannerOutput.model_validate_json(raw)
        assert parsed.campaign_name == "Test"
        assert len(parsed.daily_actions) == 1

    def test_schema_generation(self):
        """Schema can be generated for injection into Claude system prompt."""
        schema = PlannerOutput.model_json_schema()
        assert "properties" in schema
        assert "daily_actions" in schema["properties"]
        assert "experiments" in schema["properties"]
        assert "content_gaps" in schema["properties"]


# ═══════════════════════════════════════════════════════════════════
# CONTENT OUTPUT SCHEMA TESTS
# ═══════════════════════════════════════════════════════════════════


class TestCopyVariant:
    def test_defaults(self):
        v = CopyVariant()
        assert v.variant_id == "A"
        assert v.hashtags == []
        assert v.char_count == 0

    def test_full(self):
        v = CopyVariant(
            variant_id="B",
            headline="New album dropping 3/29",
            body="For Love of Country is here. 14 tracks of real country music.",
            hashtags=["#ForLoveOfCountry", "#CountryMusic", "#NewMusic2026"],
            cta="Link in bio",
            platform="instagram",
            content_type="caption",
            char_count=85,
        )
        assert v.variant_id == "B"
        assert len(v.hashtags) == 3


class TestContentOutput:
    def test_empty(self):
        c = ContentOutput()
        assert c.variants == []

    def test_three_variants(self):
        c = ContentOutput(
            action_id="act-001",
            channel="instagram",
            content_type="caption",
            variants=[
                CopyVariant(variant_id="A", headline="A hook", body="A body", cta="Link in bio"),
                CopyVariant(variant_id="B", headline="B hook", body="B body", cta="Pre-save now"),
                CopyVariant(variant_id="C", headline="C hook", body="C body", cta="Listen now"),
            ],
            brand_voice_applied="Country authentic",
        )
        assert len(c.variants) == 3
        ids = [v.variant_id for v in c.variants]
        assert ids == ["A", "B", "C"]

    def test_json_roundtrip(self):
        c = ContentOutput(
            channel="tiktok",
            variants=[CopyVariant(variant_id="A", headline="Hook")],
        )
        raw = c.model_dump_json()
        parsed = ContentOutput.model_validate_json(raw)
        assert parsed.channel == "tiktok"

    def test_schema_generation(self):
        schema = ContentOutput.model_json_schema()
        assert "variants" in schema["properties"]


class TestCommentReply:
    def test_construction(self):
        r = CommentReply(
            comment_id="c1",
            original_comment="Love this track!",
            reply="Thank you so much! Means a lot.",
            tone="grateful",
            requires_approval=False,
        )
        assert r.tone == "grateful"
        assert not r.requires_approval

    def test_flagged_for_approval(self):
        r = CommentReply(
            comment_id="c2",
            original_comment="Can you come play at my wedding?",
            reply="That's awesome! DM me and let's talk.",
            tone="warm",
            requires_approval=True,
            approval_reason="Private event booking — needs human review",
        )
        assert r.requires_approval


class TestCommentReplyOutput:
    def test_empty(self):
        o = CommentReplyOutput()
        assert o.replies == []
        assert o.flagged_comments == []

    def test_with_replies(self):
        o = CommentReplyOutput(
            replies=[
                CommentReply(comment_id="c1", reply="Thanks!"),
                CommentReply(comment_id="c2", reply="Appreciate it!"),
            ],
            flagged_comments=["c3"],
        )
        assert len(o.replies) == 2
        assert len(o.flagged_comments) == 1


class TestHashtagOutput:
    def test_three_sets(self):
        h = HashtagOutput(
            sets=[
                HashtagSet(category="discovery", tags=["#CountryMusic", "#NewMusic"]),
                HashtagSet(category="niche", tags=["#IndieCountry", "#Americana"]),
                HashtagSet(category="campaign", tags=["#ForLoveOfCountry", "#DrewBaird"]),
            ],
        )
        assert len(h.sets) == 3
        assert h.sets[0].category == "discovery"


class TestEmailCopy:
    def test_construction(self):
        e = EmailCopy(
            subject="For Love of Country drops March 29",
            preview_text="14 tracks of real country music",
            body="Hey there, ...",
            cta_text="Pre-save now",
        )
        assert "March 29" in e.subject


# ═══════════════════════════════════════════════════════════════════
# PROMPT TEMPLATE TESTS
# ═══════════════════════════════════════════════════════════════════


class TestPlannerPrompts:
    def test_system_prompt_loads(self):
        from amplify.core.prompts.planner import SYSTEM_PROMPT
        assert "14-day" in SYSTEM_PROMPT or "14" in SYSTEM_PROMPT
        assert "content_gaps" in SYSTEM_PROMPT or "content gaps" in SYSTEM_PROMPT

    def test_plan_template_renders(self):
        from amplify.core.prompts.planner import PLAN_CAMPAIGN_TEMPLATE
        rendered = PLAN_CAMPAIGN_TEMPLATE.format(
            artist_name="Drew Baird",
            release_title="For Love of Country",
            release_type="album",
            release_date="2026-03-29",
            genre="Country",
            track_listing='["For Love of Country", "Whiskey Scars"]',
            destination_urls='{"linktree": "https://linktr.ee/dbmusic26"}',
            channels="- instagram\n- tiktok",
            calendar_items="[]",
            prior_metrics="{}",
            budget="No budget",
            today="2026-03-18",
        )
        assert "Drew Baird" in rendered
        assert "For Love of Country" in rendered

    def test_adjust_template_renders(self):
        from amplify.core.prompts.planner import ADJUST_PLAN_TEMPLATE
        rendered = ADJUST_PLAN_TEMPLATE.format(
            current_plan='{"campaign_name": "test"}',
            feedback="More TikTok content needed",
            updated_metrics="{}",
        )
        assert "TikTok" in rendered


class TestContentPrompts:
    def test_system_prompt_loads(self):
        from amplify.core.prompts.copywriter import SYSTEM_PROMPT
        assert "3 variants" in SYSTEM_PROMPT or "variant" in SYSTEM_PROMPT

    def test_caption_template_renders(self):
        from amplify.core.prompts.copywriter import CAPTION_TEMPLATE
        rendered = CAPTION_TEMPLATE.format(
            artist_name="Drew Baird",
            platform="instagram",
            content_type="teaser",
            release_title="For Love of Country",
            key_message="Album drops 3/29",
            cta_destination="https://linktr.ee/dbmusic26",
            tone="authentic",
            brand_voice="Country, genuine, storytelling",
            hashtags="#ForLoveOfCountry, #CountryMusic",
        )
        assert "Drew Baird" in rendered

    def test_hook_template_renders(self):
        from amplify.core.prompts.copywriter import HOOK_TEMPLATE
        rendered = HOOK_TEMPLATE.format(
            artist_name="Drew Baird",
            platform="tiktok",
            track_title="Whiskey Scars",
            hook_concept="Opening guitar riff with on-screen lyrics",
            cta="Link in bio",
            brand_voice="Country authentic",
        )
        assert "Whiskey Scars" in rendered

    def test_video_script_template_renders(self):
        from amplify.core.prompts.copywriter import VIDEO_SCRIPT_TEMPLATE
        rendered = VIDEO_SCRIPT_TEMPLATE.format(
            artist_name="Drew Baird",
            platform="tiktok",
            concept="Behind the scenes of recording",
            track_title="Boat Problems",
            visual_style="raw, studio footage",
            cta_destination="https://linktr.ee/dbmusic26",
            brand_voice="Genuine, down to earth",
        )
        assert "Boat Problems" in rendered

    def test_comment_reply_template_renders(self):
        from amplify.core.prompts.copywriter import COMMENT_REPLY_TEMPLATE
        rendered = COMMENT_REPLY_TEMPLATE.format(
            artist_name="Drew Baird",
            brand_voice="Warm, grateful",
            comments="- [c1] Love this!\n- [c2] When's the album?",
        )
        assert "Love this!" in rendered

    def test_hashtag_template_renders(self):
        from amplify.core.prompts.copywriter import HASHTAG_TEMPLATE
        rendered = HASHTAG_TEMPLATE.format(
            artist_name="Drew Baird",
            genre="Country",
            platform="instagram",
            release_title="For Love of Country",
            campaign_hashtags="#ForLoveOfCountry",
        )
        assert "Country" in rendered


# ═══════════════════════════════════════════════════════════════════
# PLANNER AGENT TESTS (mocked Claude)
# ═══════════════════════════════════════════════════════════════════


MOCK_PLANNER_RESPONSE = {
    "campaign_name": "For Love of Country Launch",
    "artist_id": "00000001",
    "release_id": "00000002",
    "plan_start": "2026-03-18",
    "plan_end": "2026-04-01",
    "daily_actions": [
        {
            "day": "2026-03-18",
            "platform": "instagram",
            "action_type": "reel",
            "content_brief": "15s clip of Whiskey Scars chorus with on-screen lyrics",
            "cta_destination": "https://linktr.ee/dbmusic26",
            "assets_required": ["whiskey_scars_clip.mp4"],
            "priority": "high",
            "track_reference": "Whiskey Scars",
        },
        {
            "day": "2026-03-19",
            "platform": "tiktok",
            "action_type": "post",
            "content_brief": "Hook-first clip of Caviar Cowboy",
            "cta_destination": "https://linktr.ee/dbmusic26",
            "assets_required": [],
            "priority": "high",
            "track_reference": "Caviar Cowboy",
        },
    ],
    "experiments": [
        {
            "name": "Hook Style A/B",
            "hypothesis": "Storytelling hooks outperform direct announcement hooks",
            "variants": ["storytelling", "direct"],
            "metric": "engagement_rate",
            "platform": "tiktok",
            "duration_days": 7,
        }
    ],
    "content_gaps": [
        {
            "platform": "youtube",
            "date_range": "2026-03-20 to 2026-03-22",
            "gap_type": "no_content",
            "suggestion": "Post a YouTube Short with preview of track 3",
        }
    ],
    "publish_recommendations": [],
    "kpis": [
        {"metric": "followers", "target": "+200", "platform": "instagram"}
    ],
    "budget_allocation": {},
    "notes": "Focus on countdown content.",
}


def _make_mock_runner(response_json: dict) -> AgentRunner:
    """Create an AgentRunner with a mocked ClaudeClient."""
    config = AgentConfig(api_key="test-key")
    runner = AgentRunner(config=config)

    async def mock_send_message(system, messages, tools=None, max_tokens=None):
        return {
            "content": [{"type": "text", "text": json.dumps(response_json)}],
            "stop_reason": "end_turn",
            "model": "claude-sonnet-4-20250514",
        }

    runner.client.send_message = mock_send_message
    return runner


TENANT = uuid.UUID("00000000-0000-0000-0000-000000000000")


class TestPlannerAgent:
    @pytest.mark.asyncio
    async def test_plan_campaign(self):
        runner = _make_mock_runner(MOCK_PLANNER_RESPONSE)
        agent = PlannerAgent(runner=runner)

        result = await agent.plan_campaign(
            tenant_id=TENANT,
            artist_name="Drew Baird",
            release_title="For Love of Country",
            release_type="album",
            release_date="2026-03-29",
            genre="Country",
            channels=["instagram", "tiktok", "youtube"],
            track_listing=["For Love of Country", "Whiskey Scars", "Caviar Cowboy"],
            destination_urls={"linktree": "https://linktr.ee/dbmusic26"},
        )

        assert isinstance(result, AgentResult)
        assert result.agent_name == "planner"
        assert result.structured is not None
        plan = result.structured
        assert isinstance(plan, PlannerOutput)
        assert plan.campaign_name == "For Love of Country Launch"
        assert len(plan.daily_actions) == 2
        assert plan.daily_actions[0].track_reference == "Whiskey Scars"
        assert len(plan.experiments) == 1
        assert len(plan.content_gaps) == 1

    @pytest.mark.asyncio
    async def test_plan_with_calendar_and_metrics(self):
        runner = _make_mock_runner(MOCK_PLANNER_RESPONSE)
        agent = PlannerAgent(runner=runner)

        result = await agent.plan_campaign(
            tenant_id=TENANT,
            artist_name="Drew Baird",
            release_title="For Love of Country",
            release_date=date(2026, 3, 29),
            genre="Country",
            channels=["instagram", "tiktok"],
            calendar_items=[
                {"date": "2026-03-15", "title": "Teaser post", "completed": True},
            ],
            prior_metrics={
                "instagram": {"engagement_rate": 0.045, "top_format": "reel"},
            },
            budget=500.0,
        )

        assert result.structured is not None

    @pytest.mark.asyncio
    async def test_adjust_plan(self):
        runner = _make_mock_runner(MOCK_PLANNER_RESPONSE)
        agent = PlannerAgent(runner=runner)

        result = await agent.adjust_plan(
            tenant_id=TENANT,
            current_plan=MOCK_PLANNER_RESPONSE,
            feedback="Add more YouTube content and reduce Instagram frequency",
            updated_metrics={"youtube": {"views": 1200}},
        )

        assert isinstance(result, AgentResult)
        assert result.structured is not None


# ═══════════════════════════════════════════════════════════════════
# CONTENT AGENT TESTS (mocked Claude)
# ═══════════════════════════════════════════════════════════════════


MOCK_CONTENT_RESPONSE = {
    "action_id": "act-001",
    "channel": "instagram",
    "content_type": "caption",
    "variants": [
        {
            "variant_id": "A",
            "headline": "The wait is almost over",
            "body": "For Love of Country drops March 29. 14 tracks of real country.",
            "hashtags": ["#ForLoveOfCountry", "#CountryMusic"],
            "cta": "Pre-save now — link in bio",
            "platform": "instagram",
            "content_type": "caption",
            "char_count": 78,
        },
        {
            "variant_id": "B",
            "headline": "3/29. Mark it.",
            "body": "My debut album is almost here. For Love of Country.",
            "hashtags": ["#ForLoveOfCountry", "#NewMusic2026"],
            "cta": "Link in bio to pre-save",
            "platform": "instagram",
            "content_type": "caption",
            "char_count": 62,
        },
        {
            "variant_id": "C",
            "headline": "I wrote these songs for you",
            "body": "For Love of Country. 14 stories. March 29.",
            "hashtags": ["#ForLoveOfCountry", "#DrewBaird"],
            "cta": "Pre-save at linktr.ee/dbmusic26",
            "platform": "instagram",
            "content_type": "caption",
            "char_count": 55,
        },
    ],
    "brand_voice_applied": "Country authentic",
    "notes": "Variant A is informative, B is punchy, C is emotional.",
}

MOCK_HASHTAG_RESPONSE = {
    "sets": [
        {"category": "discovery", "tags": ["#CountryMusic", "#NewMusic", "#Country"]},
        {"category": "niche", "tags": ["#IndieCountry", "#Americana", "#CountrySongwriter"]},
        {"category": "campaign", "tags": ["#ForLoveOfCountry", "#DrewBaird"]},
    ],
    "notes": "Mix 2-3 from each set per post.",
}

MOCK_COMMENT_REPLY_RESPONSE = {
    "replies": [
        {
            "comment_id": "c1",
            "original_comment": "Love this track!",
            "reply": "Thank you! Means a lot that you're listening.",
            "tone": "grateful",
            "requires_approval": False,
            "approval_reason": "",
        },
        {
            "comment_id": "c2",
            "original_comment": "Play at my bar?",
            "reply": "That'd be awesome! DM me the details.",
            "tone": "warm",
            "requires_approval": True,
            "approval_reason": "Booking request — needs human review",
        },
    ],
    "flagged_comments": [],
    "notes": "",
}


class TestContentAgent:
    @pytest.mark.asyncio
    async def test_generate_caption(self):
        runner = _make_mock_runner(MOCK_CONTENT_RESPONSE)
        agent = ContentAgent(runner=runner)

        result = await agent.generate_caption(
            tenant_id=TENANT,
            artist_name="Drew Baird",
            platform="instagram",
            content_type="teaser",
            release_title="For Love of Country",
            key_message="Album drops 3/29",
            cta_destination="https://linktr.ee/dbmusic26",
            brand_voice="Country, genuine, storytelling",
        )

        assert isinstance(result, AgentResult)
        assert result.structured is not None
        output = result.structured
        assert isinstance(output, ContentOutput)
        assert len(output.variants) == 3
        assert output.variants[0].variant_id == "A"
        assert output.variants[1].variant_id == "B"
        assert output.variants[2].variant_id == "C"
        # Every variant has a CTA
        for v in output.variants:
            assert v.cta != ""

    @pytest.mark.asyncio
    async def test_generate_caption_with_hashtags(self):
        runner = _make_mock_runner(MOCK_CONTENT_RESPONSE)
        agent = ContentAgent(runner=runner)

        result = await agent.generate_caption(
            tenant_id=TENANT,
            artist_name="Drew Baird",
            platform="instagram",
            content_type="announcement",
            release_title="For Love of Country",
            key_message="Album out now",
            hashtags=["#ForLoveOfCountry", "#CountryMusic"],
        )

        assert result.structured is not None

    @pytest.mark.asyncio
    async def test_generate_hook(self):
        runner = _make_mock_runner(MOCK_CONTENT_RESPONSE)
        agent = ContentAgent(runner=runner)

        result = await agent.generate_hook(
            tenant_id=TENANT,
            artist_name="Drew Baird",
            platform="tiktok",
            track_title="Whiskey Scars",
            hook_concept="Opening guitar riff with on-screen lyrics",
            cta="Link in bio",
            brand_voice="Country authentic",
        )

        assert result.structured is not None

    @pytest.mark.asyncio
    async def test_generate_video_script(self):
        runner = _make_mock_runner(MOCK_CONTENT_RESPONSE)
        agent = ContentAgent(runner=runner)

        result = await agent.generate_video_script(
            tenant_id=TENANT,
            artist_name="Drew Baird",
            platform="tiktok",
            concept="Behind the scenes of recording Boat Problems",
            track_title="Boat Problems",
            visual_style="raw studio footage",
            cta_destination="https://linktr.ee/dbmusic26",
        )

        assert result.structured is not None

    @pytest.mark.asyncio
    async def test_generate_hashtags(self):
        runner = _make_mock_runner(MOCK_HASHTAG_RESPONSE)
        agent = ContentAgent(runner=runner)

        result = await agent.generate_hashtags(
            tenant_id=TENANT,
            artist_name="Drew Baird",
            genre="Country",
            platform="instagram",
            release_title="For Love of Country",
            campaign_hashtags=["#ForLoveOfCountry", "#DrewBaird"],
        )

        assert result.structured is not None
        output = result.structured
        assert isinstance(output, HashtagOutput)
        assert len(output.sets) == 3
        categories = [s.category for s in output.sets]
        assert "discovery" in categories
        assert "niche" in categories
        assert "campaign" in categories

    @pytest.mark.asyncio
    async def test_generate_comment_replies(self):
        runner = _make_mock_runner(MOCK_COMMENT_REPLY_RESPONSE)
        agent = ContentAgent(runner=runner)

        result = await agent.generate_comment_replies(
            tenant_id=TENANT,
            artist_name="Drew Baird",
            comments=[
                {"id": "c1", "text": "Love this track!"},
                {"id": "c2", "text": "Play at my bar?"},
            ],
            brand_voice="Warm, grateful, human",
        )

        assert result.structured is not None
        output = result.structured
        assert isinstance(output, CommentReplyOutput)
        assert len(output.replies) == 2
        # Second reply should be flagged for approval
        assert output.replies[1].requires_approval is True

    @pytest.mark.asyncio
    async def test_generate_email(self):
        mock_email = {
            "subject": "For Love of Country drops March 29",
            "preview_text": "14 tracks of real country music",
            "body": "Hey there...",
            "cta_text": "Pre-save now",
        }
        runner = _make_mock_runner(mock_email)
        agent = ContentAgent(runner=runner)

        result = await agent.generate_email(
            tenant_id=TENANT,
            context={
                "artist": "Drew Baird",
                "release": "For Love of Country",
                "purpose": "pre-release announcement",
            },
        )

        assert result.structured is not None
        assert isinstance(result.structured, EmailCopy)

    @pytest.mark.asyncio
    async def test_generate_variants(self):
        runner = _make_mock_runner(MOCK_CONTENT_RESPONSE)
        agent = ContentAgent(runner=runner)

        result = await agent.generate_variants(
            tenant_id=TENANT,
            content="New album For Love of Country out March 29!",
            platform="instagram",
            count=3,
        )

        assert isinstance(result, AgentResult)

    @pytest.mark.asyncio
    async def test_brand_voice_passthrough(self):
        """Brand voice constraints are included in the prompt."""
        calls = []

        async def capture_send(system, messages, tools=None, max_tokens=None):
            calls.append({"system": system, "messages": messages})
            return {
                "content": [{"type": "text", "text": json.dumps(MOCK_CONTENT_RESPONSE)}],
                "stop_reason": "end_turn",
            }

        config = AgentConfig(api_key="test-key")
        runner = AgentRunner(config=config)
        runner.client.send_message = capture_send
        agent = ContentAgent(runner=runner)

        await agent.generate_caption(
            tenant_id=TENANT,
            artist_name="Drew Baird",
            platform="instagram",
            content_type="teaser",
            release_title="For Love of Country",
            key_message="Album drops 3/29",
            brand_voice="Country, genuine, never sarcastic, minimal emojis",
        )

        # The brand voice should appear in the user message
        assert len(calls) == 1
        user_msg = calls[0]["messages"][-1]["content"]
        assert "never sarcastic" in user_msg
        assert "minimal emojis" in user_msg
