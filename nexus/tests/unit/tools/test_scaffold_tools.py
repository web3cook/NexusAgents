import pytest
from pathlib import Path
from agent.tools.code.tools import (
    scaffold_fastapi_project, scaffold_react_project,
    scaffold_api_route, scaffold_react_page,
    scaffold_db_model, scaffold_k8s_manifest, run_linter, run_formatter,
)

def test_scaffold_fastapi_creates_files(tmp_path):
    result = scaffold_fastapi_project(
        workspace=str(tmp_path),
        app_name="testapp",
        features=["auth"],
        db_models=["User"],
        api_routes=["/auth/login"],
    )
    assert result["dockerfile_path"].endswith("Dockerfile")
    assert any("main.py" in f for f in result["files_created"])

def test_scaffold_react_creates_files(tmp_path):
    result = scaffold_react_project(
        workspace=str(tmp_path),
        app_name="testapp",
        pages=["Dashboard"],
        api_routes=["/auth/login"],
    )
    assert result["dockerfile_path"].endswith("Dockerfile")
    assert any("App.tsx" in f for f in result["files_created"])
    assert any("AdminDashboard" in f for f in result["files_created"])

def test_scaffold_db_model(tmp_path):
    result = scaffold_db_model(
        workspace=str(tmp_path),
        model_name="ApiKey",
        fields=[{"name": "key_hash", "column_type": "String", "python_type": "str", "nullable": False}],
    )
    content = Path(result["file_path"]).read_text()
    assert "ApiKey" in content
    assert "key_hash" in content

def test_scaffold_k8s_manifest(tmp_path):
    result = scaffold_k8s_manifest(
        workspace=str(tmp_path),
        name="backend",
        namespace="myapp",
        image="123.dkr.ecr.us-east-1.amazonaws.com/backend:latest",
        port=8000,
        env_vars=[{"name": "DATABASE_URL", "key": "database_url"}],
    )
    assert result["deployment_path"].endswith(".yaml")
    content = Path(result["deployment_path"]).read_text()
    assert "backend" in content
    assert "8000" in content
