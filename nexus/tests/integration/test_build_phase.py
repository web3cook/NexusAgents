"""Integration test: scaffold tools produce valid file trees."""
import pytest
from pathlib import Path
from agent.tools.code.tools import (
    scaffold_fastapi_project, scaffold_react_project, scaffold_db_model
)


def test_scaffold_fastapi_full(tmp_path):
    result = scaffold_fastapi_project(
        workspace=str(tmp_path),
        app_name="myapp",
        features=["auth", "dashboard"],
        db_models=["User", "Alert"],
        api_routes=["/auth/login", "/auth/register", "/alerts"],
    )
    assert Path(result["dockerfile_path"]).exists()
    assert any("main.py" in f for f in result["files_created"])
    assert any("auth.py" in f for f in result["files_created"])
    assert "DATABASE_URL" in result["env_vars_required"]


def test_scaffold_react_full(tmp_path):
    result = scaffold_react_project(
        workspace=str(tmp_path),
        app_name="myapp",
        pages=["Dashboard", "Alerts"],
        api_routes=["/auth/login", "/alerts"],
    )
    assert Path(result["dockerfile_path"]).exists()
    assert any("App.tsx" in f for f in result["files_created"])
    assert any("AdminDashboard" in f for f in result["files_created"])


def test_scaffold_db_model_produces_valid_python(tmp_path):
    result = scaffold_db_model(
        workspace=str(tmp_path),
        model_name="ApiKey",
        fields=[
            {"name": "key_hash", "column_type": "String(64)", "nullable": False},
            {"name": "name", "column_type": "String(128)", "nullable": True},
        ],
    )
    content = Path(result["file_path"]).read_text()
    assert "class ApiKey" in content
    assert "key_hash" in content
    compile(content, result["file_path"], "exec")
