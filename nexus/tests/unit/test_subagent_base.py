from unittest.mock import MagicMock
from agent.subagents.base import BaseSubagent


def _make_usage(inp=100, out=50, cr=80, cc=0):
    u = MagicMock()
    u.input_tokens = inp
    u.output_tokens = out
    u.cache_read_input_tokens = cr
    u.cache_creation_input_tokens = cc
    return u


def test_subagent_accumulates_cost():
    agent = BaseSubagent(
        name="Test", system_prompt="test", allowed_namespaces=[], model="claude-haiku-4-5-20251001"
    )
    agent._accumulate_usage(_make_usage(inp=1000, out=200, cr=800, cc=0))
    assert agent.total_cost_usd > 0
    assert agent.total_input_tokens == 1000
    assert agent.total_output_tokens == 200
    assert agent._api_calls == 1


def test_subagent_cost_summary_dict():
    agent = BaseSubagent(
        name="Test", system_prompt="test", allowed_namespaces=[], model="claude-sonnet-4-6"
    )
    agent._accumulate_usage(_make_usage(inp=500, out=100, cr=400, cc=0))
    summary = agent.cost_summary()
    assert "total_cost_usd" in summary
    assert "calls" in summary
    assert summary["calls"] == 1
    assert summary["model"] == "claude-sonnet-4-6"


def test_accumulate_multiple_calls():
    agent = BaseSubagent(
        name="Test", system_prompt="test", allowed_namespaces=[], model="claude-opus-4-8"
    )
    agent._accumulate_usage(_make_usage(inp=1000, out=200))
    agent._accumulate_usage(_make_usage(inp=500,  out=100))
    assert agent._api_calls == 2
    assert agent.total_input_tokens == 1500
    assert agent.total_output_tokens == 300
    assert agent.total_cost_usd > 0
