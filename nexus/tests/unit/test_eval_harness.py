from eval.harness import Check, run_eval, EvalCase
from agent.core.state import BuildState, CostSummary


def test_cost_summary_check_passes():
    state = BuildState(session_id="x", user_description="test")
    state.cost_summary = CostSummary(aws_monthly_usd=47.0, llm_tokens_estimated=180000, llm_cost_usd=2.0, steps_estimated=28)
    state.tool_call_count = 25
    check = Check.cost_summary_present()
    result = check(state)
    assert result.passed is True


def test_tool_call_count_check():
    state = BuildState(session_id="x", user_description="test")
    state.tool_call_count = 25
    check = Check.tool_call_count_gte(20)
    assert check(state).passed is True
    check2 = Check.tool_call_count_gte(30)
    assert check2(state).passed is False


def test_run_eval_aggregates():
    state = BuildState(session_id="x", user_description="test")
    state.tool_call_count = 25
    state.cost_summary = CostSummary(aws_monthly_usd=47.0, llm_tokens_estimated=180000, llm_cost_usd=2.0, steps_estimated=28)
    case = EvalCase(description="test case", checks=[Check.cost_summary_present(), Check.tool_call_count_gte(20)])
    result = run_eval(case, state)
    assert result["passed"] == 2
    assert result["failed"] == 0
