"""Tests for the agent runtime framework.

Tests the config, tool registry, memory, AgentRunner, structured outputs,
audit logging, and subagent wiring — all without requiring an Anthropic API key.
"""

from __future__ import annotations

import json
import os
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure no real API calls
os.environ.pop("ANTHROPIC_API_KEY", None)

from amplify.agents.runtime.config import AgentConfig
from amplify.agents.runtime.tool_registry import ToolRegistry
from amplify.agents.runtime.memory import AgentMemory
from amplify.agents.runtime.claude_client import ClaudeClient, _extract_text, _parse_pydantic, _json_candidates
from amplify.agents.runtime.agent_runner import AgentRunner, AgentContext, AgentResult, AuditSink
from amplify.agents.subagents.planner_agent import PlannerAgent, PlannerOutput
from amplify.agents.subagents.content_agent import ContentAgent, ContentOutput
from amplify.agents.subagents.publisher_agent import PublisherAgent, ValidationResult
from amplify.agents.subagents.community_agent import CommunityAgent, CommentAnalysis
from amplify.agents.subagents.analyst_agent import AnalystAgent, CampaignInsight

pytestmark = pytest.mark.asyncio

TENANT_ID = uuid.UUID("00000000-0000-0000-0000-000000000000")
USER_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")


# ── Helpers ──────────────────────────────────────────────────────


