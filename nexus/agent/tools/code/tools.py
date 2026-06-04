from __future__ import annotations
import re
import subprocess
from pathlib import Path
from agent.tools.registry import registry
from agent.core.observability import instrument
from agent.core.retry import rate_limit
from agent.core.errors import NexusError


# ── File I/O ──────────────────────────────────────────────────────────────

@registry.register(
    name="code.read_file",
    description="Read the contents of a file from the workspace",
    input_schema={"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]},
)
@instrument(namespace="code", tool="read_file")
def read_file(file_path: str) -> dict:
    rate_limit("code")
    content = Path(file_path).read_text(encoding="utf-8")
    return {"file_path": file_path, "content": content, "lines": len(content.splitlines())}


@registry.register(
    name="code.write_file",
    description="Write content to a file, creating parent directories if needed",
    input_schema={
        "type": "object",
        "properties": {"file_path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["file_path", "content"],
    },
)
@instrument(namespace="code", tool="write_file")
def write_file(file_path: str, content: str) -> dict:
    rate_limit("code")
    p = Path(file_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"file_path": file_path, "bytes_written": len(content.encode("utf-8"))}


@registry.register(
    name="code.list_dir",
    description="List files and directories at the given path",
    input_schema={"type": "object", "properties": {"directory": {"type": "string"}}, "required": ["directory"]},
)
@instrument(namespace="code", tool="list_dir")
def list_dir(directory: str) -> dict:
    rate_limit("code")
    p = Path(directory)
    entries = [
        {"name": e.name, "type": "dir" if e.is_dir() else "file", "size": e.stat().st_size if e.is_file() else 0}
        for e in sorted(p.iterdir(), key=lambda e: e.name)
    ]
    return {"directory": directory, "entries": entries}


@registry.register(
    name="code.delete_file",
    description="Delete a file from the workspace",
    input_schema={"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]},
)
@instrument(namespace="code", tool="delete_file")
def delete_file(file_path: str) -> dict:
    rate_limit("code")
    p = Path(file_path)
    if not p.exists():
        raise NexusError(f"delete_file: file not found: {file_path}")
    p.unlink()
    return {"deleted": file_path}


@registry.register(
    name="code.search_code",
    description="Search for a regex pattern across all files in a directory",
    input_schema={
        "type": "object",
        "properties": {"pattern": {"type": "string"}, "directory": {"type": "string"}},
        "required": ["pattern", "directory"],
    },
)
@instrument(namespace="code", tool="search_code")
def search_code(pattern: str, directory: str) -> dict:
    rate_limit("code")
    matches = []
    for path in Path(directory).rglob("*"):
        if path.is_file():
            try:
                for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    if re.search(pattern, line):
                        matches.append({"file": str(path), "line": i, "text": line.strip()})
            except (UnicodeDecodeError, OSError):
                pass
    return {"pattern": pattern, "matches": matches}


@registry.register(
    name="code.apply_patch",
    description="Replace old_string with new_string in a file (exact match required)",
    input_schema={
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "old_string": {"type": "string"},
            "new_string": {"type": "string"},
        },
        "required": ["file_path", "old_string", "new_string"],
    },
)
@instrument(namespace="code", tool="apply_patch")
def apply_patch(file_path: str, old_string: str, new_string: str) -> dict:
    rate_limit("code")
    p = Path(file_path)
    content = p.read_text(encoding="utf-8")
    if old_string not in content:
        raise NexusError(f"apply_patch: old_string not found in {file_path}")
    p.write_text(content.replace(old_string, new_string, 1), encoding="utf-8")
    return {"file_path": file_path, "patched": True}


# ── Scaffold Tools ────────────────────────────────────────────────────────────

from jinja2 import Environment, FileSystemLoader
from pathlib import Path as _Path

_TEMPLATES_DIR = _Path(__file__).parent.parent.parent.parent / "templates"
_jinja = Environment(loader=FileSystemLoader(str(_TEMPLATES_DIR)), trim_blocks=True, lstrip_blocks=True)

def _render(template_path: str, **ctx) -> str:
    return _jinja.get_template(template_path).render(**ctx)

def _write(workspace: str, rel_path: str, content: str) -> str:
    full = _Path(workspace) / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return str(full)


@registry.register(
    name="code.scaffold_fastapi_project",
    description="Bootstrap a complete FastAPI project from AppSpec",
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "app_name": {"type": "string"},
            "features": {"type": "array", "items": {"type": "string"}},
            "db_models": {"type": "array", "items": {"type": "string"}},
            "api_routes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["workspace", "app_name", "features", "db_models", "api_routes"],
    },
)
@instrument(namespace="code", tool="scaffold_fastapi_project")
def scaffold_fastapi_project(workspace: str, app_name: str, features: list[str], db_models: list[str], api_routes: list[str]) -> dict:
    rate_limit("code")
    files = []
    files.append(_write(workspace, "backend/app/database.py", _render("fastapi/database.py.j2")))
    files.append(_write(workspace, "backend/app/main.py", _render("fastapi/main.py.j2", app_name=app_name, db_models=db_models, api_routes=api_routes)))
    if "auth" in features:
        files.append(_write(workspace, "backend/app/models/user.py", _render("fastapi/model.py.j2", model_name="User", fields=[
            {"name": "email", "column_type": "String(255)", "nullable": False},
            {"name": "password_hash", "column_type": "String(255)", "nullable": False},
            {"name": "name", "column_type": "String(255)", "nullable": True},
        ])))
        files.append(_write(workspace, "backend/app/routes/auth.py", _render("fastapi/auth.py.j2")))
    files.append(_write(workspace, "backend/app/routes/admin.py", _render("fastapi/admin.py.j2")))
    files.append(_write(workspace, "backend/Dockerfile", _render("fastapi/Dockerfile.j2")))
    files.append(_write(workspace, "backend/requirements.txt",
        "fastapi\nuvicorn\nsqlalchemy\nalembic\nbcrypt\npyjwt\npsycopg2-binary\nboto3\n"))
    env_vars = ["DATABASE_URL", "JWT_SECRET", "AWS_REGION", "CLUSTER_NAME"]
    return {"files_created": files, "api_routes": api_routes, "env_vars_required": env_vars,
            "dockerfile_path": f"{workspace}/backend/Dockerfile"}


@registry.register(
    name="code.scaffold_react_project",
    description="Bootstrap a complete React + TypeScript project from AppSpec",
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "app_name": {"type": "string"},
            "pages": {"type": "array", "items": {"type": "string"}},
            "api_routes": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["workspace", "app_name", "pages", "api_routes"],
    },
)
@instrument(namespace="code", tool="scaffold_react_project")
def scaffold_react_project(workspace: str, app_name: str, pages: list[str], api_routes: list[str]) -> dict:
    rate_limit("code")
    files = []
    files.append(_write(workspace, "frontend/src/App.tsx", _render("react/App.tsx.j2", pages=pages)))
    files.append(_write(workspace, "frontend/src/contexts/AuthContext.tsx", _render("react/AuthContext.tsx.j2")))
    files.append(_write(workspace, "frontend/src/lib/api.ts", _render("react/api.ts.j2")))
    files.append(_write(workspace, "frontend/src/pages/Login.tsx", _render("react/Login.tsx.j2")))
    files.append(_write(workspace, "frontend/src/pages/AdminDashboard.tsx", _render("react/AdminDashboard.tsx.j2")))
    for page in pages:
        files.append(_write(workspace, f"frontend/src/pages/{page}.tsx",
            _render("react/page.tsx.j2", page_name=page, model_name=page, route_prefix=page.lower(), page_title=page, fields=[])))
    files.append(_write(workspace, "frontend/Dockerfile", _render("react/Dockerfile.j2")))
    pkg = '{"name":"frontend","version":"1.0.0","scripts":{"dev":"vite","build":"vite build"},"dependencies":{"react":"^18","react-dom":"^18","react-router-dom":"^6","axios":"^1","recharts":"^2"},"devDependencies":{"@types/react":"^18","typescript":"^5","vite":"^5","@vitejs/plugin-react":"^4"}}'
    files.append(_write(workspace, "frontend/package.json", pkg))
    return {"files_created": files, "dockerfile_path": f"{workspace}/frontend/Dockerfile",
            "static_build_cmd": "npm run build"}


