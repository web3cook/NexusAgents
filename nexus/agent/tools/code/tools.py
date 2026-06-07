"""File I/O and scaffolding tools for generating project source."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from jinja2 import Environment, FileSystemLoader

from agent.core.errors import NexusError
from agent.core.observability import instrument
from agent.core.retry import rate_limit
from agent.tools.registry import registry


def _run_subprocess(
    cmd: list[str],
    cwd: str | None = None,
    timeout: int = 60,
    ok_key: str = "passed",
    ok_fn: Callable[[Any], bool] = lambda r: r.returncode == 0,
) -> dict:
    """Runs a subprocess with a hard timeout and no interactive stdin.

    Args:
        cmd: The command and arguments to run.
        cwd: Working directory for the command.
        timeout: Hard timeout in seconds.
        ok_key: Result-dict key under which the success flag is stored.
        ok_fn: Predicate mapping the completed process to a success flag.

    Returns:
        A dict with returncode, truncated stdout, and the ok_key flag.
    """
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=cwd, stdin=subprocess.DEVNULL, timeout=timeout,
        )
        return {
            "returncode": result.returncode,
            "stdout": result.stdout[:2000],
            ok_key: ok_fn(result),
        }
    except subprocess.TimeoutExpired:
        return {
            "returncode": 1,
            "stdout": f"Command timed out after {timeout}s — {' '.join(cmd[:3])}",
            ok_key: False,
        }


@registry.register(
    name="code.read_file",
    description="Read the contents of a file from the workspace",
    input_schema={
        "type": "object",
        "properties": {"file_path": {"type": "string"}},
        "required": ["file_path"],
    },
)
@instrument(namespace="code", tool="read_file")
def read_file(file_path: str) -> dict:
    """Reads a UTF-8 file from the workspace.

    Args:
        file_path: Path to the file to read.

    Returns:
        A dict with the file path, content, and line count.
    """
    rate_limit("code")
    content = Path(file_path).read_text(encoding="utf-8")
    return {
        "file_path": file_path,
        "content": content,
        "lines": len(content.splitlines()),
    }


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
    """Writes content to a file, creating parent directories as needed.

    Args:
        file_path: Destination file path.
        content: Text to write.

    Returns:
        A dict with the file path and number of bytes written.
    """
    rate_limit("code")
    p = Path(file_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {
        "file_path": file_path,
        "bytes_written": len(content.encode("utf-8")),
    }


@registry.register(
    name="code.list_dir",
    description="List files and directories at the given path",
    input_schema={
        "type": "object",
        "properties": {"directory": {"type": "string"}},
        "required": ["directory"],
    },
)
@instrument(namespace="code", tool="list_dir")
def list_dir(directory: str) -> dict:
    """Lists the entries of a directory, sorted by name.

    Args:
        directory: The directory to list.

    Returns:
        A dict with the directory and a list of {name, type, size} entries.
    """
    rate_limit("code")
    p = Path(directory)
    entries = [
        {
            "name": e.name,
            "type": "dir" if e.is_dir() else "file",
            "size": e.stat().st_size if e.is_file() else 0,
        }
        for e in sorted(p.iterdir(), key=lambda e: e.name)
    ]
    return {"directory": directory, "entries": entries}


@registry.register(
    name="code.delete_file",
    description="Delete a file from the workspace",
    input_schema={
        "type": "object",
        "properties": {"file_path": {"type": "string"}},
        "required": ["file_path"],
    },
)
@instrument(namespace="code", tool="delete_file")
def delete_file(file_path: str) -> dict:
    """Deletes a file from the workspace.

    Args:
        file_path: Path to the file to delete.

    Returns:
        A dict naming the deleted file.

    Raises:
        NexusError: If the file does not exist.
    """
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
    """Searches a regex pattern across all files under a directory.

    Files that cannot be read as text are skipped.

    Args:
        pattern: The regular expression to search for.
        directory: The root directory to search recursively.

    Returns:
        A dict with the pattern and a list of {file, line, text} matches.
    """
    rate_limit("code")
    matches = []
    for path in Path(directory).rglob("*"):
        if path.is_file():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except (UnicodeDecodeError, OSError):
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if re.search(pattern, line):
                    matches.append({
                        "file": str(path),
                        "line": i,
                        "text": line.strip(),
                    })
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
    """Replaces the first occurrence of old_string with new_string.

    Args:
        file_path: The file to patch.
        old_string: The exact text to replace.
        new_string: The replacement text.

    Returns:
        A dict naming the patched file.

    Raises:
        NexusError: If old_string is not found in the file.
    """
    rate_limit("code")
    p = Path(file_path)
    content = p.read_text(encoding="utf-8")
    if old_string not in content:
        raise NexusError(f"apply_patch: old_string not found in {file_path}")
    p.write_text(content.replace(old_string, new_string, 1), encoding="utf-8")
    return {"file_path": file_path, "patched": True}


_TEMPLATES_DIR = Path(__file__).parent.parent.parent.parent / "templates"
_jinja = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _render(template_path: str, **ctx) -> str:
    """Renders a named template from the templates directory.

    Args:
        template_path: Template path relative to the templates root.
        **ctx: Variables passed to the template.

    Returns:
        The rendered template text.
    """
    return _jinja.get_template(template_path).render(**ctx)


def _write(workspace: str, rel_path: str, content: str) -> str:
    """Writes content to a workspace-relative path, creating parents.

    Args:
        workspace: The root workspace directory.
        rel_path: Path relative to the workspace.
        content: Text to write.

    Returns:
        The absolute path written.
    """
    full = Path(workspace) / rel_path
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
        "required": [
            "workspace", "app_name", "features", "db_models", "api_routes"
        ],
    },
)
@instrument(namespace="code", tool="scaffold_fastapi_project")
def scaffold_fastapi_project(
    workspace: str,
    app_name: str,
    features: list[str],
    db_models: list[str],
    api_routes: list[str],
) -> dict:
    """Bootstraps a complete FastAPI project from an AppSpec.

    Args:
        workspace: The root workspace directory.
        app_name: The application name.
        features: List of feature flags (e.g., "auth").
        db_models: List of SQLAlchemy model names.
        api_routes: List of API route paths.

    Returns:
        A dict with files_created, api_routes, env_vars_required,
        and dockerfile_path.
    """
    rate_limit("code")
    files = []
    files.append(_write(
        workspace, "backend/app/database.py",
        _render("fastapi/database.py.j2"),
    ))
    files.append(_write(
        workspace, "backend/app/main.py",
        _render(
            "fastapi/main.py.j2",
            app_name=app_name,
            db_models=db_models,
            api_routes=api_routes,
        ),
    ))
    if "auth" in features:
        files.append(_write(
            workspace, "backend/app/models/user.py",
            _render("fastapi/model.py.j2", model_name="User", fields=[
                {"name": "email", "column_type": "String(255)", "nullable": False},
                {"name": "password_hash", "column_type": "String(255)", "nullable": False},
                {"name": "name", "column_type": "String(255)", "nullable": True},
            ]),
        ))
        files.append(_write(
            workspace, "backend/app/routes/auth.py",
            _render("fastapi/auth.py.j2"),
        ))
    files.append(_write(
        workspace, "backend/app/routes/admin.py",
        _render("fastapi/admin.py.j2"),
    ))
    files.append(_write(
        workspace, "backend/Dockerfile", _render("fastapi/Dockerfile.j2")
    ))
    files.append(_write(
        workspace, "backend/requirements.txt",
        "fastapi\nuvicorn\nsqlalchemy\nalembic\nbcrypt\npyjwt\n"
        "psycopg2-binary\nboto3\n",
    ))
    files.append(_write(
        workspace, "docker-compose.yml",
        _render(
            "docker-compose.yml.j2",
            db_name="nexusdb",
            db_user="nexus",
            db_password="nexuspassword",
        ),
    ))
    env_vars = ["DATABASE_URL", "JWT_SECRET", "AWS_REGION", "CLUSTER_NAME"]
    return {
        "files_created": files,
        "api_routes": api_routes,
        "env_vars_required": env_vars,
        "dockerfile_path": f"{workspace}/backend/Dockerfile",
    }


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
def scaffold_react_project(
    workspace: str,
    app_name: str,
    pages: list[str],
    api_routes: list[str],
) -> dict:
    """Bootstraps a complete React + TypeScript project from an AppSpec.

    Args:
        workspace: The root workspace directory.
        app_name: The application name.
        pages: List of page names (CamelCase React component names).
        api_routes: List of API route paths.

    Returns:
        A dict with files_created, dockerfile_path, and static_build_cmd.
    """
    rate_limit("code")
    files = []
    files.append(_write(
        workspace, "frontend/src/App.tsx",
        _render("react/App.tsx.j2", pages=pages),
    ))
    files.append(_write(
        workspace, "frontend/src/contexts/AuthContext.tsx",
        _render("react/AuthContext.tsx.j2"),
    ))
    files.append(_write(
        workspace, "frontend/src/lib/api.ts",
        _render("react/api.ts.j2"),
    ))
    files.append(_write(
        workspace, "frontend/src/pages/Login.tsx",
        _render("react/Login.tsx.j2"),
    ))
    files.append(_write(
        workspace, "frontend/src/pages/AdminDashboard.tsx",
        _render("react/AdminDashboard.tsx.j2"),
    ))
    for page in pages:
        files.append(_write(
            workspace, f"frontend/src/pages/{page}.tsx",
            _render(
                "react/page.tsx.j2",
                page_name=page, model_name=page,
                route_prefix=page.lower(), page_title=page, fields=[],
            ),
        ))
    files.append(_write(
        workspace, "frontend/Dockerfile", _render("react/Dockerfile.j2")
    ))
    pkg = (
        '{"name":"frontend","version":"1.0.0",'
        '"scripts":{"dev":"vite","build":"vite build"},'
        '"dependencies":{"react":"^18","react-dom":"^18",'
        '"react-router-dom":"^6","axios":"^1","recharts":"^2"},'
        '"devDependencies":{"@types/react":"^18","typescript":"^5",'
        '"vite":"^5","@vitejs/plugin-react":"^4",'
        '"tailwindcss":"^3","postcss":"^8","autoprefixer":"^10"}}'
    )
    files.append(_write(workspace, "frontend/package.json", pkg))
    files.append(_write(
        workspace, "frontend/tailwind.config.js",
        "/** @type {import('tailwindcss').Config} */\n"
        "export default {\n"
        "  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],\n"
        "  theme: { extend: {} },\n"
        "  plugins: [],\n"
        "}\n",
    ))
    files.append(_write(
        workspace, "frontend/postcss.config.js",
        "export default {\n"
        "  plugins: { tailwindcss: {}, autoprefixer: {} },\n"
        "}\n",
    ))
    files.append(_write(
        workspace, "frontend/src/index.css",
        "@tailwind base;\n@tailwind components;\n@tailwind utilities;\n",
    ))
    files.append(_write(
        workspace, "frontend/src/main.tsx",
        "import React from 'react'\n"
        "import ReactDOM from 'react-dom/client'\n"
        "import App from './App'\n"
        "import './index.css'\n\n"
        "ReactDOM.createRoot(document.getElementById('root')!).render(\n"
        "  <React.StrictMode><App /></React.StrictMode>\n"
        ")\n",
    ))
    files.append(_write(
        workspace, "frontend/index.html",
        "<!DOCTYPE html>\n"
        "<html lang=\"en\">\n"
        "  <head>\n"
        "    <meta charset=\"UTF-8\" />\n"
        "    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />\n"
        f"    <title>{app_name}</title>\n"
        "  </head>\n"
        "  <body>\n"
        "    <div id=\"root\"></div>\n"
        "    <script type=\"module\" src=\"/src/main.tsx\"></script>\n"
        "  </body>\n"
        "</html>\n",
    ))
    files.append(_write(
        workspace, "frontend/vite.config.ts",
        "import { defineConfig } from 'vite'\n"
        "import react from '@vitejs/plugin-react'\n\n"
        "export default defineConfig({\n"
        "  plugins: [react()],\n"
        "  server: { proxy: { '/api': 'http://localhost:8000' } },\n"
        "})\n",
    ))
    return {
        "files_created": files,
        "dockerfile_path": f"{workspace}/frontend/Dockerfile",
        "static_build_cmd": "npm run build",
    }


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
def scaffold_api_route(
    workspace: str, model_name: str, fields: list[dict]
) -> dict:
    """Generates a FastAPI CRUD route for a model.

    Args:
        workspace: The root workspace directory.
        model_name: The model name (CamelCase noun).
        fields: List of field definition dicts.

    Returns:
        A dict with file_path and model_name.
    """
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
        "required": [
            "workspace", "page_name", "model_name", "route_prefix", "fields"
        ],
    },
)
@instrument(namespace="code", tool="scaffold_react_page")
def scaffold_react_page(
    workspace: str,
    page_name: str,
    model_name: str,
    route_prefix: str,
    fields: list[dict],
) -> dict:
    """Generates a React CRUD page for a model.

    Args:
        workspace: The root workspace directory.
        page_name: The React component name (CamelCase).
        model_name: The corresponding model name (CamelCase).
        route_prefix: The lowercase URL route prefix.
        fields: List of field definition dicts.

    Returns:
        A dict with file_path and page_name.
    """
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
def scaffold_db_model(
    workspace: str, model_name: str, fields: list[dict]
) -> dict:
    """Generates a SQLAlchemy model file.

    Args:
        workspace: The root workspace directory.
        model_name: The model name (CamelCase).
        fields: List of field definition dicts.

    Returns:
        A dict with file_path and model_name.
    """
    rate_limit("code")
    content = _render("fastapi/model.py.j2", model_name=model_name, fields=fields)
    path = _write(workspace, f"backend/app/models/{model_name.lower()}.py", content)
    return {"file_path": path, "model_name": model_name}


@registry.register(
    name="code.scaffold_migration",
    description="Generate an Alembic migration stub for a model",
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "model_name": {"type": "string"},
        },
        "required": ["workspace", "model_name"],
    },
)
@instrument(namespace="code", tool="scaffold_migration")
def scaffold_migration(workspace: str, model_name: str) -> dict:
    """Generates an Alembic migration stub for a model.

    Args:
        workspace: The root workspace directory.
        model_name: The model name (CamelCase).

    Returns:
        A dict with file_path.
    """
    rate_limit("code")
    mn = model_name.lower()
    content = (
        f'"""create {mn} table"""\n'
        "from alembic import op\n"
        "import sqlalchemy as sa\n\n"
        "def upgrade():\n"
        f'    op.create_table("{mn}s",\n'
        '        sa.Column("id", sa.Integer, primary_key=True),\n'
        '        sa.Column("created_at", sa.DateTime),\n'
        "    )\n\n"
        "def downgrade():\n"
        f'    op.drop_table("{mn}s")\n'
    )
    path = _write(
        workspace,
        f"backend/alembic/versions/create_{mn}.py",
        content,
    )
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
def scaffold_k8s_manifest(
    workspace: str,
    name: str,
    namespace: str,
    image: str,
    port: int,
    env_vars: list[dict],
) -> dict:
    """Generates Kubernetes Deployment + Service YAML for a component.

    Args:
        workspace: The root workspace directory.
        name: The component name.
        namespace: The Kubernetes namespace.
        image: The container image URI.
        port: The container port.
        env_vars: List of environment variable definition dicts.

    Returns:
        A dict with deployment_path and service_path.
    """
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
    """Generates a basic Helm chart skeleton for a component.

    Args:
        workspace: The root workspace directory.
        name: The component name.

    Returns:
        A dict with chart_path.
    """
    rate_limit("code")
    chart = f'apiVersion: v2\nname: {name}\nversion: 0.1.0\n'
    path = _write(workspace, f"helm/{name}/Chart.yaml", chart)
    return {"chart_path": str(Path(path).parent)}


def _extract_fields(
    paths: dict, path: str, method: str
) -> list[tuple[str, str]]:
    """Extracts (name, type) pairs from an OpenAPI path's request body."""
    try:
        schema = (
            paths[path][method]["requestBody"]["content"]
            ["application/json"]["schema"]
        )
        return [
            (name, info.get("type", "string"))
            for name, info in schema.get("properties", {}).items()
        ]
    except (KeyError, TypeError):
        return []


