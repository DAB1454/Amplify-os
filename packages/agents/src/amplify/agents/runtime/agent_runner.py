"""Agent execution framework with audit logging.

AgentRunner is the single entry point for running any subagent.  It:

1. Loads the agent's system prompt, tools, and config
2. Runs the Claude tool-use loop
3. Optionally parses structured output into a Pydantic model
4. Persists every decision and tool call to the AuditLog
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, TypeVar

import structlog
from pydantic import BaseModel

from amplify.agents.runtime.claude_client import ClaudeClient, _extract_text, _parse_pydantic
from amplify.agents.runtime.config import AgentConfig
from amplify.agents.runtime.memory import AgentMemory
from amplify.agents.runtime.tool_registry import ToolRegistry
from amplify.core.policies.integrity import IntegrityPolicy

log = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class AgentContext(BaseModel):
    """Immutable context passed into every agent run."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID | None = None
    agent_name: str
    run_id: uuid.UUID


class IntegrityError(Exception):
    """Raised when agent output or tool call violates integrity policy."""

    def __init__(self, message: str, violations: list) -> None:
        super().__init__(message)
        self.violations = violations


class AgentResult(BaseModel):
    """Standard result returned by AgentRunner.run()."""

    run_id: uuid.UUID
    agent_name: str
    text: str = ""
    data: dict[str, Any] | None = None
    structured: Any | None = None
    tool_calls: list[dict[str, Any]] = []
    model: str = ""
    elapsed_ms: int = 0
    # Learning audit fields — populated by agents that consume LearningContext
    learning_decision: Any | None = None
    learning_snapshot: dict[str, Any] | None = None
    # Prompt lineage — which prompt version produced this output
    prompt_lineage: dict[str, Any] | None = None


class AuditSink:
    """Interface for persisting agent audit records.

    The default implementation is a no-op.  In production, inject the
    real AuditService (from apps/api) or any async-compatible sink.
    """

    async def log(
        self,
        *,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID | None,
        action: str,
        entity_type: str,
        entity_id: uuid.UUID | None = None,
        changes: dict[str, Any] | None = None,
    ) -> None:
        """Persist an audit entry.  Override in subclasses."""


