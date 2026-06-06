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
