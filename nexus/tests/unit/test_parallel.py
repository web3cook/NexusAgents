import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from agent.core.parallel import pre_render_build_templates, run_build_parallel
from agent.core.state import AppSpec, BuildState, AgentStatus

SPEC = AppSpec(
    features=["auth", "dashboard"],
    db_models=["User", "Post"],
    api_routes=["/auth", "/posts"],
    pages=["Login", "Dashboard"],
)


def test_pre_render_creates_dockerfiles():
    with tempfile.TemporaryDirectory() as ws:
        pre_render_build_templates(SPEC, ws)
        assert (Path(ws) / "backend" / "Dockerfile").exists()
        assert (Path(ws) / "frontend" / "Dockerfile").exists()


def test_pre_render_creates_k8s_manifests():
    with tempfile.TemporaryDirectory() as ws:
        pre_render_build_templates(SPEC, ws)
        k8s = Path(ws) / "k8s"
        assert (k8s / "backend-deployment.yaml").exists()
        assert (k8s / "frontend-deployment.yaml").exists()
        assert (k8s / "ingress.yaml").exists()


def test_pre_render_injects_spec_values():
    with tempfile.TemporaryDirectory() as ws:
        pre_render_build_templates(SPEC, ws)
        content = (Path(ws) / "k8s" / "backend-deployment.yaml").read_text()
        assert "nexus-backend" in content


def test_pre_render_returns_file_list():
    with tempfile.TemporaryDirectory() as ws:
        files = pre_render_build_templates(SPEC, ws)
        assert len(files) >= 5
        assert all(Path(f).exists() for f in files)


def test_run_build_parallel_returns_both_manifests():
    state = BuildState(session_id="t", user_description="test")
    state.app_spec = SPEC

    mock_backend = {
        "files_created": ["/tmp/x.py"],
        "api_routes": ["/auth"],
        "env_vars_required": [],
        "dockerfile_path": "/tmp/Dockerfile",
        "test_results": {"passed": 1, "failed": 0},
    }
    mock_frontend = {
        "files_created": ["/tmp/App.tsx"],
        "dockerfile_path": "/tmp/Dockerfile",
        "static_build_cmd": "npm run build",
        "test_results": {"passed": 1, "failed": 0},
    }

    # Use ThreadPoolExecutor so patched module-level functions are visible to workers
    with patch("agent.core.parallel.ProcessPoolExecutor", ThreadPoolExecutor), \
         patch("agent.core.parallel._run_backend_subprocess",  return_value=mock_backend), \
         patch("agent.core.parallel._run_frontend_subprocess", return_value=mock_frontend):
        backend, frontend = run_build_parallel(SPEC, "/tmp/ws", state)

    assert backend["api_routes"] == ["/auth"]
    assert frontend["files_created"] == ["/tmp/App.tsx"]
    assert state.agent_statuses["BackendBuilderSubagent"] == AgentStatus.CODE_COMPLETED.value
    assert state.agent_statuses["FrontendBuilderSubagent"] == AgentStatus.CODE_COMPLETED.value


def test_run_build_parallel_marks_error_status():
    state = BuildState(session_id="t2", user_description="test")
    state.app_spec = SPEC

    with patch("agent.core.parallel.ProcessPoolExecutor", ThreadPoolExecutor), \
         patch("agent.core.parallel._run_backend_subprocess",  return_value={"error": "boom"}), \
         patch("agent.core.parallel._run_frontend_subprocess", return_value={"files_created": []}):
        backend, _ = run_build_parallel(SPEC, "/tmp/ws", state)

    # error result → stays Ongoing (not upgraded to Code Completed)
    assert state.agent_statuses["BackendBuilderSubagent"] == AgentStatus.ONGOING.value