def _gen_api_ts(
    login_fields: list[tuple[str, str]],
    register_fields: list[tuple[str, str]],
) -> str:
    """Generates a spec-aligned api.ts with typed login/register functions."""
    ts_type = {"string": "string", "integer": "number", "boolean": "boolean"}

    def iface(name: str, fields: list[tuple[str, str]]) -> str:
        members = "; ".join(
            f"{f}: {ts_type.get(t, 'string')}" for f, t in fields
        )
        return f"export interface {name} {{ {members} }}"

    login_iface = iface("LoginRequest", login_fields)
    register_iface = iface("RegisterRequest", register_fields)

    return (
        "import axios from 'axios'\n\n"
        "const BASE_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'\n"
        "export const api = axios.create({ baseURL: BASE_URL })\n\n"
        "api.interceptors.request.use(config => {\n"
        "  const token = localStorage.getItem('nexus_token')\n"
        "  if (token) config.headers.Authorization = `Bearer ${token}`\n"
        "  return config\n"
        "})\n\n"
        "api.interceptors.response.use(\n"
        "  r => r,\n"
        "  err => {\n"
        "    if (err.response?.status === 401) {\n"
        "      localStorage.removeItem('nexus_token')\n"
        "      window.location.href = '/login'\n"
        "    }\n"
        "    return Promise.reject(err)\n"
        "  }\n"
        ")\n\n"
        f"{login_iface}\n"
        f"{register_iface}\n"
        "export interface AuthResponse { access_token: string; token_type: string }\n\n"
        "export async function loginApi(data: LoginRequest): Promise<AuthResponse> {\n"
        "  const r = await api.post<AuthResponse>('/auth/login', data)\n"
        "  return r.data\n"
        "}\n\n"
        "export async function registerApi(data: RegisterRequest): Promise<AuthResponse> {\n"
        "  const r = await api.post<AuthResponse>('/auth/register', data)\n"
        "  return r.data\n"
        "}\n"
    )


