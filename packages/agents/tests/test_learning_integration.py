"""Tests for tenant learning integration into planner and content agents.

Validates:
1. LearningContext construction and prompt formatting
2. Context injection into planner and content agents
3. Fallback behavior when no patterns exist
4. Audit trail completeness (decision records, snapshots)
5. Recommendation ID traceability
6. build_learning_context helper
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime

import pytest

from amplify.agents.runtime.agent_runner import AgentContext, AgentResult, AgentRunner, AuditSink
from amplify.agents.runtime.config import AgentConfig
from amplify.agents.learning_context import (
    AgentDecisionRecord,
    LearnedPreference,
    LearningContext,
    build_learning_context,
)
from amplify.agents.subagents.planner_agent import PlannerAgent, PlannerOutput
from amplify.agents.subagents.content_agent import ContentAgent, ContentOutput

TENANT = uuid.UUID("00000000-0000-0000-0000-000000000000")


# ── Fixtures ──────────────────────────────────────────────────────


def _sample_learning_context(
    with_recommendations: bool = True,
    with_avoid: bool = True,
) -> LearningContext:
    """Build a sample LearningContext for tests."""
    recs = []
    if with_recommendations:
        recs = [
            LearnedPreference(
                id="rec001",
                feature="content_hook_family",
                value="question",
                direction="winning",
                confidence=0.85,
                title="question (hook style) performs well",
                explanation="Posts using question hooks score 45% above average.",
                evidence_summary="45% lift over baseline from 12 posts",
            ),
            LearnedPreference(
                id="rec002",
                feature="post_hour",
                value="18",
                direction="winning",
                confidence=0.78,
                title="6:00 PM (posting hour) performs well",
                explanation="Posts at 6 PM score 30% above average.",
                evidence_summary="30% lift over baseline from 15 posts",
                is_pinned=True,
            ),
            LearnedPreference(
                id="rec003",
                feature="content_cta_type",
                value="presave",
                direction="winning",
                confidence=0.72,
                title="presave (call-to-action) performs well",
                explanation="Pre-save CTAs drive 25% more engagement.",
                evidence_summary="25% lift over baseline from 8 posts",
            ),
        ]

    avoid = []
    if with_avoid:
        avoid = [
            LearnedPreference(
                id="avoid01",
                feature="content_hook_family",
                value="statement",
                direction="losing",
                confidence=0.80,
                title="statement (hook style) underperforms",
                explanation="Statement hooks score 35% below average.",
                evidence_summary="35% below baseline from 10 posts",
            ),
        ]

    return LearningContext(
        snapshot_id="snap-test-001",
        tenant_id=str(TENANT),
        captured_at="2026-03-20T12:00:00",
        recommendations=recs,
        avoid=avoid,
        has_enough_data=True,
        total_observations=27,
        patterns_version=3,
    )


class RecordingAuditSink(AuditSink):
    """Captures audit entries for test assertions."""

    def __init__(self):
        self.entries: list[dict] = []

    async def log(self, **kwargs):
        self.entries.append(kwargs)


MOCK_PLANNER_RESPONSE = {
    "campaign_name": "Test Plan",
    "artist_id": "001",
    "release_id": "002",
    "plan_start": "2026-03-18",
    "plan_end": "2026-04-01",
    "daily_actions": [
        {
            "day": "2026-03-18",
            "platform": "instagram",
            "action_type": "reel",
            "content_brief": "Question hook: 'Ready for something new?' [rec001]",
            "cta_destination": "https://linktr.ee/test",
            "priority": "high",
        },
    ],
    "experiments": [
        {
            "name": "Hook A/B",
            "hypothesis": "Question hooks outperform direct [rec001]",
            "variants": ["question", "direct"],
            "metric": "engagement_rate",
            "platform": "tiktok",
        }
    ],
    "content_gaps": [],
    "publish_recommendations": [],
    "kpis": [],
    "budget_allocation": {},
    "notes": "Applied learned preferences: question hooks [rec001], 6pm posting [rec002].",
    "recommendations_applied": ["rec001", "rec002"],
}

MOCK_CONTENT_RESPONSE = {
    "action_id": "act-001",
    "channel": "instagram",
    "content_type": "caption",
    "variants": [
        {
            "variant_id": "A",
            "headline": "Ready for something new? [rec001]",
            "body": "New music dropping soon.",
            "cta": "Pre-save now [rec003]",
            "platform": "instagram",
            "content_type": "caption",
            "char_count": 50,
        },
        {
            "variant_id": "B",
            "headline": "Have you heard?",
            "body": "Something's coming...",
            "cta": "Link in bio",
            "platform": "instagram",
            "content_type": "caption",
            "char_count": 40,
        },
        {
            "variant_id": "C",
            "headline": "What if I told you...",
            "body": "The wait is almost over.",
            "cta": "Pre-save — link in bio",
            "platform": "instagram",
            "content_type": "caption",
            "char_count": 45,
        },
    ],
    "brand_voice_applied": "Authentic country",
    "notes": "Used question hook style [rec001] and presave CTA [rec003].",
    "recommendations_applied": ["rec001", "rec003"],
}


def _make_mock_runner(response_json: dict, audit: AuditSink | None = None) -> AgentRunner:
    config = AgentConfig(api_key="test-key")
    runner = AgentRunner(config=config, audit=audit)

    async def mock_send(system, messages, tools=None, max_tokens=None):
        return {
            "content": [{"type": "text", "text": json.dumps(response_json)}],
            "stop_reason": "end_turn",
            "model": "claude-sonnet-4-20250514",
        }

    runner.client.send_message = mock_send
    return runner


# ═══════════════════════════════════════════════════════════════════
# LearningContext unit tests
# ═══════════════════════════════════════════════════════════════════


class TestLearningContext:
    def test_empty_context(self):
        lc = LearningContext()
        assert lc.is_empty()
        assert lc.recommendation_ids() == []
        assert lc.avoid_ids() == []

    def test_with_recommendations(self):
        lc = _sample_learning_context()
        assert not lc.is_empty()
        assert len(lc.recommendation_ids()) == 3
        assert len(lc.avoid_ids()) == 1

    def test_format_for_prompt_empty(self):
        lc = LearningContext()
        text = lc.format_for_prompt()
        assert "No learned preferences" in text
        assert "general best practices" in text

    def test_format_for_prompt_with_data(self):
        lc = _sample_learning_context()
        text = lc.format_for_prompt()
        assert "## Tenant Learning" in text
        assert "27 posts" in text
        assert "[rec001]" in text
        assert "[rec002]" in text
        assert "[PINNED by admin]" in text
        assert "Patterns to avoid" in text
        assert "[avoid01]" in text
        assert "Reference recommendation IDs" in text

    def test_format_includes_confidence(self):
        lc = _sample_learning_context()
        text = lc.format_for_prompt()
        assert "85%" in text  # rec001 confidence
        assert "78%" in text  # rec002 confidence

    def test_to_audit_snapshot(self):
        lc = _sample_learning_context()
        snap = lc.to_audit_snapshot()
        assert snap["snapshot_id"] == "snap-test-001"
        assert snap["tenant_id"] == str(TENANT)
        assert len(snap["recommendation_ids"]) == 3
        assert len(snap["avoid_ids"]) == 1
        assert snap["has_enough_data"] is True
        assert snap["total_observations"] == 27

    def test_recommendation_ids(self):
        lc = _sample_learning_context()
        ids = lc.recommendation_ids()
        assert "rec001" in ids
        assert "rec002" in ids
        assert "rec003" in ids


class TestLearnedPreference:
    def test_auto_id(self):
        p = LearnedPreference(feature="x", value="y")
        assert len(p.id) == 8  # short UUID prefix

    def test_explicit_id(self):
        p = LearnedPreference(id="custom", feature="x", value="y")
        assert p.id == "custom"


class TestAgentDecisionRecord:
    def test_serialization(self):
        rec = AgentDecisionRecord(
            learning_snapshot_id="snap-001",
            recommendations_available=["rec001", "rec002"],
            recommendations_applied=["rec001"],
            recommendations_ignored=["rec002"],
            fallback_used=False,
        )
        d = rec.to_dict()
        assert d["learning_snapshot_id"] == "snap-001"
        assert d["recommendations_applied"] == ["rec001"]
        assert d["fallback_used"] is False

    def test_fallback_mode(self):
        rec = AgentDecisionRecord(fallback_used=True)
        assert rec.fallback_used is True
        assert rec.recommendations_available == []


class TestBuildLearningContext:
    def test_from_service_data(self):
        patterns = [
            {
                "subject_feature": "content_hook_family",
                "subject_value": "statement",
                "direction": "losing",
                "confidence": 0.8,
                "title": "statement underperforms",
                "explanation": "Below average.",
                "is_active": True,
                "evidence": {"lift_vs_baseline": -35, "raw_count": 10},
            },
        ]
        recommendations = [
            {
                "feature": "content_hook_family",
                "recommended_value": "question",
                "title": "Use question hooks",
                "explanation": "45% above average.",
                "confidence": 0.85,
                "evidence": {"lift_vs_baseline": 45, "raw_count": 12, "pinned": False},
            },
        ]
        data_status = {
            "feature_vectors": 20,
            "outcomes": 15,
            "active_patterns": 5,
            "min_required": 5,
            "has_enough_data": True,
        }

        lc = build_learning_context(
            tenant_id=str(TENANT),
            patterns=patterns,
            recommendations=recommendations,
            data_status=data_status,
        )

        assert not lc.is_empty()
        assert len(lc.recommendations) == 1
        assert lc.recommendations[0].value == "question"
        assert len(lc.avoid) == 1
        assert lc.avoid[0].value == "statement"
        assert lc.has_enough_data is True
        assert lc.total_observations == 20

    def test_empty_data(self):
        lc = build_learning_context(
            tenant_id=str(TENANT),
            patterns=[],
            recommendations=[],
            data_status={"feature_vectors": 0, "outcomes": 0, "has_enough_data": False},
        )
        assert lc.is_empty()
        assert not lc.has_enough_data


# ═══════════════════════════════════════════════════════════════════
# Planner agent learning integration tests
# ═══════════════════════════════════════════════════════════════════


class TestPlannerLearningIntegration:
    @pytest.mark.asyncio
    async def test_context_injection(self):
        """Learning context is injected into the system prompt."""
        captured_prompts = []

        async def capture_send(system, messages, tools=None, max_tokens=None):
            captured_prompts.append(system)
            return {
                "content": [{"type": "text", "text": json.dumps(MOCK_PLANNER_RESPONSE)}],
                "stop_reason": "end_turn",
            }

        config = AgentConfig(api_key="test-key")
        runner = AgentRunner(config=config)
        runner.client.send_message = capture_send
        agent = PlannerAgent(runner=runner)
        lc = _sample_learning_context()

        await agent.plan_campaign(
            tenant_id=TENANT,
            artist_name="Test Artist",
            release_title="Test Release",
            release_date="2026-03-29",
            genre="Country",
            channels=["instagram", "tiktok"],
            learning_context=lc,
        )

        assert len(captured_prompts) == 1
        prompt = captured_prompts[0]
        # Learning context should be in the system prompt
        assert "Tenant Learning" in prompt
        assert "[rec001]" in prompt
        assert "[rec002]" in prompt
        assert "question" in prompt
        assert "6:00 PM" in prompt
        assert "Patterns to avoid" in prompt
        assert "[avoid01]" in prompt

    @pytest.mark.asyncio
    async def test_fallback_when_no_patterns(self):
        """When no learning context, fallback message is injected."""
        captured_prompts = []

        async def capture_send(system, messages, tools=None, max_tokens=None):
            captured_prompts.append(system)
            return {
                "content": [{"type": "text", "text": json.dumps(MOCK_PLANNER_RESPONSE)}],
                "stop_reason": "end_turn",
            }

        config = AgentConfig(api_key="test-key")
        runner = AgentRunner(config=config)
        runner.client.send_message = capture_send
        agent = PlannerAgent(runner=runner)

        result = await agent.plan_campaign(
            tenant_id=TENANT,
            artist_name="Test Artist",
            release_title="Test Release",
            release_date="2026-03-29",
            genre="Country",
            channels=["instagram"],
            # No learning_context provided
        )

        assert len(captured_prompts) == 1
        prompt = captured_prompts[0]
        assert "No learned preferences" in prompt
        assert "general best practices" in prompt
        # Decision record should indicate fallback
        assert result.learning_decision is not None
        assert result.learning_decision.fallback_used is True

    @pytest.mark.asyncio
    async def test_decision_record_populated(self):
        """AgentResult includes a decision record tracking which recs were used."""
        runner = _make_mock_runner(MOCK_PLANNER_RESPONSE)
        agent = PlannerAgent(runner=runner)
        lc = _sample_learning_context()

        result = await agent.plan_campaign(
            tenant_id=TENANT,
            artist_name="Test Artist",
            release_title="Test Release",
            release_date="2026-03-29",
            genre="Country",
            channels=["instagram"],
            learning_context=lc,
        )

        assert result.learning_decision is not None
        dec = result.learning_decision
        assert dec.learning_snapshot_id == "snap-test-001"
        assert len(dec.recommendations_available) == 4  # 3 recs + 1 avoid
        # rec001 and rec002 appear in the mock response text
        assert "rec001" in dec.recommendations_applied
        assert "rec002" in dec.recommendations_applied
        assert dec.fallback_used is False

    @pytest.mark.asyncio
    async def test_learning_snapshot_attached(self):
        """AgentResult includes the learning snapshot for audit."""
        runner = _make_mock_runner(MOCK_PLANNER_RESPONSE)
        agent = PlannerAgent(runner=runner)
        lc = _sample_learning_context()

        result = await agent.plan_campaign(
            tenant_id=TENANT,
            artist_name="Test Artist",
            release_title="Test Release",
            release_date="2026-03-29",
            genre="Country",
            channels=["instagram"],
            learning_context=lc,
        )

        assert result.learning_snapshot is not None
        snap = result.learning_snapshot
        assert snap["snapshot_id"] == "snap-test-001"
        assert snap["total_observations"] == 27
        assert len(snap["recommendation_ids"]) == 3

    @pytest.mark.asyncio
    async def test_audit_records_logged(self):
        """Audit sink receives run started/completed entries."""
        audit = RecordingAuditSink()
        runner = _make_mock_runner(MOCK_PLANNER_RESPONSE, audit=audit)
        agent = PlannerAgent(runner=runner)

        await agent.plan_campaign(
            tenant_id=TENANT,
            artist_name="Test",
            release_title="Test",
            release_date="2026-03-29",
            genre="Country",
            channels=["instagram"],
            learning_context=_sample_learning_context(),
        )

        actions = [e["action"] for e in audit.entries]
        assert "agent.planner.started" in actions
        assert "agent.planner.completed" in actions

    @pytest.mark.asyncio
    async def test_adjust_plan_with_learning(self):
        """adjust_plan also accepts and uses learning context."""
        captured = []

        async def capture_send(system, messages, tools=None, max_tokens=None):
            captured.append(system)
            return {
                "content": [{"type": "text", "text": json.dumps(MOCK_PLANNER_RESPONSE)}],
                "stop_reason": "end_turn",
            }

        config = AgentConfig(api_key="test-key")
        runner = AgentRunner(config=config)
        runner.client.send_message = capture_send
        agent = PlannerAgent(runner=runner)

        result = await agent.adjust_plan(
            tenant_id=TENANT,
            current_plan=MOCK_PLANNER_RESPONSE,
            feedback="More TikTok",
            learning_context=_sample_learning_context(),
        )

        assert "Tenant Learning" in captured[0]
        assert result.learning_decision is not None


# ═══════════════════════════════════════════════════════════════════
# Content agent learning integration tests
# ═══════════════════════════════════════════════════════════════════


class TestContentLearningIntegration:
    @pytest.mark.asyncio
    async def test_caption_context_injection(self):
        """Learning context is injected into content agent system prompt."""
        captured_prompts = []

        async def capture_send(system, messages, tools=None, max_tokens=None):
            captured_prompts.append(system)
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
            artist_name="Test Artist",
            platform="instagram",
            content_type="teaser",
            release_title="Test Release",
            key_message="Album drops soon",
            learning_context=_sample_learning_context(),
        )

        assert len(captured_prompts) == 1
        prompt = captured_prompts[0]
        assert "Tenant Learning" in prompt
        assert "question" in prompt
        assert "[rec001]" in prompt
        assert "presave" in prompt

    @pytest.mark.asyncio
    async def test_caption_fallback_no_context(self):
        """Without learning context, fallback message appears."""
        captured = []

        async def capture_send(system, messages, tools=None, max_tokens=None):
            captured.append(system)
            return {
                "content": [{"type": "text", "text": json.dumps(MOCK_CONTENT_RESPONSE)}],
                "stop_reason": "end_turn",
            }

        config = AgentConfig(api_key="test-key")
        runner = AgentRunner(config=config)
        runner.client.send_message = capture_send
        agent = ContentAgent(runner=runner)

        result = await agent.generate_caption(
            tenant_id=TENANT,
            artist_name="Test",
            platform="instagram",
            content_type="teaser",
            release_title="Test",
            key_message="Test",
        )

        assert "No learned preferences" in captured[0]
        assert result.learning_decision.fallback_used is True

    @pytest.mark.asyncio
    async def test_content_decision_record(self):
        """Content agent populates decision record."""
        runner = _make_mock_runner(MOCK_CONTENT_RESPONSE)
        agent = ContentAgent(runner=runner)
        lc = _sample_learning_context()

        result = await agent.generate_caption(
            tenant_id=TENANT,
            artist_name="Test",
            platform="instagram",
            content_type="teaser",
            release_title="Test",
            key_message="Test",
            learning_context=lc,
        )

        assert result.learning_decision is not None
        dec = result.learning_decision
        assert "rec001" in dec.recommendations_applied
        assert "rec003" in dec.recommendations_applied
        assert dec.fallback_used is False

    @pytest.mark.asyncio
    async def test_hook_with_learning(self):
        """generate_hook also accepts learning context."""
        captured = []

        async def capture_send(system, messages, tools=None, max_tokens=None):
            captured.append(system)
            return {
                "content": [{"type": "text", "text": json.dumps(MOCK_CONTENT_RESPONSE)}],
                "stop_reason": "end_turn",
            }

        config = AgentConfig(api_key="test-key")
        runner = AgentRunner(config=config)
        runner.client.send_message = capture_send
        agent = ContentAgent(runner=runner)

        result = await agent.generate_hook(
            tenant_id=TENANT,
            artist_name="Test",
            platform="tiktok",
            track_title="Test Track",
            hook_concept="Opening line question",
            learning_context=_sample_learning_context(),
        )

        assert "Tenant Learning" in captured[0]
        assert result.learning_decision is not None

    @pytest.mark.asyncio
    async def test_content_snapshot_attached(self):
        """Content result includes learning snapshot for audit."""
        runner = _make_mock_runner(MOCK_CONTENT_RESPONSE)
        agent = ContentAgent(runner=runner)
        lc = _sample_learning_context()

        result = await agent.generate_caption(
            tenant_id=TENANT,
            artist_name="Test",
            platform="instagram",
            content_type="teaser",
            release_title="Test",
            key_message="Test",
            learning_context=lc,
        )

        assert result.learning_snapshot is not None
        assert result.learning_snapshot["snapshot_id"] == "snap-test-001"

    @pytest.mark.asyncio
    async def test_methods_without_learning_still_work(self):
        """All content methods work without learning_context (backward compat)."""
        runner = _make_mock_runner(MOCK_CONTENT_RESPONSE)
        agent = ContentAgent(runner=runner)

        # generate_caption without learning_context
        r1 = await agent.generate_caption(
            tenant_id=TENANT,
            artist_name="Test",
            platform="instagram",
            content_type="teaser",
            release_title="Test",
            key_message="Test",
        )
        assert r1.structured is not None

        # generate_hook without learning_context
        r2 = await agent.generate_hook(
            tenant_id=TENANT,
            artist_name="Test",
            platform="tiktok",
            track_title="Track",
            hook_concept="Concept",
        )
        assert r2.structured is not None

    @pytest.mark.asyncio
    async def test_pinned_preference_in_prompt(self):
        """Pinned preferences are marked in the prompt."""
        captured = []

        async def capture_send(system, messages, tools=None, max_tokens=None):
            captured.append(system)
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
            artist_name="Test",
            platform="instagram",
            content_type="teaser",
            release_title="Test",
            key_message="Test",
            learning_context=_sample_learning_context(),
        )

        prompt = captured[0]
        assert "[PINNED by admin]" in prompt


# ═══════════════════════════════════════════════════════════════════
# Cross-cutting: structured output schema tests
# ═══════════════════════════════════════════════════════════════════


class TestOutputSchemas:
    def test_planner_output_includes_recommendations_applied(self):
        """PlannerOutput schema includes recommendations_applied field."""
        schema = PlannerOutput.model_json_schema()
        assert "recommendations_applied" in schema["properties"]

    def test_content_output_includes_recommendations_applied(self):
        """ContentOutput schema includes recommendations_applied field."""
        schema = ContentOutput.model_json_schema()
        assert "recommendations_applied" in schema["properties"]

    def test_planner_output_roundtrip_with_recs(self):
        p = PlannerOutput(
            campaign_name="Test",
            recommendations_applied=["rec001", "rec002"],
        )
        raw = p.model_dump_json()
        parsed = PlannerOutput.model_validate_json(raw)
        assert parsed.recommendations_applied == ["rec001", "rec002"]

    def test_content_output_roundtrip_with_recs(self):
        c = ContentOutput(
            channel="instagram",
            recommendations_applied=["rec001"],
        )
        raw = c.model_dump_json()
        parsed = ContentOutput.model_validate_json(raw)
        assert parsed.recommendations_applied == ["rec001"]

    def test_agent_result_has_learning_fields(self):
        r = AgentResult(
            run_id=uuid.uuid4(),
            agent_name="test",
        )
        assert r.learning_decision is None
        assert r.learning_snapshot is None
