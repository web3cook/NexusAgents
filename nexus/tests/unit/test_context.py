from agent.core.context import compress_phase, summarise_messages
from agent.core.state import BuildState, Phase, AppSpec, CostSummary


def test_compress_planning_phase():
    state = BuildState(session_id="s1", user_description="build an app")
    state.app_spec = AppSpec(features=["auth"], db_models=["User"], api_routes=["/auth"], pages=["Login"])
    state.cost_summary = CostSummary(aws_monthly_usd=47.0, llm_tokens_estimated=180000, llm_cost_usd=2.0, steps_estimated=28)

    summary = compress_phase(state, Phase.PLANNING)
    assert "auth" in summary
    assert "47.0" in summary


def test_summarise_messages_keeps_recent():
    msgs = [{"role": "user", "content": f"msg {i}"} for i in range(20)]
    result = summarise_messages(msgs, keep_last=5)
    assert len(result) <= 6  # summary + 5 recent


def test_summarise_messages_never_orphans_tool_result():
    # Build a message list that ends with a tool_use / tool_result pair.
    # If keep_last cuts inside the pair the function must slide forward to a
    # safe boundary so the API never sees a tool_result without its tool_use.
    msgs = [
        {"role": "user", "content": "start"},                           # 0
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "foo", "input": {}}]},  # 1
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "r1"}]},      # 2
        {"role": "assistant", "content": "plan done"},                  # 3
        {"role": "user", "content": "continue"},                        # 4
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t2", "name": "bar", "input": {}}]},  # 5
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t2", "content": "r2"}]},      # 6
    ]
    # keep_last=3 naively starts at index 4 (msg "continue") — safe, plain text user msg
    result = summarise_messages(msgs, keep_last=3)
    first_real = result[1] if result[0]["content"].startswith("[CONTEXT") else result[0]
    assert first_real["role"] == "user"
    content = first_real.get("content", "")
    assert isinstance(content, str), "First kept message must be plain text, not tool_result"

    # keep_last=2 naively starts at index 5 (assistant tool_use) — must slide to index 4
    result2 = summarise_messages(msgs, keep_last=2)
    first_real2 = result2[1] if result2[0]["content"].startswith("[CONTEXT") else result2[0]
    assert first_real2["role"] == "user"
    content2 = first_real2.get("content", "")
    assert isinstance(content2, str), "Must not start with orphaned tool_result"
