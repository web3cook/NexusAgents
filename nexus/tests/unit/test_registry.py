from agent.tools.registry import ToolRegistry, ToolDefinition
import pytest

def test_register_and_call():
    reg = ToolRegistry()

    @reg.register(
        name="test.add",
        description="Add two numbers",
        input_schema={"type": "object", "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}}, "required": ["a", "b"]}
    )
    def add(a: int, b: int) -> int:
        return a + b

    assert reg.call("test.add", a=2, b=3) == 5

def test_get_anthropic_tools_filters_by_namespace():
    reg = ToolRegistry()

    @reg.register(name="plan.foo", description="foo", input_schema={"type": "object", "properties": {}})
    def foo(): return "foo"

    @reg.register(name="aws.bar", description="bar", input_schema={"type": "object", "properties": {}})
    def bar(): return "bar"

    plan_tools = reg.get_anthropic_tools(namespaces=["plan"])
    assert len(plan_tools) == 1
    assert plan_tools[0]["name"] == "plan.foo"

def test_get_anthropic_tools_all():
    reg = ToolRegistry()

    @reg.register(name="a.x", description="x", input_schema={"type": "object", "properties": {}})
    def x(): return "x"

    @reg.register(name="b.y", description="y", input_schema={"type": "object", "properties": {}})
    def y(): return "y"

    all_tools = reg.get_anthropic_tools()
    assert len(all_tools) == 2

def test_call_unknown_tool_raises():
    reg = ToolRegistry()
    with pytest.raises(ValueError, match="Unknown tool"):
        reg.call("nonexistent.tool")
