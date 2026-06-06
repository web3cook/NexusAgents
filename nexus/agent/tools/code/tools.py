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
        '"vite":"^5","@vitejs/plugin-react":"^4"}}'
    )
    files.append(_write(workspace, "frontend/package.json", pkg))
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
