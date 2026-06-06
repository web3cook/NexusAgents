from agent.core.state import BuildState, Phase, AppSpec, CostSummary

def test_build_state_defaults():
    s = BuildState(session_id="abc", user_description="build me an app")
    assert s.current_phase == Phase.PLANNING
    assert s.tool_call_count == 0
    assert s.app_spec is None

def test_build_state_checkpoint_roundtrip(tmp_path):
    s = BuildState(
        session_id="test-123",
        user_description="test app",
        current_phase=Phase.BACKEND,
        app_spec=AppSpec(features=["auth"], db_models=["User"], api_routes=["/auth"], pages=["Login"]),
        cost_summary=CostSummary(aws_monthly_usd=47.20, llm_tokens_estimated=180000, llm_cost_usd=2.16, steps_estimated=28),
        tool_call_count=5,
    )
    checkpoint_path = tmp_path / "state.json"
    s.checkpoint(checkpoint_path)
    loaded = BuildState.from_checkpoint(checkpoint_path)
    assert loaded.session_id == "test-123"
    assert loaded.current_phase == Phase.BACKEND
    assert loaded.tool_call_count == 5
    assert loaded.app_spec.features == ["auth"]
    assert loaded.cost_summary.aws_monthly_usd == 47.20

def test_phase_enum_completeness():
    phases = [Phase.PLANNING, Phase.BACKEND, Phase.FRONTEND, Phase.INFRA, Phase.TEST, Phase.MONITORING, Phase.COMPLETE]
    assert len(phases) == 7