def _gen_auth_context(
    login_fields: list[tuple[str, str]],
    register_fields: list[tuple[str, str]],
) -> str:
    """Generates AuthContext.tsx using typed loginApi/registerApi."""
    login_params = ", ".join(f"{f}: string" for f, _ in login_fields)
    login_obj = ", ".join(f for f, _ in login_fields)
    register_params = ", ".join(f"{f}: string" for f, _ in register_fields)
    register_obj = ", ".join(f for f, _ in register_fields)

    return (
        "import { createContext, useContext, useState, ReactNode } from 'react'\n"
        "import { loginApi, registerApi } from '../lib/api'\n\n"
        "interface AuthContextType {\n"
        "  isAuthenticated: boolean\n"
        f"  login: ({login_params}) => Promise<void>\n"
        f"  register: ({register_params}) => Promise<void>\n"
        "  logout: () => void\n"
        "}\n\n"
        "const AuthContext = createContext<AuthContextType | null>(null)\n\n"
        "export function AuthProvider({ children }: { children: ReactNode }) {\n"
        "  const [isAuthenticated, setIsAuthenticated] = useState(\n"
        "    !!localStorage.getItem('nexus_token')\n"
        "  )\n\n"
        f"  const login = async ({login_params}) => {{\n"
        f"    const r = await loginApi({{ {login_obj} }})\n"
        "    localStorage.setItem('nexus_token', r.access_token)\n"
        "    setIsAuthenticated(true)\n"
        "  }\n\n"
        f"  const register = async ({register_params}) => {{\n"
        f"    const r = await registerApi({{ {register_obj} }})\n"
        "    localStorage.setItem('nexus_token', r.access_token)\n"
        "    setIsAuthenticated(true)\n"
        "  }\n\n"
        "  const logout = () => {\n"
        "    localStorage.removeItem('nexus_token')\n"
        "    setIsAuthenticated(false)\n"
        "  }\n\n"
        "  return (\n"
        "    <AuthContext.Provider value={{ isAuthenticated, login, register, logout }}>\n"
        "      {children}\n"
        "    </AuthContext.Provider>\n"
        "  )\n"
        "}\n\n"
        "export function useAuth() {\n"
        "  const ctx = useContext(AuthContext)\n"
        "  if (!ctx) throw new Error('useAuth must be used within AuthProvider')\n"
        "  return ctx\n"
        "}\n"
    )