@registry.register(
    name="code.scaffold_api_route",
    description="Generate a FastAPI CRUD route for a model",
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "model_name": {"type": "string"},
            "fields": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["workspace", "model_name", "fields"],
    },
)
@instrument(namespace="code", tool="scaffold_api_route")
def scaffold_api_route(workspace: str, model_name: str, fields: list[dict]) -> dict:
    rate_limit("code")
    content = _render("fastapi/route.py.j2", model_name=model_name, fields=fields)
    path = _write(workspace, f"backend/app/routes/{model_name.lower()}.py", content)
    return {"file_path": path, "model_name": model_name}


@registry.register(
    name="code.scaffold_react_page",
    description="Generate a React CRUD page for a model",
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "page_name": {"type": "string"},
            "model_name": {"type": "string"},
            "route_prefix": {"type": "string"},
            "fields": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["workspace", "page_name", "model_name", "route_prefix", "fields"],
    },
)
@instrument(namespace="code", tool="scaffold_react_page")
def scaffold_react_page(workspace: str, page_name: str, model_name: str, route_prefix: str, fields: list[dict]) -> dict:
    rate_limit("code")
    content = _render("react/page.tsx.j2", page_name=page_name, model_name=model_name,
                      route_prefix=route_prefix, page_title=page_name, fields=fields)
    path = _write(workspace, f"frontend/src/pages/{page_name}.tsx", content)
    return {"file_path": path, "page_name": page_name}


