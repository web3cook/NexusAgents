from unittest.mock import patch, MagicMock
from agent.tools.docker.tools import build_image, tag_image, push_to_ecr, inspect_image


def test_build_image_calls_subprocess():
    with patch("agent.tools.docker.tools.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Successfully built abc123\n", stderr="")
        result = build_image(context_path="/tmp/backend", tag="backend:latest", dockerfile="Dockerfile")
    assert result["tag"] == "backend:latest"
    assert result["success"] is True


def test_tag_image():
    with patch("agent.tools.docker.tools.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = tag_image(source_tag="backend:latest", target_tag="123.dkr.ecr.us-east-1.amazonaws.com/backend:latest")
    assert result["target_tag"] == "123.dkr.ecr.us-east-1.amazonaws.com/backend:latest"


def test_push_to_ecr():
    with patch("agent.tools.docker.tools.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="Pushed\n", stderr="")
        result = push_to_ecr(image_tag="123.dkr.ecr.us-east-1.amazonaws.com/backend:latest", region="us-east-1")
    assert result["pushed"] is True
    assert "ecr_uri" in result
