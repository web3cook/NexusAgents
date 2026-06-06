"""A registry mapping tool names to callables and Anthropic schemas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class ToolDefinition:
    """A registered tool's metadata and implementation."""

    name: str
    description: str
    input_schema: dict
    fn: Callable[..., Any]


class ToolRegistry:
    """An in-process registry of tools keyed by "namespace.toolname"."""

    def __init__(self) -> None:
        """Initializes an empty registry."""
        self._tools: dict[str, ToolDefinition] = {}

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict,
    ) -> Callable:
        """Builds a decorator that registers the wrapped function as a tool.

        Args:
            name: The tool name in "namespace.toolname" form.
            description: Human-readable description for the model.
            input_schema: The JSON schema for the tool's input.

        Returns:
            A decorator that registers and returns the function unchanged.

        Raises:
            ValueError: If name is not in "namespace.toolname" form.
        """
        if "." not in name:
            raise ValueError(
                f"Tool name must be 'namespace.toolname', got: '{name}'"
            )

        def decorator(fn: Callable) -> Callable:
            """Registers fn under name and returns it unchanged."""
            self._tools[name] = ToolDefinition(
                name=name,
                description=description,
                input_schema=input_schema,
                fn=fn,
            )
            return fn
        return decorator

    def call(self, name: str, **kwargs: Any) -> Any:
        """Invokes a registered tool by name.

        Accepts both the registry form ("namespace.tool") and the API form
        ("namespace__tool").

        Args:
            name: The tool name in either form.
            **kwargs: Keyword arguments forwarded to the tool.

        Returns:
            Whatever the tool returns.

        Raises:
            ValueError: If no tool is registered under name.
        """
        # Accept both "namespace.tool" and "namespace__tool" (API form).
        registry_name = self._registry_name(name) if "__" in name else name
        if registry_name not in self._tools:
            raise ValueError(f"Unknown tool: {name}")
        return self._tools[registry_name].fn(**kwargs)

    @staticmethod
    def _api_name(name: str) -> str:
        """Converts "namespace.tool" to the API form "namespace__tool".

        Args:
            name: The registry-form tool name.

        Returns:
            The API-form tool name.
        """
        return name.replace(".", "__", 1)

    @staticmethod
    def _registry_name(api_name: str) -> str:
        """Converts "namespace__tool" to the registry form "namespace.tool".

        Args:
            api_name: The API-form tool name.

        Returns:
            The registry-form tool name.
        """
        return api_name.replace("__", ".", 1)

    def get_anthropic_tools(
        self,
        namespaces: list[str] | None = None,
    ) -> list[dict]:
        """Returns Anthropic tool definitions, optionally filtered.

        Args:
            namespaces: If given, only tools in these namespaces are
                returned; otherwise all tools are returned.

        Returns:
            A list of Anthropic tool definition dicts.
        """
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
        """Returns the sorted list of distinct registered namespaces."""
        return sorted({name.split(".")[0] for name in self._tools})

    def __len__(self) -> int:
        """Returns the number of registered tools."""
        return len(self._tools)


# Global singleton; all tool modules register into this.
registry = ToolRegistry()
