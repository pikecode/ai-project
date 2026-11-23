"""Tool definitions and registry."""

import asyncio
import inspect
import json
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any


class BaseTool(ABC):
    """Base class for all tools."""

    name: str
    description: str
    parameters: dict

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """Execute the tool with given parameters."""

    def get_schema(self) -> dict:
        """Get JSON schema for function calling."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Registry for managing tools."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Register a tool."""
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_tools(self) -> list[dict]:
        """List all tool schemas."""
        return [tool.get_schema() for tool in self._tools.values()]

    def get_descriptions(self) -> str:
        """Get formatted tool descriptions for prompts."""
        lines = []
        for tool in self._tools.values():
            lines.append(json.dumps(tool.get_schema(), ensure_ascii=False))
        return "\n".join(lines)


# Global registry instance
registry = ToolRegistry()


def tool(name: str, description: str, parameters: dict):
    """Decorator to register a function as a tool."""

    def decorator(func: Callable):
        class FunctionTool(BaseTool):
            def __init__(self):
                self.name = name
                self.description = description
                self.parameters = parameters

            async def execute(self, **kwargs):
                if inspect.iscoroutinefunction(func):
                    return await func(**kwargs)
                return await asyncio.to_thread(func, **kwargs)

        registry.register(FunctionTool())
        return func

    return decorator