@registry.register(
    name="code.scaffold_db_model",
    description="Generate a SQLAlchemy model file",
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "model_name": {"type": "string"},
            "fields": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["workspace", "model_name", "fields"],
    },
)
@instrument(namespace="code", tool="scaffold_db_model")
def scaffold_db_model(workspace: str, model_name: str, fields: list[dict]) -> dict:
    rate_limit("code")
    content = _render("fastapi/model.py.j2", model_name=model_name, fields=fields)
    path = _write(workspace, f"backend/app/models/{model_name.lower()}.py", content)
    return {"file_path": path, "model_name": model_name}


@registry.register(
    name="code.scaffold_migration",
    description="Generate an Alembic migration stub for a model",
    input_schema={
        "type": "object",
        "properties": {"workspace": {"type": "string"}, "model_name": {"type": "string"}},
        "required": ["workspace", "model_name"],
    },
)
@instrument(namespace="code", tool="scaffold_migration")
def scaffold_migration(workspace: str, model_name: str) -> dict:
    rate_limit("code")
    content = f'"""create {model_name.lower()} table"""\nfrom alembic import op\nimport sqlalchemy as sa\n\ndef upgrade():\n    op.create_table("{model_name.lower()}s",\n        sa.Column("id", sa.Integer, primary_key=True),\n        sa.Column("created_at", sa.DateTime),\n    )\n\ndef downgrade():\n    op.drop_table("{model_name.lower()}s")\n'
    path = _write(workspace, f"backend/alembic/versions/create_{model_name.lower()}.py", content)
    return {"file_path": path}


@registry.register(
    name="code.scaffold_k8s_manifest",
    description="Generate Kubernetes Deployment + Service YAML for a component",
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "name": {"type": "string"},
            "namespace": {"type": "string"},
            "image": {"type": "string"},
            "port": {"type": "integer"},
            "env_vars": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["workspace", "name", "namespace", "image", "port", "env_vars"],
    },
)
@instrument(namespace="code", tool="scaffold_k8s_manifest")
def scaffold_k8s_manifest(workspace: str, name: str, namespace: str, image: str, port: int, env_vars: list[dict]) -> dict:
    rate_limit("code")
    dep = _render("k8s/deployment.yaml.j2", name=name, namespace=namespace, image=image, port=port, env_vars=env_vars)
    svc = _render("k8s/service.yaml.j2", name=name, namespace=namespace, port=port)
    dep_path = _write(workspace, f"k8s/{name}-deployment.yaml", dep)
    svc_path = _write(workspace, f"k8s/{name}-service.yaml", svc)
    return {"deployment_path": dep_path, "service_path": svc_path}


@registry.register(
    name="code.scaffold_helm_chart",
    description="Generate a basic Helm chart skeleton for a component",
    input_schema={
        "type": "object",
        "properties": {"workspace": {"type": "string"}, "name": {"type": "string"}},
        "required": ["workspace", "name"],
    },
)
@instrument(namespace="code", tool="scaffold_helm_chart")
def scaffold_helm_chart(workspace: str, name: str) -> dict:
    rate_limit("code")
    chart = f'apiVersion: v2\nname: {name}\nversion: 0.1.0\n'
    path = _write(workspace, f"helm/{name}/Chart.yaml", chart)
    from pathlib import Path as _P
    return {"chart_path": str(_P(path).parent)}


@registry.register(
    name="code.run_linter",
    description="Run ruff (Python) or eslint (TypeScript) on the workspace",
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "language": {"type": "string", "enum": ["python", "typescript"]},
        },
        "required": ["workspace", "language"],
    },
)
@instrument(namespace="code", tool="run_linter")
def run_linter(workspace: str, language: str) -> dict:
    rate_limit("code")
    if language == "python":
        result = subprocess.run(["ruff", "check", workspace], capture_output=True, text=True)
    else:
        result = subprocess.run(["npx", "eslint", workspace, "--ext", ".ts,.tsx"], capture_output=True, text=True, cwd=workspace)
    return {"returncode": result.returncode, "stdout": result.stdout[:2000], "passed": result.returncode == 0}


@registry.register(
    name="code.run_formatter",
    description="Run black (Python) or prettier (TypeScript) on the workspace",
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "language": {"type": "string", "enum": ["python", "typescript"]},
        },
        "required": ["workspace", "language"],
    },
)
@instrument(namespace="code", tool="run_formatter")
def run_formatter(workspace: str, language: str) -> dict:
    rate_limit("code")
    if language == "python":
        result = subprocess.run(["black", workspace], capture_output=True, text=True)
    else:
        result = subprocess.run(["npx", "prettier", "--write", workspace], capture_output=True, text=True)
    return {"returncode": result.returncode, "formatted": result.returncode == 0}
