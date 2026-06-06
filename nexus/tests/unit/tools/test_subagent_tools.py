from unittest.mock import patch, MagicMock
from agent.subagents.base import BaseSubagent


def test_base_subagent_scopes_tools():
    subagent = BaseSubagent(
        name="TestSubagent",
        system_prompt="You are a test subagent.",
        allowed_namespaces=["plan"],
        model="claude-haiku-4-5-20251001",
    )
    tools = subagent.get_tools()
    # API names use __ separator; extract namespace from 'namespace__tool'
    namespaces = {t["name"].split("__")[0] for t in tools}
    assert namespaces == {"plan"}


def test_base_subagent_rejects_wrong_namespace():
    subagent = BaseSubagent(
        name="PlannerOnly",
        system_prompt="You plan.",
        allowed_namespaces=["plan"],
        model="claude-haiku-4-5-20251001",
    )
    tools = subagent.get_tools()
    assert all(t["name"].startswith("plan__") for t in tools)