def _input_block(f: str, label: str | None = None) -> str:
    """Renders a labeled form input block for a single field."""
    input_type = (
        "password" if f == "password"
        else "email" if f == "email"
        else "text"
    )
    display = label or f.replace("_", " ").capitalize()
    placeholder = "••••••••" if f == "password" else f"Enter {display.lower()}"
    cap = f.capitalize()
    return (
        f"            <div>\n"
        f"              <label className=\"block text-sm font-medium text-gray-700 mb-1.5\">{display}</label>\n"
        f"              <input\n"
        f"                type=\"{input_type}\"\n"
        f"                value={{{f}}}\n"
        f"                onChange={{e => set{cap}(e.target.value)}}\n"
        f"                placeholder=\"{placeholder}\"\n"
        f"                required\n"
        f"                className=\"w-full px-3 py-2.5 border border-gray-300 rounded-lg text-sm"
        f" focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent transition\"\n"
        f"              />\n"
        f"            </div>"
    )


def _gen_login_tsx(login_fields: list[tuple[str, str]]) -> str:
    """Generates Login.tsx with split-panel layout and fields from spec."""
    state_lines = "\n".join(
        f"  const [{f}, set{f.capitalize()}] = useState('')" for f, _ in login_fields
    )
    input_blocks = "\n".join(_input_block(f) for f, _ in login_fields)
    call_args = ", ".join(f for f, _ in login_fields)

    return (
        "import { useState } from 'react'\n"
        "import { useNavigate, Link } from 'react-router-dom'\n"
        "import { useAuth } from '../contexts/AuthContext'\n\n"
        "export default function Login() {\n"
        "  const { login } = useAuth()\n"
        "  const navigate = useNavigate()\n"
        f"{state_lines}\n"
        "  const [error, setError] = useState('')\n"
        "  const [loading, setLoading] = useState(false)\n\n"
        "  const handleSubmit = async (e: React.FormEvent) => {\n"
        "    e.preventDefault()\n"
        "    setLoading(true); setError('')\n"
        "    try {\n"
        f"      await login({call_args})\n"
        "      navigate('/')\n"
        "    } catch { setError('Invalid email or password') }\n"
        "    finally { setLoading(false) }\n"
        "  }\n\n"
        "  return (\n"
        "    <div className=\"min-h-screen flex\">\n"
        "      <div className=\"hidden lg:flex lg:w-1/2 bg-gray-900 flex-col justify-center px-16\">\n"
        "        <p className=\"text-blue-400 text-sm font-semibold uppercase tracking-widest mb-4\">Nexus</p>\n"
        "        <h1 className=\"text-4xl font-bold text-white mb-4 leading-snug\">Welcome back</h1>\n"
        "        <p className=\"text-gray-400 text-lg\">Sign in to manage your workspace.</p>\n"
        "      </div>\n"
        "      <div className=\"flex-1 flex items-center justify-center px-8 bg-white\">\n"
        "        <div className=\"w-full max-w-md\">\n"
        "          <h2 className=\"text-2xl font-bold text-gray-900 mb-1\">Sign in</h2>\n"
        "          <p className=\"text-sm text-gray-500 mb-8\">\n"
        "            No account?{' '}\n"
        "            <Link to=\"/register\" className=\"text-blue-600 hover:underline font-medium\">Create one free</Link>\n"
        "          </p>\n"
        "          {error && (\n"
        "            <div className=\"mb-5 px-4 py-3 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm\">{error}</div>\n"
        "          )}\n"
        "          <form onSubmit={handleSubmit} className=\"space-y-4\">\n"
        f"{input_blocks}\n"
        "            <button\n"
        "              type=\"submit\" disabled={loading}\n"
        "              className=\"w-full py-2.5 px-4 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white text-sm font-semibold rounded-lg transition-colors mt-1\"\n"
        "            >\n"
        "              {loading ? 'Signing in…' : 'Sign in'}\n"
        "            </button>\n"
        "          </form>\n"
        "        </div>\n"
        "      </div>\n"
        "    </div>\n"
        "  )\n"
        "}\n"
    )


