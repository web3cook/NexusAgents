from __future__ import annotations

import json
import logging
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.core.state import AppSpec, BuildState

logger = logging.getLogger("nexus.parallel")

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"


def _render(template_path: Path, **ctx) -> str:
    """Renders a Jinja2 template with strict undefined handling.

    Args:
        template_path: Path to the template file.
        **ctx: Variables passed to the template.

    Returns:
        The rendered template text.
    """
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    return env.get_template(template_path.name).render(**ctx)


def pre_render_build_templates(app_spec: AppSpec, workspace: str) -> list[str]:
    """Renders Dockerfiles and K8s manifests from existing templates.

    Performs no LLM call. Existing Dockerfiles and compose files are left
    untouched so a subagent's own output is not overwritten.

    Args:
        app_spec: The application spec (currently unused by the templates
            but kept for signature stability).
        workspace: The root workspace directory to write into.

    Returns:
        Absolute paths of the files that were written.
    """
    ws = Path(workspace)
    written: list[str] = []

    # Backend Dockerfile (no template variables).
    tmpl = _TEMPLATES_DIR / "fastapi" / "Dockerfile.j2"
    if tmpl.exists():
        backend_dir = ws / "backend"
        backend_dir.mkdir(parents=True, exist_ok=True)
        out = backend_dir / "Dockerfile"
        if not out.exists():  # Do not overwrite a subagent-written file.
            out.write_text(tmpl.read_text())
            written.append(str(out))
            logger.debug("rendered %s", out)

    # Frontend Dockerfile (no template variables).
    tmpl = _TEMPLATES_DIR / "react" / "Dockerfile.j2"
    if tmpl.exists():
        frontend_dir = ws / "frontend"
        frontend_dir.mkdir(parents=True, exist_ok=True)
        out = frontend_dir / "Dockerfile"
        if not out.exists():
            out.write_text(tmpl.read_text())
            written.append(str(out))
            logger.debug("rendered %s", out)

    # K8s manifests.
    k8s_dir = ws / "k8s"
    k8s_dir.mkdir(parents=True, exist_ok=True)

    depl_tmpl = _TEMPLATES_DIR / "k8s" / "deployment.yaml.j2"
    svc_tmpl = _TEMPLATES_DIR / "k8s" / "service.yaml.j2"
    namespace = "nexus"

    _backend_env_vars = [
        {"name": "DATABASE_URL", "key": "database-url"},
        {"name": "JWT_SECRET", "key": "jwt-secret"},
        {"name": "AWS_REGION", "key": "aws-region"},
    ]
    _frontend_env_vars: list = []

    for role, port, env_vars, health in [
        ("backend", 8000, _backend_env_vars, "/health"),
        ("frontend", 80, _frontend_env_vars, "/"),
    ]:
        if depl_tmpl.exists():
            out = k8s_dir / f"{role}-deployment.yaml"
            out.write_text(_render(
                depl_tmpl,
                name=f"nexus-{role}",
                namespace=namespace,
                image=f"<ECR_REGISTRY>/nexus-{role}:latest",
                port=port,
                env_vars=env_vars,
                health_path=health,
            ))
            written.append(str(out))

        if svc_tmpl.exists():
            out = k8s_dir / f"{role}-service.yaml"
            out.write_text(_render(
                svc_tmpl, name=f"nexus-{role}", namespace=namespace, port=port
            ))
            written.append(str(out))

    ingress_tmpl = _TEMPLATES_DIR / "k8s" / "ingress.yaml.j2"
    if ingress_tmpl.exists():
        out = k8s_dir / "ingress.yaml"
        out.write_text(_render(ingress_tmpl, namespace=namespace))
        written.append(str(out))

    # docker-compose.yml for local testing.
    compose_tmpl = _TEMPLATES_DIR / "docker-compose.yml.j2"
    if compose_tmpl.exists():
        out = ws / "docker-compose.yml"
        if not out.exists():
            out.write_text(_render(
                compose_tmpl,
                db_name="nexusdb",
                db_user="nexus",
                db_password="nexuspassword",
            ))
            written.append(str(out))
            logger.debug("rendered %s", out)

    logger.info(
        "pre_render_build_templates: %d files written to %s",
        len(written), workspace,
    )
    return written


