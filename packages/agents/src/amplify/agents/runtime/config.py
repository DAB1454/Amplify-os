"""Agent configuration — model selection, limits, and feature flags."""

from __future__ import annotations

import os
from typing import Any

from pydantic import BaseModel, Field


class AgentConfig(BaseModel):
    """Configuration for the agent runtime.

    Values are resolved from:
    1. Explicit kwargs at construction time
    2. Environment variables (AGENT_MODEL, ANTHROPIC_API_KEY, etc.)
    3. Defaults below
    """

    api_key: str = Field(default="")
    model: str = Field(default="claude-sonnet-4-6")
    max_tokens: int = Field(default=16384)
    max_tool_iterations: int = Field(default=10)
    max_memory_messages: int = Field(default=50)
    temperature: float = Field(default=0.0)
    audit_enabled: bool = Field(default=True)
    dry_run: bool = Field(default=False)

    def model_post_init(self, __context: Any) -> None:
        if not self.api_key:
            self.api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        env_model = os.environ.get("AGENT_MODEL")
        if env_model:
            self.model = env_model

    @classmethod
    def for_agent(cls, agent_name: str, **overrides: Any) -> AgentConfig:
        """Create config with agent-specific env overrides.

        Checks AGENT_{NAME}_MODEL, AGENT_{NAME}_MAX_TOKENS, etc.
        """
        prefix = f"AGENT_{agent_name.upper()}_"
        env_model = os.environ.get(f"{prefix}MODEL")
        env_max_tokens = os.environ.get(f"{prefix}MAX_TOKENS")

        kwargs: dict[str, Any] = {}
        if env_model:
            kwargs["model"] = env_model
        if env_max_tokens:
            kwargs["max_tokens"] = int(env_max_tokens)
        kwargs.update(overrides)
        return cls(**kwargs)
