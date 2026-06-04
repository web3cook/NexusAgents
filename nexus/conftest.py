import pytest
from pathlib import Path

@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    """Provides a temporary directory as the build workspace."""
    return tmp_path

@pytest.fixture
def sample_description() -> str:
    return "Build a SaaS app with user login, an alerting dashboard, and an API key manager"
