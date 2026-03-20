"""Claude API client wrapper with structured output support."""

from __future__ import annotations

import json
from typing import Any, TypeVar

import structlog
from anthropic import AsyncAnthropic
from pydantic import BaseModel

from amplify.agents.runtime.config import AgentConfig

log = structlog.get_logger(__name__)

T = TypeVar("T", bound=BaseModel)


class ClaudeClient:
    """Async wrapper around the Anthropic SDK.

    Supports:
    - Plain text messages
    - Tool-use agentic loops
    - Structured outputs via Pydantic models
    - Configurable model selection
    """

    def __init__(self, config: AgentConfig | None = None) -> None:
        self.config = config or AgentConfig()
        if self.config.api_key:
            self._client = AsyncAnthropic(api_key=self.config.api_key)
        else:
            log.warning("anthropic_api_key_missing")
            self._client = None

    @property
    def model(self) -> str:
        return self.config.model

    async def send_message(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
    ) -> dict:
        """Send a single message to Claude and return the raw response dict."""
        if self._client is None:
            return {"error": "Anthropic API key is not configured."}

        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": max_tokens or self.config.max_tokens,
            "system": system,
            "messages": messages,
        }
        if self.config.temperature > 0:
            kwargs["temperature"] = self.config.temperature
        if tools:
            kwargs["tools"] = tools

        try:
            response = await self._client.messages.create(**kwargs)
            return response.model_dump()
        except Exception as exc:
            log.error("claude_send_error", error=str(exc))
            return {"error": str(exc)}

    async def send_structured(
        self,
        system: str,
        messages: list[dict],
        output_type: type[T],
        max_tokens: int | None = None,
    ) -> T | None:
        """Send a message and parse the response into a Pydantic model.

        Uses a JSON-mode prompt to request structured output, then validates
        against *output_type*. Returns None on parse failure.
        """
        schema = output_type.model_json_schema()
        structured_system = (
            f"{system}\n\n"
            f"You MUST respond with valid JSON matching this schema:\n"
            f"```json\n{json.dumps(schema, indent=2)}\n```\n"
            f"Do not include any text outside the JSON object."
        )

        response = await self.send_message(
            structured_system, messages, max_tokens=max_tokens
        )
        if "error" in response:
            log.error("structured_send_error", error=response["error"])
            return None

        text = _extract_text(response)
        return _parse_pydantic(text, output_type)

    async def send_with_tools(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        tool_executor: Any = None,
        max_iterations: int | None = None,
    ) -> dict:
        """Agentic tool-use loop.

        Sends messages to Claude. When the response contains tool_use blocks,
        executes them via *tool_executor* (a ToolRegistry instance), appends
        results, and loops until Claude returns a final text answer or
        *max_iterations* is exhausted.

        Returns the final response dict plus a list of tool calls made.
        """
        if self._client is None:
            return {"error": "Anthropic API key is not configured."}

        max_iter = max_iterations or self.config.max_tool_iterations
        conversation = list(messages)
        all_tool_calls: list[dict] = []
        response: dict = {}

        for iteration in range(max_iter):
            log.debug("tool_loop_iteration", iteration=iteration)
            response = await self.send_message(system, conversation, tools=tools)

            if "error" in response:
                response["tool_calls"] = all_tool_calls
                return response

            stop_reason = response.get("stop_reason")
            tool_use_blocks = [
                b for b in response.get("content", []) if b.get("type") == "tool_use"
            ]

            if stop_reason != "tool_use" or not tool_use_blocks:
                response["tool_calls"] = all_tool_calls
                return response

            # Record assistant message
            conversation.append({"role": "assistant", "content": response["content"]})

            # Execute tools
            tool_results: list[dict] = []
            for block in tool_use_blocks:
                tool_name = block["name"]
                tool_input = block["input"]
                tool_use_id = block["id"]

                call_record = {
                    "tool": tool_name,
                    "input": tool_input,
                    "iteration": iteration,
                }

                if tool_executor is not None:
                    try:
                        result = await tool_executor.execute(tool_name, tool_input)
                        call_record["output"] = str(result)[:2000]
                        call_record["status"] = "success"
                    except Exception as exc:
                        result = f"Error executing {tool_name}: {exc}"
                        call_record["output"] = str(exc)[:500]
                        call_record["status"] = "error"
                        log.error("tool_exec_error", tool=tool_name, error=str(exc))
                else:
                    result = f"No tool executor configured — cannot run {tool_name}."
                    call_record["status"] = "skipped"

                all_tool_calls.append(call_record)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": str(result),
                })

            conversation.append({"role": "user", "content": tool_results})

        log.warning("tool_loop_max_iterations", max=max_iter)
        response["tool_calls"] = all_tool_calls
        return response


# ── Helpers ──────────────────────────────────────────────────────


def _extract_text(response: dict) -> str:
    """Pull the first text block from a Claude response."""
    for block in response.get("content", []):
        if block.get("type") == "text":
            return block["text"].strip()
    return ""


def _parse_pydantic(text: str, model_type: type[T]) -> T | None:
    """Parse text as JSON and validate against a Pydantic model."""
    # Try direct parse
    for candidate in _json_candidates(text):
        try:
            return model_type.model_validate_json(candidate)
        except Exception:
            continue
    log.warning("structured_parse_failed", model=model_type.__name__)
    return None


def _json_candidates(text: str) -> list[str]:
    """Extract possible JSON strings from text (handles markdown fences)."""
    candidates = [text]
    if "```" in text:
        parts = text.split("```")
        for i in range(1, len(parts), 2):
            inner = parts[i]
            if inner.startswith("json"):
                inner = inner[4:]
            candidates.insert(0, inner.strip())
    return candidates
