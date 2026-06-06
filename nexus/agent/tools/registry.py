from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict
    fn: Callable[..., Any]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, name: str, description: str, input_schema: dict) -> Callable:
        if "." not in name:
            raise ValueError(f"Tool name must be 'namespace.toolname', got: '{name}'")
        def decorator(fn: Callable) -> Callable:
            self._tools[name] = ToolDefinition(
                name=name,
                description=description,
                input_schema=input_schema,
                fn=fn,
            )
            return fn
        return decorator

    def call(self, name: str, **kwargs: Any) -> Any:
        # Accept both 'namespace.tool' and 'namespace__tool' (API form)
        registry_name = self._registry_name(name) if "__" in name else name
        if registry_name not in self._tools:
            raise ValueError(f"Unknown tool: {name}")
        return self._tools[registry_name].fn(**kwargs)

    @staticmethod
    def _api_name(name: str) -> str:
        """Convert 'namespace.tool' → 'namespace__tool' for Anthropic API compatibility."""
        return name.replace(".", "__", 1)

    @staticmethod
    def _registry_name(api_name: str) -> str:
        """Convert 'namespace__tool' → 'namespace.tool' for registry lookup."""
        return api_name.replace("__", ".", 1)

    def get_anthropic_tools(self, namespaces: list[str] | None = None) -> list[dict]:
        result = []
        for name, defn in self._tools.items():
            ns = name.split(".")[0]
            if namespaces is None or ns in namespaces:
                result.append({
                    "name": self._api_name(name),
                    "description": defn.description,
                    "input_schema": defn.input_schema,
                })
        return result

    def get_namespaces(self) -> list[str]:
        return sorted({name.split(".")[0] for name in self._tools})

    def __len__(self) -> int:
        return len(self._tools)


# Global singleton — all tool modules register into this
registry = ToolRegistry()
