from unittest.mock import patch, MagicMock
from agent.tools.test.tools import run_unit_tests, health_check_endpoints, validate_k8s_manifests


def test_run_unit_tests_python(tmp_path):
    (tmp_path / "test_sample.py").write_text("def test_ok(): assert 1 == 1\n")
    result = run_unit_tests(workspace=str(tmp_path), language="python")
    assert "passed" in result
    assert result["passed"] >= 1


def test_health_check_success():
    with patch("agent.tools.test.tools.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=200)
        result = health_check_endpoints(endpoints=["http://localhost:8000/health"])
    assert result["all_healthy"] is True


def test_health_check_failure():
    with patch("agent.tools.test.tools.httpx.get") as mock_get:
        mock_get.return_value = MagicMock(status_code=503)
        result = health_check_endpoints(endpoints=["http://localhost:8000/health"])
    assert result["all_healthy"] is False
