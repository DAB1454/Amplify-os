"""Agent conversation memory with automatic trimming."""

from __future__ import annotations

from typing import Any

import structlog

log = structlog.get_logger(__name__)


class AgentMemory:
    """Maintains conversation context for an agent session.

    Messages are stored in Anthropic API format.  When the message count
    exceeds *max_messages* the oldest messages are automatically trimmed
    (the first message is always preserved to retain initial context).
    """

    def __init__(self, max_messages: int = 50) -> None:
        self.max_messages = max_messages
        self._messages: list[dict] = []

    def add_user(self, content: str | list[dict]) -> None:
        """Append a user message (text or content blocks)."""
        self._messages.append({"role": "user", "content": content})
        self._trim()

    def add_assistant(self, content: str | list[dict]) -> None:
        """Append an assistant message (text or content blocks)."""
        self._messages.append({"role": "assistant", "content": content})
        self._trim()

    def add_tool_result(self, tool_use_id: str, result: str) -> None:
        """Append a tool result message."""
        self._messages.append({
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": result,
            }],
        })
        self._trim()

    def get_messages(self) -> list[dict]:
        """Return a copy of the message list."""
        return list(self._messages)

    def clear(self) -> None:
        self._messages.clear()

    @property
    def is_empty(self) -> bool:
        return len(self._messages) == 0

    @property
    def token_estimate(self) -> int:
        """Rough token estimate (chars / 4)."""
        total = 0
        for msg in self._messages:
            c = msg.get("content", "")
            if isinstance(c, str):
                total += len(c)
            elif isinstance(c, list):
                for block in c:
                    if isinstance(block, dict):
                        total += len(str(block.get("content", "") or block.get("text", "")))
        return total // 4

    def to_audit_summary(self) -> list[dict[str, Any]]:
        """Return a compact representation suitable for audit logging.

        Strips large content blocks to keep the audit entry small.
        """
        summary: list[dict[str, Any]] = []
        for msg in self._messages:
            role = msg["role"]
            content = msg.get("content", "")
            if isinstance(content, str):
                summary.append({"role": role, "text": content[:500]})
            elif isinstance(content, list):
                blocks = []
                for b in content:
                    if isinstance(b, dict):
                        block_type = b.get("type", "unknown")
                        if block_type == "text":
                            blocks.append({"type": "text", "text": b.get("text", "")[:300]})
                        elif block_type == "tool_use":
                            blocks.append({"type": "tool_use", "name": b.get("name")})
                        elif block_type == "tool_result":
                            blocks.append({"type": "tool_result", "id": b.get("tool_use_id")})
                summary.append({"role": role, "blocks": blocks})
        return summary

    def _trim(self) -> None:
        if len(self._messages) <= self.max_messages:
            return
        overflow = len(self._messages) - self.max_messages
        self._messages = [self._messages[0]] + self._messages[1 + overflow:]
        log.debug("memory_trimmed", removed=overflow, remaining=len(self._messages))
