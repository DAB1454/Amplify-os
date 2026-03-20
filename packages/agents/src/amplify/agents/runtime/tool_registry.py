"""Tool registry for the agent runtime.

All external actions (publishing, fetching analytics, etc.) are registered as
tools backed by adapter interfaces.  The registry enforces that agents never
make direct network calls — everything goes through a registered tool.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable

import structlog

log = structlog.get_logger(__name__)


class ToolRegistry:
    """Maps tool names to handlers and produces Anthropic-formatted definitions.

    Tools are the *only* way agents interact with external systems.
    Each tool wraps an adapter method so that agents remain network-agnostic.
    """

    def __init__(self) -> None:
        self._tools: dict[str, _ToolEntry] = {}

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict,
        handler: Callable,
        *,
        tags: list[str] | None = None,
    ) -> None:
        """Register a tool.

        Parameters
        ----------
        name:
            Unique tool name (e.g. ``"instagram_publish_photo"``).
        description:
            Human-readable description shown to the model.
        input_schema:
            JSON Schema object describing the tool's input parameters.
        handler:
            Async or sync callable.  Receives parsed input as **kwargs,
            returns a JSON-serialisable value.
        tags:
            Optional classification (e.g. ``["adapter", "instagram"]``).
        """
        if name in self._tools:
            log.warning("tool_already_registered", tool=name)
        self._tools[name] = _ToolEntry(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
            tags=tags or [],
        )
        log.info("tool_registered", tool=name, tags=tags)

    def register_adapter(
        self,
        adapter: Any,
        methods: list[str] | None = None,
    ) -> None:
        """Convenience: register all public async methods of an adapter object.

        Each method becomes a tool named ``{adapter_class}_{method_name}``
        with an auto-generated input schema based on type annotations.
        """
        cls_name = type(adapter).__name__.lower().replace("publisher", "").replace("adapter", "")
        for attr_name in dir(adapter):
            if attr_name.startswith("_"):
                continue
            if methods and attr_name not in methods:
                continue
            method = getattr(adapter, attr_name)
            if not callable(method):
                continue

            tool_name = f"{cls_name}_{attr_name}"
            sig = inspect.signature(method)
            properties: dict[str, Any] = {}
            required: list[str] = []
            for param_name, param in sig.parameters.items():
                if param_name == "self":
                    continue
                prop: dict[str, Any] = {"type": "string"}
                if param.annotation is int:
                    prop = {"type": "integer"}
                elif param.annotation is float:
                    prop = {"type": "number"}
                elif param.annotation is bool:
                    prop = {"type": "boolean"}
                properties[param_name] = prop
                if param.default is inspect.Parameter.empty:
                    required.append(param_name)

            self.register(
                name=tool_name,
                description=f"Adapter: {cls_name}.{attr_name}",
                input_schema={
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
                handler=method,
                tags=["adapter", cls_name],
            )

    def get_tools(self) -> list[dict]:
        """Return Anthropic-formatted tool definitions."""
        return [
            {
                "name": entry.name,
                "description": entry.description,
                "input_schema": entry.input_schema,
            }
            for entry in self._tools.values()
        ]

    def get_tools_by_tag(self, tag: str) -> list[dict]:
        """Return tool definitions filtered by tag."""
        return [
            {
                "name": e.name,
                "description": e.description,
                "input_schema": e.input_schema,
            }
            for e in self._tools.values()
            if tag in e.tags
        ]

    async def execute(self, tool_name: str, tool_input: dict) -> Any:
        """Look up a tool by name and invoke its handler.

        Sync handlers are executed in a thread pool.
        Returns the result (any JSON-serialisable value).
        """
        entry = self._tools.get(tool_name)
        if entry is None:
            raise KeyError(f"Tool '{tool_name}' is not registered.")

        log.debug("tool_execute", tool=tool_name)
        handler = entry.handler
        if inspect.iscoroutinefunction(handler):
            return await handler(**tool_input)
        return await asyncio.to_thread(handler, **tool_input)

    def has_tool(self, name: str) -> bool:
        return name in self._tools

    @property
    def tool_names(self) -> list[str]:
        return list(self._tools.keys())


class _ToolEntry:
    __slots__ = ("name", "description", "input_schema", "handler", "tags")

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict,
        handler: Callable,
        tags: list[str],
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.handler = handler
        self.tags = tags