def _mock_response(text: str, stop_reason: str = "end_turn") -> dict:
    """Build a fake Claude response dict."""
    return {
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "model": "test-model",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


def _mock_tool_response(tool_name: str, tool_input: dict, tool_id: str = "tu_1") -> dict:
    """Build a fake Claude response with a tool_use block."""
    return {
        "content": [
            {"type": "tool_use", "id": tool_id, "name": tool_name, "input": tool_input},
        ],
        "stop_reason": "tool_use",
        "model": "test-model",
        "usage": {"input_tokens": 10, "output_tokens": 20},
    }


class RecordingAuditSink(AuditSink):
    """Captures audit entries for test assertions."""

    def __init__(self):
        self.entries: list[dict] = []

    async def log(self, **kwargs):
        self.entries[len(self.entries):] = [kwargs]


# ── Config tests ─────────────────────────────────────────────────


class TestAgentConfig:
    def test_defaults(self):
        config = AgentConfig()
        assert config.model == "claude-sonnet-4-20250514"
        assert config.max_tokens == 4096
        assert config.max_tool_iterations == 10
        assert config.temperature == 0.0
        assert config.audit_enabled is True
        assert config.dry_run is False

    def test_explicit_override(self):
        config = AgentConfig(model="claude-opus-4-20250514", max_tokens=8192)
        assert config.model == "claude-opus-4-20250514"
        assert config.max_tokens == 8192

    def test_env_override(self):
        with patch.dict(os.environ, {"AGENT_MODEL": "claude-haiku-4-5-20251001"}):
            config = AgentConfig()
            assert config.model == "claude-haiku-4-5-20251001"

    def test_for_agent(self):
        with patch.dict(os.environ, {"AGENT_PLANNER_MODEL": "claude-opus-4-20250514"}):
            config = AgentConfig.for_agent("planner")
            assert config.model == "claude-opus-4-20250514"

    def test_for_agent_with_overrides(self):
        config = AgentConfig.for_agent("planner", max_tokens=16000)
        assert config.max_tokens == 16000


# ── Tool Registry tests ─────────────────────────────────────────


class TestToolRegistry:
    def test_register_and_list(self):
        reg = ToolRegistry()
        reg.register(
            name="test_tool",
            description="A test tool",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
            handler=lambda x: x,
        )
        tools = reg.get_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "test_tool"

    async def test_execute_sync(self):
        reg = ToolRegistry()
        reg.register("add", "Add numbers", {"type": "object"}, handler=lambda a, b: int(a) + int(b))
        result = await reg.execute("add", {"a": "1", "b": "2"})
        assert result == 3

    async def test_execute_async(self):
        async def async_handler(msg):
            return f"echo: {msg}"

        reg = ToolRegistry()
        reg.register("echo", "Echo", {"type": "object"}, handler=async_handler)
        result = await reg.execute("echo", {"msg": "hello"})
        assert result == "echo: hello"

    async def test_execute_unknown_tool(self):
        reg = ToolRegistry()
        with pytest.raises(KeyError, match="not registered"):
            await reg.execute("missing", {})

    def test_register_adapter(self):
        class FakePublisher:
            async def publish_photo(self, image_url: str, caption: str) -> str:
                return "ok"

            async def publish_reel(self, video_url: str, caption: str) -> str:
                return "ok"

        reg = ToolRegistry()
        reg.register_adapter(FakePublisher())
        assert reg.has_tool("fake_publish_photo")
        assert reg.has_tool("fake_publish_reel")
        tools = reg.get_tools()
        assert len(tools) == 2

    def test_get_tools_by_tag(self):
        reg = ToolRegistry()
        reg.register("a", "A", {"type": "object"}, lambda: None, tags=["adapter", "ig"])
        reg.register("b", "B", {"type": "object"}, lambda: None, tags=["internal"])
        assert len(reg.get_tools_by_tag("adapter")) == 1
        assert len(reg.get_tools_by_tag("ig")) == 1
        assert len(reg.get_tools_by_tag("internal")) == 1


# ── Memory tests ─────────────────────────────────────────────────


class TestAgentMemory:
    def test_add_messages(self):
        mem = AgentMemory()
        mem.add_user("hello")
        mem.add_assistant("hi")
        msgs = mem.get_messages()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_trim(self):
        mem = AgentMemory(max_messages=3)
        mem.add_user("a")
        mem.add_assistant("b")
        mem.add_user("c")
        mem.add_assistant("d")
        msgs = mem.get_messages()
        assert len(msgs) == 3
        assert msgs[0]["content"] == "a"  # first preserved

    def test_token_estimate(self):
        mem = AgentMemory()
        mem.add_user("x" * 100)
        assert mem.token_estimate == 25

    def test_to_audit_summary(self):
        mem = AgentMemory()
        mem.add_user("request")
        mem.add_assistant("response")
        summary = mem.to_audit_summary()
        assert len(summary) == 2
        assert summary[0]["role"] == "user"

    def test_clear(self):
        mem = AgentMemory()
        mem.add_user("test")
        mem.clear()
        assert mem.is_empty


# ── Claude Client helpers ────────────────────────────────────────


class TestClientHelpers:
    def test_extract_text(self):
        resp = _mock_response("hello world")
        assert _extract_text(resp) == "hello world"

    def test_extract_text_empty(self):
        assert _extract_text({"content": []}) == ""

    def test_json_candidates_plain(self):
        candidates = _json_candidates('{"key": "value"}')
        assert len(candidates) == 1

    def test_json_candidates_fenced(self):
        text = "Here's the plan:\n```json\n{\"key\": \"value\"}\n```\nDone."
        candidates = _json_candidates(text)
        assert len(candidates) > 1
        assert '{"key": "value"}' in candidates[0]

    def test_parse_pydantic_valid(self):
        from pydantic import BaseModel

        class TestModel(BaseModel):
            name: str
            count: int

        result = _parse_pydantic('{"name": "test", "count": 5}', TestModel)
        assert result is not None
        assert result.name == "test"
        assert result.count == 5

    def test_parse_pydantic_fenced(self):
        from pydantic import BaseModel

        class M(BaseModel):
            x: int

        text = '```json\n{"x": 42}\n```'
        result = _parse_pydantic(text, M)
        assert result is not None
        assert result.x == 42

    def test_parse_pydantic_invalid(self):
        from pydantic import BaseModel

        class M(BaseModel):
            x: int

        result = _parse_pydantic("not json", M)
        assert result is None


# ── ClaudeClient (no API key) ────────────────────────────────────


class TestClaudeClientNoKey:
    def test_no_key_warning(self):
        config = AgentConfig(api_key="")
        client = ClaudeClient(config)
        assert client._client is None

    async def test_send_without_key_returns_error(self):
        client = ClaudeClient(AgentConfig(api_key=""))
        result = await client.send_message("sys", [{"role": "user", "content": "hi"}])
        assert "error" in result

    async def test_structured_without_key(self):
        from pydantic import BaseModel

        class M(BaseModel):
            x: int

        client = ClaudeClient(AgentConfig(api_key=""))
        result = await client.send_structured("sys", [{"role": "user", "content": "hi"}], M)
        assert result is None


# ── AgentRunner ──────────────────────────────────────────────────


class TestAgentRunner:
    async def test_run_basic(self):
        """Runner executes and returns AgentResult with audit entries."""
        audit = RecordingAuditSink()
        config = AgentConfig(api_key="test-key")
        client = ClaudeClient(config)

        # Mock the send_message
        client.send_message = AsyncMock(return_value=_mock_response('{"plan": "test"}'))

        runner = AgentRunner(config=config, client=client, audit=audit)
        ctx = AgentContext(
            tenant_id=TENANT_ID,
            user_id=USER_ID,
            agent_name="test",
            run_id=uuid.uuid4(),
        )

        result = await runner.run(
            agent_name="test",
            system_prompt="You are a test agent.",
            user_message="Do something",
            context=ctx,
        )

        assert isinstance(result, AgentResult)
        assert result.agent_name == "test"
        assert result.text == '{"plan": "test"}'
        assert result.data == {"plan": "test"}

        # Should have started + completed audit entries
        actions = [e["action"] for e in audit.entries]
        assert "agent.test.started" in actions
        assert "agent.test.completed" in actions

    async def test_run_with_structured_output(self):
        """Runner parses structured output into Pydantic model."""
        config = AgentConfig(api_key="test-key")
        client = ClaudeClient(config)

        plan_json = json.dumps({
            "campaign_name": "Album Launch",
            "artist_id": "123",
            "release_id": "456",
            "daily_actions": [],
            "experiments": [],
            "content_gaps": [],
            "kpis": [],
            "budget_allocation": {},
            "notes": "test plan",
        })
        client.send_message = AsyncMock(return_value=_mock_response(plan_json))

        runner = AgentRunner(config=config, client=client)
        ctx = AgentContext(
            tenant_id=TENANT_ID,
            agent_name="planner",
            run_id=uuid.uuid4(),
        )

        result = await runner.run(
            agent_name="planner",
            system_prompt="Plan.",
            user_message="Plan a campaign",
            context=ctx,
            output_type=PlannerOutput,
        )

        assert result.structured is not None
        assert isinstance(result.structured, PlannerOutput)
        assert result.structured.campaign_name == "Album Launch"

    async def test_run_with_tools(self):
        """Runner executes tool-use loop and records tool calls in audit."""
        audit = RecordingAuditSink()
        config = AgentConfig(api_key="test-key")
        client = ClaudeClient(config)

        # First response: tool_use, second response: final text
        client.send_message = AsyncMock(side_effect=[
            _mock_tool_response("lookup_artist", {"artist_id": "123"}),
            _mock_response("Found artist: Luna Vega"),
        ])

        reg = ToolRegistry()
        reg.register(
            "lookup_artist", "Look up artist",
            {"type": "object", "properties": {"artist_id": {"type": "string"}}},
            handler=AsyncMock(return_value="Luna Vega, electronic"),
        )

        runner = AgentRunner(config=config, client=client, audit=audit)
        ctx = AgentContext(
            tenant_id=TENANT_ID,
            agent_name="test",
            run_id=uuid.uuid4(),
        )

        result = await runner.run(
            agent_name="test",
            system_prompt="Test",
            user_message="Find artist",
            context=ctx,
            tools=reg,
        )

        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["tool"] == "lookup_artist"
        assert result.tool_calls[0]["status"] == "success"

        # Audit should have tool_call entry
        tool_call_entries = [e for e in audit.entries if "tool_call" in e["action"]]
        assert len(tool_call_entries) == 1

    async def test_run_error(self):
        """Runner handles API errors gracefully."""
        config = AgentConfig(api_key="test-key")
        client = ClaudeClient(config)
        client.send_message = AsyncMock(return_value={"error": "rate_limited"})

        runner = AgentRunner(config=config, client=client)
        ctx = AgentContext(
            tenant_id=TENANT_ID,
            agent_name="test",
            run_id=uuid.uuid4(),
        )

        result = await runner.run(
            agent_name="test",
            system_prompt="Test",
            user_message="Do thing",
            context=ctx,
        )

        assert result.text == "rate_limited"


# ── Subagent tests ───────────────────────────────────────────────


class TestPlannerAgent:
    async def test_plan_campaign(self):
        config = AgentConfig(api_key="test-key")
        client = ClaudeClient(config)

        plan = {
            "campaign_name": "Midnight Frequencies Launch",
            "artist_id": "a1",
            "release_id": "r1",
            "daily_actions": [
                {"day": "2026-03-20", "platform": "instagram", "action_type": "reel",
                 "content_brief": "Preview clip", "cta_destination": "https://linktr.ee/test"},
            ],
            "experiments": [],
            "content_gaps": [],
            "budget_allocation": {},
            "kpis": [{"metric": "streams", "target": "10000"}],
            "notes": "",
        }
        client.send_message = AsyncMock(return_value=_mock_response(json.dumps(plan)))

        runner = AgentRunner(config=config, client=client)
        agent = PlannerAgent(runner=runner)

        result = await agent.plan_campaign(
            tenant_id=TENANT_ID,
            artist_name="Luna Vega",
            release_title="Midnight Frequencies",
            release_date="2026-03-29",
            genre="electronic",
            channels=["instagram", "tiktok"],
        )

        assert result.structured is not None
        assert result.structured.campaign_name == "Midnight Frequencies Launch"
        assert len(result.structured.daily_actions) == 1


class TestContentAgent:
    async def test_generate_caption(self):
        config = AgentConfig(api_key="test-key")
        client = ClaudeClient(config)

        copy = {
            "action_id": "a1",
            "channel": "instagram",
            "content_type": "caption",
            "variants": [
                {"variant_id": "A", "body": "New music!", "cta": "Link in bio", "char_count": 10},
                {"variant_id": "B", "body": "Out now!", "cta": "Listen now", "char_count": 8},
                {"variant_id": "C", "body": "Drop!", "cta": "Tap to listen", "char_count": 5},
            ],
            "notes": "",
        }
        client.send_message = AsyncMock(return_value=_mock_response(json.dumps(copy)))

        runner = AgentRunner(config=config, client=client)
        agent = ContentAgent(runner=runner)

        result = await agent.generate_caption(
            tenant_id=TENANT_ID,
            artist_name="Luna Vega",
            platform="instagram",
            content_type="teaser",
            release_title="Midnight Frequencies",
            key_message="New album out soon",
        )

        assert result.structured is not None
        assert len(result.structured.variants) == 3


class TestPublisherAgent:
    def test_validate_post_valid(self):
        runner = AgentRunner(config=AgentConfig(api_key="test-key"))
        agent = PublisherAgent(runner=runner)

        result = agent.validate_post(
            {"text": "Hello world!", "destination_url": "https://example.com"},
            "instagram",
        )
        assert result.is_valid
        assert result.issues == []

    def test_validate_post_too_long(self):
        runner = AgentRunner(config=AgentConfig(api_key="test-key"))
        agent = PublisherAgent(runner=runner)

        result = agent.validate_post(
            {"text": "x" * 2500, "destination_url": "https://example.com"},
            "instagram",
        )
        assert not result.is_valid
        assert any("exceeds" in i for i in result.issues)

    def test_validate_post_missing_destination(self):
        runner = AgentRunner(config=AgentConfig(api_key="test-key"))
        agent = PublisherAgent(runner=runner)

        result = agent.validate_post({"text": "hello"}, "instagram")
        assert not result.is_valid
        assert any("destination" in i.lower() for i in result.issues)

    async def test_dry_run_publish(self):
        config = AgentConfig(api_key="test-key", dry_run=True)
        runner = AgentRunner(config=config)
        agent = PublisherAgent(runner=runner)

        result = await agent.execute_publish(
            tenant_id=TENANT_ID,
            post_id="p1",
            channel_id="c1",
            platform="instagram",
        )

        assert result.data["status"] == "dry_run"


class TestCommunityAgent:
    async def test_analyze_comment(self):
        config = AgentConfig(api_key="test-key")
        client = ClaudeClient(config)

        analysis = {
            "sentiment": "positive",
            "category": "praise",
            "suggested_action": "reply",
            "draft_reply": "Thank you!",
            "confidence": 0.95,
            "requires_approval": False,
        }
        client.send_message = AsyncMock(return_value=_mock_response(json.dumps(analysis)))

        runner = AgentRunner(config=config, client=client)
        agent = CommunityAgent(runner=runner)

        result = await agent.analyze_comment(
            tenant_id=TENANT_ID,
            comment={"text": "Love this track!", "author": "fan123"},
            artist_context={"name": "Luna Vega"},
        )

        assert result.structured is not None
        assert result.structured.sentiment == "positive"
        assert result.structured.confidence == 0.95


class TestAnalystAgent:
    async def test_analyze_campaign(self):
        config = AgentConfig(api_key="test-key")
        client = ClaudeClient(config)

        insight = {
            "summary": "Strong engagement",
            "score": 85.0,
            "insights": ["Reels outperforming static"],
            "recommendations": [
                {"content_id": "c1", "action": "keep", "reason": "High engagement", "priority": "high"},
                {"content_id": "c2", "action": "stop", "reason": "Low reach", "priority": "low"},
            ],
            "anomalies": [],
        }
        client.send_message = AsyncMock(return_value=_mock_response(json.dumps(insight)))

        runner = AgentRunner(config=config, client=client)
        agent = AnalystAgent(runner=runner)

        result = await agent.analyze_campaign(
            tenant_id=TENANT_ID,
            campaign_id="camp-1",
            metrics={"impressions": 50000, "engagement_rate": 0.05},
        )

        assert result.structured is not None
        assert result.structured.score == 85.0
        assert len(result.structured.recommendations) == 2
        assert result.structured.recommendations[0].action == "keep"
        assert result.structured.recommendations[1].action == "stop"


# ── Model selection tests ────────────────────────────────────────


class TestModelSelection:
    def test_config_model_propagates_to_client(self):
        config = AgentConfig(api_key="test", model="claude-opus-4-20250514")
        client = ClaudeClient(config)
        assert client.model == "claude-opus-4-20250514"

    def test_per_agent_model(self):
        with patch.dict(os.environ, {"AGENT_PLANNER_MODEL": "claude-opus-4-20250514"}):
            config = AgentConfig.for_agent("planner")
            assert config.model == "claude-opus-4-20250514"

        with patch.dict(os.environ, {"AGENT_CONTENT_MODEL": "claude-haiku-4-5-20251001"}):
            config = AgentConfig.for_agent("content")
            assert config.model == "claude-haiku-4-5-20251001"

    async def test_model_in_audit(self):
        audit = RecordingAuditSink()
        config = AgentConfig(api_key="test-key", model="claude-opus-4-20250514")
        client = ClaudeClient(config)
        client.send_message = AsyncMock(return_value=_mock_response("done"))

        runner = AgentRunner(config=config, client=client, audit=audit)
        ctx = AgentContext(tenant_id=TENANT_ID, agent_name="test", run_id=uuid.uuid4())

        result = await runner.run("test", "sys", "msg", ctx)
        assert result.model == "claude-opus-4-20250514"

        # Check audit records the model
        started = [e for e in audit.entries if "started" in e["action"]]
        assert started[0]["changes"]["model"] == "claude-opus-4-20250514"