def _gen_register_tsx(register_fields: list[tuple[str, str]]) -> str:
    """Generates Register.tsx with split-panel layout and fields from spec."""
    state_lines = "\n".join(
        f"  const [{f}, set{f.capitalize()}] = useState('')" for f, _ in register_fields
    )
    input_blocks = "\n".join(_input_block(f) for f, _ in register_fields)
    call_args = ", ".join(f for f, _ in register_fields)

    return (
        "import { useState } from 'react'\n"
        "import { useNavigate, Link } from 'react-router-dom'\n"
        "import { useAuth } from '../contexts/AuthContext'\n\n"
        "export default function Register() {\n"
        "  const { register } = useAuth()\n"
        "  const navigate = useNavigate()\n"
        f"{state_lines}\n"
        "  const [error, setError] = useState('')\n"
        "  const [loading, setLoading] = useState(false)\n\n"
        "  const handleSubmit = async (e: React.FormEvent) => {\n"
        "    e.preventDefault()\n"
        "    setLoading(true); setError('')\n"
        "    try {\n"
        f"      await register({call_args})\n"
        "      navigate('/')\n"
        "    } catch { setError('Registration failed — email may already be in use') }\n"
        "    finally { setLoading(false) }\n"
        "  }\n\n"
        "  return (\n"
        "    <div className=\"min-h-screen flex\">\n"
        "      <div className=\"hidden lg:flex lg:w-1/2 bg-gray-900 flex-col justify-center px-16\">\n"
        "        <p className=\"text-blue-400 text-sm font-semibold uppercase tracking-widest mb-4\">Nexus</p>\n"
        "        <h1 className=\"text-4xl font-bold text-white mb-4 leading-snug\">Create your account</h1>\n"
        "        <p className=\"text-gray-400 text-lg\">Join and start building today.</p>\n"
        "      </div>\n"
        "      <div className=\"flex-1 flex items-center justify-center px-8 bg-white\">\n"
        "        <div className=\"w-full max-w-md\">\n"
        "          <h2 className=\"text-2xl font-bold text-gray-900 mb-1\">Create account</h2>\n"
        "          <p className=\"text-sm text-gray-500 mb-8\">\n"
        "            Already have an account?{' '}\n"
        "            <Link to=\"/login\" className=\"text-blue-600 hover:underline font-medium\">Sign in</Link>\n"
        "          </p>\n"
        "          {error && (\n"
        "            <div className=\"mb-5 px-4 py-3 rounded-lg bg-red-50 border border-red-200 text-red-700 text-sm\">{error}</div>\n"
        "          )}\n"
        "          <form onSubmit={handleSubmit} className=\"space-y-4\">\n"
        f"{input_blocks}\n"
        "            <button\n"
        "              type=\"submit\" disabled={loading}\n"
        "              className=\"w-full py-2.5 px-4 bg-blue-600 hover:bg-blue-700 disabled:opacity-60 text-white text-sm font-semibold rounded-lg transition-colors mt-1\"\n"
        "            >\n"
        "              {loading ? 'Creating account…' : 'Create account'}\n"
        "            </button>\n"
        "          </form>\n"
        "        </div>\n"
        "      </div>\n"
        "    </div>\n"
        "  )\n"
        "}\n"
    )


