from agent.core.errors import (
    NexusError, PlanningError, BuildError, DeploymentError,
    TestFailure, AlertingError, RateLimitError, TransientAwsError, NetworkError
)

def test_retryable_flag():
    assert RateLimitError("aws").retryable is True
    assert TransientAwsError("timeout").retryable is True
    assert NetworkError("conn refused").retryable is True
    assert AlertingError("bot down").retryable is True

def test_non_retryable_planning():
    assert PlanningError("bad spec").retryable is False

def test_non_retryable():
    tf = TestFailure("tests failed", report={"passed": 0, "failed": 3})
    assert tf.retryable is False
    assert tf.report["failed"] == 3

def test_build_error_carries_phase():
    e = BuildError("compile error", phase="backend", files_created=["app/main.py"])
    assert e.phase == "backend"
    assert "app/main.py" in e.files_created

def test_deployment_error_carries_step():
    e = DeploymentError("eks failed", last_successful_step="create_ecr_repo", cluster_name=None)
    assert e.last_successful_step == "create_ecr_repo"
    assert e.cluster_name is None
