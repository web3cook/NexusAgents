from unittest.mock import patch, MagicMock
from agent.tools.k8s.tools import (
    apply_manifest, create_namespace, create_secret,
    get_pod_status, wait_for_rollout, get_ingress_address,
)
import tempfile, os


def _mock_run(returncode=0, stdout="", stderr=""):
    m = MagicMock(returncode=returncode, stdout=stdout, stderr=stderr)
    return m


def test_apply_manifest(tmp_path):
    manifest = tmp_path / "dep.yaml"
    manifest.write_text("apiVersion: apps/v1\nkind: Deployment")
    with patch("agent.tools.k8s.tools.subprocess.run") as mock_run:
        mock_run.return_value = _mock_run(stdout="deployment.apps/backend created\n")
        result = apply_manifest(manifest_path=str(manifest))
    assert result["applied"] is True


def test_create_namespace():
    with patch("agent.tools.k8s.tools.subprocess.run") as mock_run:
        mock_run.return_value = _mock_run(stdout="namespace/myapp created\n")
        result = create_namespace(name="myapp")
    assert result["namespace"] == "myapp"


def test_create_secret():
    with patch("agent.tools.k8s.tools.subprocess.run") as mock_run:
        mock_run.return_value = _mock_run(stdout="secret/myapp-secrets created\n")
        result = create_secret(name="myapp-secrets", namespace="myapp", data={"database_url": "postgres://..."})
    assert result["created"] is True


def test_get_pod_status():
    with patch("agent.tools.k8s.tools.subprocess.run") as mock_run:
        mock_run.return_value = _mock_run(stdout='[{"name":"backend-abc","ready":"1/1","status":"Running"}]')
        result = get_pod_status(namespace="myapp", deployment="backend")
    assert isinstance(result["pods"], list)


def test_wait_for_rollout():
    with patch("agent.tools.k8s.tools.subprocess.run") as mock_run:
        mock_run.return_value = _mock_run(stdout="deployment 'backend' successfully rolled out\n")
        result = wait_for_rollout(namespace="myapp", deployment="backend")
    assert result["ready"] is True
