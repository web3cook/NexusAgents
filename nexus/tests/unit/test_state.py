from agent.core.state import (
    AgentStatus, BuildState, Phase, AppSpec, CostSummary,
)


def test_build_state_defaults():
    s = BuildState(session_id="abc", user_description="build me an app")
    assert s.current_phase == Phase.PLANNING
    assert s.tool_call_count == 0
    assert s.app_spec is None


def test_build_state_checkpoint_roundtrip(tmp_path):
    s = BuildState(
        session_id="test-123",
        user_description="test app",
        current_phase=Phase.BUILD,
        app_spec=AppSpec(features=["auth"], db_models=["User"], api_routes=["/auth"], pages=["Login"]),
        cost_summary=CostSummary(aws_monthly_usd=47.20, llm_tokens_estimated=180000, llm_cost_usd=2.16, steps_estimated=28),
        tool_call_count=5,
    )
    checkpoint_path = tmp_path / "state.json"
    s.checkpoint(checkpoint_path)
    loaded = BuildState.from_checkpoint(checkpoint_path)
    assert loaded.session_id == "test-123"
    assert loaded.current_phase == Phase.BUILD
    assert loaded.tool_call_count == 5
    assert loaded.app_spec.features == ["auth"]
    assert loaded.cost_summary.aws_monthly_usd == 47.20


def test_phase_enum_completeness():
    phases = [Phase.PLANNING, Phase.API_SPEC, Phase.BUILD, Phase.INFRA, Phase.TEST, Phase.MONITORING, Phase.COMPLETE]
    assert len(phases) == 7


def test_agent_status_ordering():
    assert AgentStatus.PENDING != AgentStatus.ONGOING
    assert list(AgentStatus) == [
        AgentStatus.PENDING, AgentStatus.ONGOING,
        AgentStatus.CODE_COMPLETED, AgentStatus.TESTED,
    ]


def test_build_state_has_new_phases():
    assert Phase.API_SPEC in list(Phase)
    assert Phase.BUILD in list(Phase)
    assert "BACKEND" not in [p.value for p in Phase]
    assert "FRONTEND" not in [p.value for p in Phase]


def test_build_state_agent_statuses():
    s = BuildState(session_id="x", user_description="test")
    assert s.agent_statuses == {}
    s.set_agent_status("BackendBuilderSubagent", AgentStatus.ONGOING)
    assert s.agent_statuses["BackendBuilderSubagent"] == AgentStatus.ONGOING.value


def test_build_state_file_registry():
    s = BuildState(session_id="x", user_description="test")
    s.register_file("/tmp/ws/backend/main.py", "backend")
    assert len(s.file_registry) == 1
    assert s.file_registry[0]["path"] == "/tmp/ws/backend/main.py"
    assert s.file_registry[0]["category"] == "backend"
    assert "created_at" in s.file_registry[0]


def test_build_state_cost_tracking():
    s = BuildState(session_id="x", user_description="test")
    s.add_cost(input_tokens=1000, output_tokens=200,
               cache_read=800, cache_creation=0, model="claude-opus-4-8")
    assert s.cost_tracking["total_usd"] > 0
    assert s.cost_tracking["calls"] == 1
    assert s.cost_tracking["input_tokens"] == 1000
    assert "claude-opus-4-8" in s.cost_tracking["by_model"]


def test_checkpoint_roundtrip_with_new_fields(tmp_path):
    s = BuildState(session_id="rt", user_description="roundtrip")
    s.set_agent_status("BackendBuilderSubagent", AgentStatus.CODE_COMPLETED)
    s.register_file("/tmp/x.py", "backend")
    s.add_cost(input_tokens=100, output_tokens=50,
               cache_read=0, cache_creation=0, model="claude-sonnet-4-6")
    path = tmp_path / "rt.json"
    s.checkpoint(path)
    s2 = BuildState.from_checkpoint(path)
    assert s2.agent_statuses["BackendBuilderSubagent"] == AgentStatus.CODE_COMPLETED.value
    assert len(s2.file_registry) == 1
    assert s2.cost_tracking["calls"] == 1


def test_from_checkpoint_migrates_old_backend_phase(tmp_path):
    """Old checkpoints with BACKEND/FRONTEND phases must load as BUILD."""
    import json
    old = {
        "session_id": "old", "user_description": "old app",
        "current_phase": "BACKEND", "app_spec": None, "cost_summary": None,
        "backend_manifest": None, "frontend_manifest": None,
        "deployment_result": None, "test_report": None,
        "errors": [], "tool_call_count": 3, "checkpointed_at": None,
    }
    path = tmp_path / "old.json"
    path.write_text(json.dumps(old))
    loaded = BuildState.from_checkpoint(path)
    assert loaded.current_phase == Phase.BUILD