@registry.register(
    name="code.generate_api_client",
    description=(
        "Parse the OpenAPI spec and generate spec-aligned TypeScript auth "
        "files (api.ts, AuthContext.tsx, Login.tsx, Register.tsx)"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "api_spec_path": {"type": "string"},
        },
        "required": ["workspace", "api_spec_path"],
    },
)
@instrument(namespace="code", tool="generate_api_client")
def generate_api_client(workspace: str, api_spec_path: str) -> dict:
    """Parses OpenAPI YAML and overwrites auth TypeScript files to match spec.

    Reads field names and types directly from the spec so frontend code
    cannot diverge from the backend contract.  Overwrites:
      frontend/src/lib/api.ts
      frontend/src/contexts/AuthContext.tsx
      frontend/src/pages/Login.tsx
      frontend/src/pages/Register.tsx

    Args:
        workspace: The root workspace directory.
        api_spec_path: Absolute path to the openapi.yaml file.

    Returns:
        A dict with files_created, login_fields, and register_fields.
    """
    import yaml as _yaml

    rate_limit("code")
    spec = _yaml.safe_load(Path(api_spec_path).read_text(encoding="utf-8"))
    paths = spec.get("paths", {})

    login_fields = _extract_fields(paths, "/auth/login", "post")
    if not login_fields:
        login_fields = [("email", "string"), ("password", "string")]

    register_fields = _extract_fields(paths, "/auth/register", "post")
    if not register_fields:
        register_fields = [
            ("email", "string"),
            ("password", "string"),
            ("name", "string"),
        ]

    files = [
        _write(
            workspace, "frontend/src/lib/api.ts",
            _gen_api_ts(login_fields, register_fields),
        ),
        _write(
            workspace, "frontend/src/contexts/AuthContext.tsx",
            _gen_auth_context(login_fields, register_fields),
        ),
        _write(
            workspace, "frontend/src/pages/Login.tsx",
            _gen_login_tsx(login_fields),
        ),
        _write(
            workspace, "frontend/src/pages/Register.tsx",
            _gen_register_tsx(register_fields),
        ),
    ]
    return {
        "files_created": files,
        "login_fields": login_fields,
        "register_fields": register_fields,
    }


