from __future__ import annotations
import json
import re
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
    def __init__(
        self,
        name: str,
        system_prompt: str,
        allowed_namespaces: list[str],
        model: str = "claude-sonnet-4-6",
        max_iterations: int = 30,
    ):
        self.name = name
        self.system_prompt = system_prompt + TOOL_USE_SYSTEM_SUFFIX
        self.allowed_namespaces = allowed_namespaces
        self.model = model
        self.max_iterations = max_iterations

    def get_tools(self) -> list[dict]:
        return registry.get_anthropic_tools(namespaces=self.allowed_namespaces)

    def run(self, input_data: dict) -> dict:
        messages = [{"role": "user", "content": str(input_data)}]
        tools = self.get_tools()
        iterations = 0

        while iterations < self.max_iterations:
            iterations += 1
            response = client.messages.create(
                model=self.model,
                max_tokens=4096,
                system=[{"type": "text", "text": self.system_prompt,
                          "cache_control": {"type": "ephemeral"}}],
                tools=tools,
                messages=messages,
            )

            tool_results = []
            for block in response.content:
                if block.type == "tool_use":
                    try:
                        result = registry.call(block.name, **block.input)
                    except Exception as exc:
                        result = {"error": str(exc)}
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": str(result),
                    })

            if tool_results:
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

            if response.stop_reason == "end_turn":
                for block in response.content:
                    if hasattr(block, "text") and "<result>" in block.text:
                        match = re.search(r"<result>(.*?)</result>", block.text, re.DOTALL)
                        if match:
                            try:
                                return json.loads(match.group(1).strip())
                            except json.JSONDecodeError:
                                return {"raw": match.group(1).strip()}
                break

        return {"error": "max_iterations reached", "iterations": iterations}