def _run_agent_subprocess(
    agent_class_path: str,
    app_spec_dict: dict,
    workspace: str,
    extra_input: dict | None = None,
) -> dict:
    """Runs a subagent in a child Python process and returns its result.

    Args:
        agent_class_path: Dotted import path to the subagent class.
        app_spec_dict: The app spec serialized as a dict.
        workspace: The root workspace directory.
        extra_input: Additional key/value pairs to include in input_data.

    Returns:
        The subagent's result dict, or an error dict if the child process
        exits without emitting a result.
    """
    root = str(Path(__file__).parent.parent.parent)
    module, class_name = agent_class_path.rsplit(".", 1)

    extra_kvs = ""
    if extra_input:
        for k, v in extra_input.items():
            extra_kvs += f", {k!r}: {v!r}"

    script = (
        f"import json, sys; sys.path.insert(0, {root!r})\n"
        f"from {module} import {class_name}\n"
        f"subagent = {class_name}()\n"
        f"result = subagent.run({{'app_spec': {json.dumps(app_spec_dict)!r}, "
        f"'workspace': {workspace!r}{extra_kvs}}})\n"
        f"print('__RESULT__' + json.dumps(result))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=900,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__"):])
    return {
        "error": f"subprocess exited {proc.returncode}: {proc.stderr[-500:]}"
    }


def _run_backend_subprocess(app_spec_dict: dict, workspace: str) -> dict:
    """Runs the backend builder subagent in a child process.

    Args:
        app_spec_dict: The app spec serialized as a dict.
        workspace: The root workspace directory.

    Returns:
        The backend builder's result dict.
    """
    return _run_agent_subprocess(
        "agent.subagents.backend_builder.BackendBuilderSubagent",
        app_spec_dict, workspace,
    )


def _run_frontend_subprocess(
    app_spec_dict: dict,
    workspace: str,
    api_spec_path: str | None = None,
) -> dict:
    """Runs the frontend builder subagent in a child process.

    Args:
        app_spec_dict: The app spec serialized as a dict.
        workspace: The root workspace directory.
        api_spec_path: Path to the OpenAPI YAML spec, passed to the subagent
            so it can call code.generate_api_client for spec alignment.

    Returns:
        The frontend builder's result dict.
    """
    extra: dict | None = (
        {"api_spec_path": api_spec_path} if api_spec_path else None
    )
    return _run_agent_subprocess(
        "agent.subagents.frontend_builder.FrontendBuilderSubagent",
        app_spec_dict, workspace, extra,
    )


def run_build_parallel(
    app_spec: AppSpec,
    workspace: str,
    state: BuildState,
) -> tuple[dict, dict]:
    """Runs the backend and frontend builders in parallel subprocesses.

    Updates state.agent_statuses as each subprocess starts and finishes.

    Args:
        app_spec: The application spec to build from.
        workspace: The root workspace directory.
        state: The build state to update with agent statuses.

    Returns:
        A (backend_result, frontend_result) tuple.
    """
    from dataclasses import asdict
    from agent.core.state import AgentStatus

    spec_dict = asdict(app_spec)
    state.set_agent_status("BackendBuilderSubagent", AgentStatus.ONGOING)
    state.set_agent_status("FrontendBuilderSubagent", AgentStatus.ONGOING)
    logger.info("  Launching backend + frontend subprocesses in parallel...")

    api_spec_path = getattr(state, "api_spec_path", None)
    with ProcessPoolExecutor(max_workers=2) as pool:
        fut_backend = pool.submit(_run_backend_subprocess, spec_dict, workspace)
        fut_frontend = pool.submit(
            _run_frontend_subprocess, spec_dict, workspace, api_spec_path
        )
        backend_result = fut_backend.result(timeout=960)
        frontend_result = fut_frontend.result(timeout=960)

    if "error" not in backend_result:
        state.set_agent_status(
            "BackendBuilderSubagent", AgentStatus.CODE_COMPLETED
        )
        logger.info("  [green]backend[/green] subprocess complete")
    else:
        logger.warning(
            "  [red]backend[/red] subprocess error: %s",
            backend_result.get("error"),
        )

    if "error" not in frontend_result:
        state.set_agent_status(
            "FrontendBuilderSubagent", AgentStatus.CODE_COMPLETED
        )
        logger.info("  [green]frontend[/green] subprocess complete")
    else:
        logger.warning(
            "  [red]frontend[/red] subprocess error: %s",
            frontend_result.get("error"),
        )

    return backend_result, frontend_result