def _gen_fastapi_auth(login_fields: list, register_fields: list) -> str:
    """Generates backend/app/routes/auth.py aligned with the OpenAPI spec."""
    type_map = {"string": "str", "integer": "int", "number": "float", "boolean": "bool"}

    login_py = "\n".join(
        f"    {name}: {type_map.get(ts_type, 'str')}"
        for name, ts_type in login_fields
    )
    register_py = "\n".join(
        f"    {name}: {type_map.get(ts_type, 'str')}"
        for name, ts_type in register_fields
    )
    extra_fields = [
        name for name, _ in register_fields
        if name not in ("email", "password")
    ]
    if extra_fields:
        user_kwargs = "email=req.email, password_hash=hashed, " + ", ".join(
            f"{name}=req.{name}" for name in extra_fields
        )
    else:
        user_kwargs = "email=req.email, password_hash=hashed"

    return f'''from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from pydantic import BaseModel
import jwt, bcrypt
from datetime import datetime, timedelta
from app.database import get_db
from app.models.user import User

router = APIRouter()
security = HTTPBearer()
JWT_SECRET = __import__("os").environ.get("JWT_SECRET", "change-me")


class LoginRequest(BaseModel):
{login_py}


class RegisterRequest(BaseModel):
{register_py}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    id: int
    email: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


def _make_token(user_id: int) -> str:
    return jwt.encode(
        {{"sub": str(user_id), "exp": datetime.utcnow() + timedelta(hours=24)}},
        JWT_SECRET, algorithm="HS256",
    )


def get_current_user(
    creds: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=["HS256"])
        user = db.query(User).filter(User.id == int(payload["sub"])).first()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token")


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return current_user


@router.post("/register", response_model=TokenResponse, status_code=201)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    hashed = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
    user = User({user_kwargs})
    db.add(user)
    db.commit()
    db.refresh(user)
    return TokenResponse(access_token=_make_token(user.id))


@router.post("/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not bcrypt.checkpw(req.password.encode(), user.password_hash.encode()):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return TokenResponse(access_token=_make_token(user.id))


@router.get("/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    return current_user
'''


