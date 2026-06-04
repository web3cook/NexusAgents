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
        if name not in self._tools:
            raise ValueError(f"Unknown tool: {name}")
        return self._tools[name].fn(**kwargs)

    def get_anthropic_tools(self, namespaces: list[str] | None = None) -> list[dict]:
        result = []
        for name, defn in self._tools.items():
            ns = name.split(".")[0]
            if namespaces is None or ns in namespaces:
                result.append({
                    "name": name,
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
