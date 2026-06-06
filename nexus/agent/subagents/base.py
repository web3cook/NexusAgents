"""Base class and shared loop for tool-using subagents."""

from __future__ import annotations

import json
import logging
import re
import time
import traceback

import anthropic

from agent.tools.registry import registry

client = anthropic.Anthropic()

TOOL_USE_SYSTEM_SUFFIX = """
Use tools one at a time. When your task is complete, stop calling tools and output a JSON result block:
<result>
{...your structured output...}
</result>
"""


class BaseSubagent:
    """A tool-using subagent driven by a bounded LLM tool-call loop."""

    def __init__(
        self,
        name: str,
        system_prompt: str,
        allowed_namespaces: list[str],
        model: str = "claude-sonnet-4-6",
        max_iterations: int = 30,
    ):
        """Initializes the subagent.

        Args:
            name: The subagent's display name.
            system_prompt: The base system prompt; a tool-use suffix is
                appended automatically.
            allowed_namespaces: Tool namespaces this subagent may call.
            model: The model id to drive the loop.
            max_iterations: Maximum number of tool-call iterations.
        """
        self.name = name
        self.system_prompt = system_prompt + TOOL_USE_SYSTEM_SUFFIX
        self.allowed_namespaces = allowed_namespaces
        self.model = model
        self.max_iterations = max_iterations
        self._logger = logging.getLogger(f"nexus.subagent.{name}")
        self.total_cost_usd: float = 0.0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_cache_read: int = 0
        self.total_cache_creation: int = 0
        self._api_calls: int = 0

    def _accumulate_usage(self, usage) -> None:
        """Adds one API call's token usage and cost to the running totals.

        Args:
            usage: The usage object returned on an Anthropic response.
        """
        from agent.core.cost import compute_cost
        inp = getattr(usage, "input_tokens", 0)
        out = getattr(usage, "output_tokens", 0)
        cr = getattr(usage, "cache_read_input_tokens", 0)
        cc = getattr(usage, "cache_creation_input_tokens", 0)
        self.total_input_tokens += inp
        self.total_output_tokens += out
        self.total_cache_read += cr
        self.total_cache_creation += cc
        self._api_calls += 1
        self.total_cost_usd += compute_cost(inp, out, cr, cc, self.model)

    def cost_summary(self) -> dict:
        """Returns a snapshot of accumulated token usage and cost."""
        return {
            "total_cost_usd": round(self.total_cost_usd, 6),
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "cache_read_tokens": self.total_cache_read,
            "calls": self._api_calls,
            "model": self.model,
        }

    def get_tools(self) -> list[dict]:
        """Returns the Anthropic tool definitions for allowed namespaces.

        The last tool carries an ephemeral cache_control marker so the
        tool block can be prompt-cached.
        """
        tools = registry.get_anthropic_tools(
            namespaces=self.allowed_namespaces
        )
        if tools:
            tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}
        return tools

    def run(self, input_data: dict) -> dict:
        """Runs the tool-call loop until a result block or the iteration cap.

        Args:
            input_data: The task input passed to the model as the first
                user message.

        Returns:
            The parsed JSON result block, a {"raw": ...} fallback when the
            block is not valid JSON, or an error dict if the iteration cap
            is reached.
        """
        self._logger.info(
            "[bold cyan]%s[/bold cyan] starting  "
            "[dim]namespaces=%s model=%s[/dim]",
            self.name, self.allowed_namespaces, self.model,
        )
        self._logger.debug(
            "  input: %s",
            {k: str(v)[:80] for k, v in input_data.items()},
        )

        messages = [{"role": "user", "content": str(input_data)}]
        tools = self.get_tools()
        iterations = 0
        tool_call_count = 0
        start = time.monotonic()

        while iterations < self.max_iterations:
            iterations += 1
            response = client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=[{
                    "type": "text",
                    "text": self.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                tools=tools,
                messages=messages,
            )
            self._accumulate_usage(response.usage)

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    tool_call_count += 1
                    display_name = (
                        registry._registry_name(block.name)
                        if "__" in block.name
                        else block.name
                    )
                    self._logger.info(
                        "  [dim][%s #%d][/dim] → %s",
                        self.name, tool_call_count, display_name,
                    )
                    self._logger.debug("    input: %s", block.input)
                    t0 = time.monotonic()
                    try:
                        result = registry.call(block.name, **block.input)
                        elapsed_ms = int((time.monotonic() - t0) * 1000)
                        self._logger.info(
                            "    [green]ok[/green]  [dim]%dms[/dim]",
                            elapsed_ms,
                        )
                    except Exception as exc:
                        elapsed_ms = int((time.monotonic() - t0) * 1000)
                        self._logger.warning(
                            "    [red]err[/red] %s  [dim]%dms[/dim]",
                            exc, elapsed_ms,
                        )
                        self._logger.debug(
                            "    traceback:\n%s", traceback.format_exc()
                        )
                        result = {
                            "error": str(exc),
                            "traceback": traceback.format_exc(),
                        }
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result),
                    })

            if tool_results:
                messages.append(
                    {"role": "assistant", "content": response.content}
                )
                messages.append({"role": "user", "content": tool_results})

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text") and "<result>" in block.text:
                        match = re.search(
                            r"<result>(.*?)</result>", block.text, re.DOTALL
                        )
                        if match:
                            elapsed = time.monotonic() - start
                            self._logger.info(
                                "[green]✓[/green] [bold cyan]%s[/bold cyan] "
                                "complete  [dim]%d tool calls, %.1fs[/dim]",
                                self.name, tool_call_count, elapsed,
                            )
                            try:
                                return json.loads(match.group(1).strip())
                            except json.JSONDecodeError:
                                return {"raw": match.group(1).strip()}
                break

        self._logger.warning(
            "[yellow]%s hit max_iterations (%d) without returning "
            "<result>[/yellow]  [dim]%d tool calls[/dim]",
            self.name, self.max_iterations, tool_call_count,
        )
        return {"error": "max_iterations reached", "iterations": iterations}