def _gen_user_model(register_fields: list) -> str:
    """Generates backend/app/models/user.py aligned with the OpenAPI register spec."""
    type_map = {
        "string": "String",
        "integer": "Integer",
        "number": "Float",
        "boolean": "Boolean",
    }
    extra_cols = []
    for name, ts_type in register_fields:
        if name in ("email", "password"):
            continue
        sa_type = type_map.get(ts_type, "String")
        extra_cols.append(f"    {name} = Column({sa_type}, nullable=True)")

    extra_cols_str = "\n".join(extra_cols)
    if extra_cols_str:
        extra_cols_str = "\n" + extra_cols_str

    return f'''from sqlalchemy import Column, Integer, String, Boolean, DateTime, Float
from sqlalchemy.sql import func
from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
{extra_cols_str}
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
'''


@registry.register(
    name="code.generate_fastapi_auth",
    description=(
        "Overwrites backend/app/routes/auth.py and backend/app/models/user.py "
        "with implementations that exactly match the OpenAPI spec field names "
        "for /auth/login and /auth/register. Call this AFTER scaffold_fastapi_project."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string", "description": "Root workspace directory"},
            "api_spec_path": {"type": "string", "description": "Path to the OpenAPI YAML spec"},
        },
        "required": ["workspace", "api_spec_path"],
    },
)
@instrument(namespace="code", tool="generate_fastapi_auth")
def generate_fastapi_auth(workspace: str, api_spec_path: str) -> dict:
    """Generates spec-aligned FastAPI auth files.

    Reads the OpenAPI spec and overwrites backend/app/routes/auth.py and
    backend/app/models/user.py so field names match the spec exactly.

    Args:
        workspace: Root workspace directory.
        api_spec_path: Path to the OpenAPI YAML spec.

    Returns:
        Dict with files_created, login_fields, register_fields.
    """
    import yaml as _yaml

    rate_limit("code")
    spec = _yaml.safe_load(Path(api_spec_path).read_text(encoding="utf-8"))
    paths = spec.get("paths", {})

    login_fields = _extract_fields(paths, "/auth/login", "post")
    if not login_fields:
        login_fields = [("email", "string"), ("password", "string")]

    register_fields = _extract_fields(paths, "/auth/register", "post")
    if not register_fields:
        register_fields = [
            ("email", "string"),
            ("password", "string"),
            ("name", "string"),
        ]

    files = [
        _write(workspace, "backend/app/routes/auth.py",
               _gen_fastapi_auth(login_fields, register_fields)),
        _write(workspace, "backend/app/models/user.py",
               _gen_user_model(register_fields)),
    ]
    return {
        "files_created": files,
        "login_fields": login_fields,
        "register_fields": register_fields,
    }


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
    """Runs ruff (Python) or eslint (TypeScript) on the workspace.

    Skips eslint if node_modules are not installed.

    Args:
        workspace: The root workspace directory.
        language: "python" or "typescript".

    Returns:
        A dict with returncode, stdout, and passed flag.
    """
    rate_limit("code")
    if language == "python":
        return _run_subprocess(
            ["ruff", "check", workspace], timeout=60,
            ok_key="passed", ok_fn=lambda r: r.returncode == 0,
        )
    # TypeScript: only run if node_modules exist — otherwise npx hangs downloading eslint
    ws = Path(workspace)
    eslint_bin = ws / "node_modules" / ".bin" / "eslint"
    if not eslint_bin.exists():
        return {"returncode": 0, "stdout": "eslint skipped — node_modules not installed", "passed": True}
    return _run_subprocess(
        [str(eslint_bin), str(ws), "--ext", ".ts,.tsx", "--max-warnings=0"],
        cwd=str(ws), timeout=60,
        ok_key="passed", ok_fn=lambda r: r.returncode == 0,
    )


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
    """Runs black (Python) or prettier (TypeScript) on the workspace.

    Skips prettier if node_modules are not installed.

    Args:
        workspace: The root workspace directory.
        language: "python" or "typescript".

    Returns:
        A dict with returncode, formatted flag, and stdout.
    """
    rate_limit("code")
    if language == "python":
        return _run_subprocess(["black", workspace], timeout=60, ok_key="formatted", ok_fn=lambda r: r.returncode == 0)
    ws = Path(workspace)
    prettier_bin = ws / "node_modules" / ".bin" / "prettier"
    if not prettier_bin.exists():
        return {"returncode": 0, "formatted": True, "stdout": "prettier skipped — node_modules not installed"}
    return _run_subprocess(
        [str(prettier_bin), "--write", str(ws)], cwd=str(ws), timeout=60,
        ok_key="formatted", ok_fn=lambda r: r.returncode == 0,
    )
