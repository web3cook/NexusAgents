from agent.tools.registry import ToolRegistry
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
    # API names use __ instead of . (Anthropic rejects dots in tool names)
    assert plan_tools[0]["name"] == "plan__foo"

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

def test_register_without_namespace_raises():
    reg = ToolRegistry()
    with pytest.raises(ValueError, match="namespace.toolname"):
        reg.register(name="notool", description="x", input_schema={})

def test_get_namespaces_sorted():
    reg = ToolRegistry()

    @reg.register(name="z.one", description="z", input_schema={"type": "object", "properties": {}})
    def one(): pass

    @reg.register(name="a.two", description="a", input_schema={"type": "object", "properties": {}})
    def two(): pass

    assert reg.get_namespaces() == ["a", "z"]

def test_call_via_api_name():
    reg = ToolRegistry()

    @reg.register(name="plan.foo", description="foo", input_schema={"type": "object", "properties": {}})
    def foo(): return "foo"

    # Both dotted and double-underscore forms should work
    assert reg.call("plan.foo") == "foo"
    assert reg.call("plan__foo") == "foo"

def test_len():
    reg = ToolRegistry()
    assert len(reg) == 0

    @reg.register(name="ns.t", description="t", input_schema={"type": "object", "properties": {}})
    def t(): pass

    assert len(reg) == 1