class AgentRunner:
    """Orchestrates agent execution with tool use and audit logging.

    Usage::

        config = AgentConfig.for_agent("planner", model="claude-sonnet-4-20250514")
        runner = AgentRunner(config=config, audit=my_audit_sink)

        result = await runner.run(
            agent_name="planner",
            system_prompt=SYSTEM_PROMPT,
            user_message="Plan a campaign for ...",
            tools=registry,
            context=AgentContext(tenant_id=tid, agent_name="planner", run_id=uuid4()),
        )
    """

    def __init__(
        self,
        config: AgentConfig | None = None,
        client: ClaudeClient | None = None,
        audit: AuditSink | None = None,
    ) -> None:
        self.config = config or AgentConfig()
        self.client = client or ClaudeClient(self.config)
        self.audit = audit or AuditSink()

    async def run(
        self,
        agent_name: str,
        system_prompt: str,
        user_message: str,
        context: AgentContext,
        tools: ToolRegistry | None = None,
        memory: AgentMemory | None = None,
        output_type: type[T] | None = None,
        prompt_lineage: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Execute a full agent run.

        Parameters
        ----------
        agent_name:
            Human-readable agent identifier (e.g. "planner").
        system_prompt:
            The system prompt for this agent.
        user_message:
            The user's request.
        context:
            Tenant/user/run context for audit.
        tools:
            Optional ToolRegistry for tool-use loop.
        memory:
            Optional AgentMemory with prior conversation.
        output_type:
            If provided, parse the final response as this Pydantic model.

        Returns
        -------
        AgentResult with text, structured data, tool call log, and timing.
        """
        start = datetime.now(timezone.utc)
        run_id = context.run_id

        # Build messages
        mem = memory or AgentMemory(max_messages=self.config.max_memory_messages)
        mem.add_user(user_message)
        messages = mem.get_messages()

        # Audit: run started
        await self.audit.log(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            action=f"agent.{agent_name}.started",
            entity_type="agent_run",
            entity_id=run_id,
            changes={
                "model": self.config.model,
                "user_message": user_message[:500],
                "dry_run": self.config.dry_run,
            },
        )

        # Execute
        tool_calls: list[dict] = []
        response_text = ""
        response_data: dict[str, Any] | None = None

        if tools and tools.get_tools():
            response = await self.client.send_with_tools(
                system=system_prompt,
                messages=messages,
                tools=tools.get_tools(),
                tool_executor=tools,
                max_iterations=self.config.max_tool_iterations,
            )
            tool_calls = response.pop("tool_calls", [])
        else:
            response = await self.client.send_message(
                system=system_prompt,
                messages=messages,
            )

        if "error" in response:
            response_text = response["error"]
        else:
            response_text = _extract_text(response)
            # Record assistant reply in memory
            mem.add_assistant(response.get("content", response_text))

        # Structured output parsing
        structured = None
        if output_type and response_text:
            structured = _parse_pydantic(response_text, output_type)
            if structured:
                response_data = structured.model_dump()

        # If no structured parse, try raw JSON
        if response_data is None and response_text:
            try:
                parsed = json.loads(response_text)
                if isinstance(parsed, dict):
                    response_data = parsed
            except (json.JSONDecodeError, ValueError):
                pass

        elapsed = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)

        # ── Integrity gate: tool call arguments ───────────────────
        for call in tool_calls:
            tool_input = call.get("input", {})
            if isinstance(tool_input, dict):
                tc_result = IntegrityPolicy.check_tool_call(
                    call.get("tool", ""), tool_input,
                )
                if tc_result.blocked:
                    msg = IntegrityPolicy.format_violations(tc_result)
                    log.warning(
                        "integrity.tool_call_blocked",
                        agent=agent_name,
                        run_id=str(run_id),
                        tool=call.get("tool"),
                        violations=[v.rule for v in tc_result.violations],
                    )
                    await self.audit.log(
                        tenant_id=context.tenant_id,
                        user_id=context.user_id,
                        action=f"agent.{agent_name}.integrity_violation",
                        entity_type="agent_run",
                        entity_id=run_id,
                        changes={
                            "stage": "tool_call",
                            "tool": call.get("tool"),
                            "violations": [v.detail for v in tc_result.violations],
                        },
                    )
                    raise IntegrityError(msg, tc_result.violations)

        # ── Integrity gate: agent output text ─────────────────────
        if response_text:
            output_check = IntegrityPolicy.check_text(response_text)
            if output_check.blocked:
                msg = IntegrityPolicy.format_violations(output_check)
                log.warning(
                    "integrity.output_blocked",
                    agent=agent_name,
                    run_id=str(run_id),
                    violations=[v.rule for v in output_check.violations],
                )
                await self.audit.log(
                    tenant_id=context.tenant_id,
                    user_id=context.user_id,
                    action=f"agent.{agent_name}.integrity_violation",
                    entity_type="agent_run",
                    entity_id=run_id,
                    changes={
                        "stage": "output",
                        "violations": [v.detail for v in output_check.violations],
                        "response_preview": response_text[:300],
                    },
                )
                raise IntegrityError(msg, output_check.violations)

        # Audit: tool calls
        for call in tool_calls:
            await self.audit.log(
                tenant_id=context.tenant_id,
                user_id=context.user_id,
                action=f"agent.{agent_name}.tool_call",
                entity_type="agent_run",
                entity_id=run_id,
                changes={
                    "tool": call.get("tool"),
                    "input": call.get("input"),
                    "status": call.get("status"),
                    "iteration": call.get("iteration"),
                },
            )

        # Audit: run completed
        completion_changes: dict[str, Any] = {
            "model": self.config.model,
            "elapsed_ms": elapsed,
            "tool_calls_count": len(tool_calls),
            "has_structured_output": structured is not None,
            "response_preview": response_text[:300],
        }
        if prompt_lineage:
            completion_changes["prompt_version_id"] = prompt_lineage.get("prompt_version_id")
            completion_changes["prompt_version_number"] = prompt_lineage.get("prompt_version_number")
            completion_changes["prompt_resolution_path"] = prompt_lineage.get("resolution_path")

        await self.audit.log(
            tenant_id=context.tenant_id,
            user_id=context.user_id,
            action=f"agent.{agent_name}.completed",
            entity_type="agent_run",
            entity_id=run_id,
            changes=completion_changes,
        )

        return AgentResult(
            run_id=run_id,
            agent_name=agent_name,
            text=response_text,
            data=response_data,
            structured=structured,
            tool_calls=tool_calls,
            model=self.config.model,
            elapsed_ms=elapsed,
            prompt_lineage=prompt_lineage,
        )
