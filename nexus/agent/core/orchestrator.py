"""The parent orchestrator that drives the build phase state machine."""

from __future__ import annotations

import atexit
import json
import logging
import re
import signal
import time
import traceback
import uuid
from pathlib import Path

import anthropic

import agent.tools  # noqa: F401  Triggers all @registry.register decorators.
from agent.core.context import compress_phase, summarise_messages
from agent.core.state import (
    AgentStatus,
    AppSpec,
    BackendManifest,
    BuildState,
    CostSummary,
    DeploymentResult,
    FrontendManifest,
    Phase,
    TestReport,
    set_session_id,
)
from agent.tools.registry import registry

logger = logging.getLogger("nexus.orchestrator")
client = anthropic.Anthropic()

SYSTEM_PROMPT = """You are Nexus, an autonomous full-stack application builder and deployer.

Given a user's app description, you will:
1. Use subagent.run_planner to plan the build and show cost estimates
2. [API_SPEC phase is handled automatically — do not call generate_api_spec directly]
3. [BUILD phase runs backend + frontend in parallel automatically — do not call builders directly]
4. Build and push Docker images using docker.* tools
5. Use subagent.run_infra_provisioner to deploy to AWS EKS
6. Run tests using test.* tools
7. Use subagent.run_alerting to start persistent log monitoring

Work through phases in order: PLANNING → API_SPEC → BUILD → INFRA → TEST → MONITORING.
After each phase, summarise what was accomplished before moving to the next.
The workspace directory is provided in the first message."""

PHASE_TOOLS = {
    Phase.PLANNING: ["subagent", "plan"],
    Phase.API_SPEC: [],  # Deterministic, so no LLM call.
    Phase.BUILD: [],  # Parallel subprocess, so no LLM call.
    Phase.INFRA: ["subagent", "aws", "k8s", "docker", "code"],
    Phase.TEST: ["test"],
    Phase.MONITORING: ["subagent", "alert"],
}

_active_state: BuildState | None = None
_active_checkpoint: Path | None = None


def _emergency_save(signum=None, frame=None) -> None:
    """Writes a checkpoint when the process is interrupted or exits.

    Registered with atexit and as the SIGTERM handler.

    Args:
        signum: The signal number, when invoked as a signal handler.
        frame: The current stack frame, when invoked as a signal handler.
    """
    if _active_state and _active_checkpoint:
        try:
            _active_state.checkpoint(_active_checkpoint)
            logger.info(
                "[yellow]Emergency checkpoint saved: %s[/yellow]",
                _active_checkpoint,
            )
        except Exception as exc:
            logger.error("Emergency save failed: %s", exc)


def _latest_checkpoint(workflow_dir: Path) -> Path | None:
    """Finds the most recently modified checkpoint under a workflow dir.

    Args:
        workflow_dir: The root directory containing per-session dirs.

    Returns:
        The newest checkpoint path, or None if none exist.
    """
    checkpoints = sorted(
        workflow_dir.glob("*/checkpoint.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return checkpoints[0] if checkpoints else None


def _phase_from_state(state: BuildState) -> Phase:
    """Infers the correct phase from the artifacts present in state.

    Used on resume to recover the phase when the checkpoint's recorded
    phase may be stale.

    Args:
        state: The build state to inspect.

    Returns:
        The phase implied by the recorded artifacts.
    """
    if state.deployment_result:
        return Phase.MONITORING if state.test_report else Phase.TEST
    if state.frontend_manifest and state.backend_manifest:
        return Phase.INFRA
    if state.app_spec and state.cost_summary and state.api_spec_path:
        return Phase.BUILD
    if state.app_spec and state.cost_summary:
        return Phase.API_SPEC
    return Phase.PLANNING


def _audit_workspace(
    state: BuildState,
    workspace: str,
) -> tuple[BuildState, str]:
    """Recovers manifests from disk that are missing from the checkpoint.

    Scans the workspace for backend, frontend, and API-spec artifacts,
    populates any absent manifests on the state, and corrects the phase.

    Args:
        state: The build state to repair in place.
        workspace: The root workspace directory.

    Returns:
        A (state, audit_report) tuple where audit_report is a multi-line
        human-readable summary of what was found.
    """
    ws = Path(workspace)
    lines: list[str] = [f"[WORKSPACE AUDIT — session {state.session_id}]"]

    # Backend.
    if state.backend_manifest is None:
        backend_root = ws / "backend"
        py_files = (
            sorted(backend_root.rglob("*.py")) if backend_root.exists() else []
        )
        dockerfile = backend_root / "Dockerfile"
        if py_files and dockerfile.exists():
            routes: list[str] = []
            routes_dir = backend_root / "app" / "routes"
            if routes_dir.exists():
                for route_file in routes_dir.glob("*.py"):
                    for m in re.finditer(
                        r'prefix\s*=\s*["\']([^"\']+)["\']',
                        route_file.read_text(),
                    ):
                        routes.append(m.group(1))
            state.backend_manifest = BackendManifest(
                files_created=[str(f) for f in py_files] + [str(dockerfile)],
                api_routes=routes or ["/api"],
                env_vars_required=[
                    "DATABASE_URL", "JWT_SECRET", "AWS_REGION", "CLUSTER_NAME"
                ],
                dockerfile_path=str(dockerfile),
                test_results={"passed": 0, "failed": 0},
            )
            lines.append(
                f"Backend: {len(py_files)} .py files found — "
                f"backend_manifest populated ({len(routes)} routes detected)"
            )
        elif backend_root.exists():
            have_dockerfile = "yes" if dockerfile.exists() else "no"
            lines.append(
                f"Backend: incomplete ({len(py_files)} .py, "
                f"Dockerfile={have_dockerfile})"
            )
        else:
            lines.append("Backend: not started")
    else:
        lines.append(
            "Backend: already in checkpoint "
            f"({len(state.backend_manifest.files_created)} files)"
        )

    # Frontend.
    if state.frontend_manifest is None:
        frontend_root = ws / "frontend"
        if (frontend_root / "frontend").exists():
            frontend_root = frontend_root / "frontend"
            lines.append(
                "Frontend: detected double-nested path — using inner dir"
            )
        tsx_files = (
            sorted(frontend_root.rglob("*.tsx"))
            if frontend_root.exists() else []
        )
        ts_files = (
            sorted(frontend_root.rglob("*.ts"))
            if frontend_root.exists() else []
        )
        dockerfile = frontend_root / "Dockerfile"
        if (tsx_files or ts_files) and dockerfile.exists():
            all_files = tsx_files + ts_files + [dockerfile]
            state.frontend_manifest = FrontendManifest(
                files_created=[str(f) for f in all_files],
                dockerfile_path=str(dockerfile),
                static_build_cmd="npm run build",
                test_results={"passed": 0, "failed": 0},
            )
            lines.append(
                f"Frontend: {len(tsx_files)} .tsx + {len(ts_files)} .ts "
                "files — frontend_manifest populated"
            )
        elif frontend_root.exists():
            have_dockerfile = "yes" if dockerfile.exists() else "no"
            lines.append(
                f"Frontend: incomplete ({len(tsx_files)} .tsx, "
                f"Dockerfile={have_dockerfile})"
            )
        else:
            lines.append("Frontend: not started")
    else:
        lines.append(
            "Frontend: already in checkpoint "
            f"({len(state.frontend_manifest.files_created)} files)"
        )

    # API spec.
    if state.api_spec_path is None:
        candidate = ws / "api" / "openapi.yaml"
        if candidate.exists():
            state.api_spec_path = str(candidate)
            lines.append(f"API spec: found at {candidate}")
        else:
            lines.append("API spec: not found")
    else:
        lines.append(
            f"API spec: already in checkpoint ({state.api_spec_path})"
        )

    # Phase correction.
    correct_phase = _phase_from_state(state)
    if correct_phase != state.current_phase:
        lines.append(
            f"Phase corrected: {state.current_phase.name} → "
            f"{correct_phase.name}"
        )
        state.current_phase = correct_phase
    else:
        lines.append(f"Phase confirmed: {state.current_phase.name}")

    # AWS resources.
    if state.aws_resources:
        lines.append(
            "\n[ALREADY-PROVISIONED AWS RESOURCES — do not recreate these]"
        )
        for key, val in state.aws_resources.items():
            lines.append(f"  {key}: {json.dumps(val)}")
    else:
        lines.append("AWS resources: none provisioned yet")

    lines.append(
        "\nContinuing build from the phase above — "
        "do not redo completed work."
    )
    return state, "\n".join(lines)


def run(
    user_description: str,
    workflow_dir: str = "/tmp/nexus-workflow",
    resume: bool = False,
    session_id: str | None = None,
) -> BuildState:
    """Runs the full build orchestrator from planning to monitoring.

    Drives the phase state machine, calling the LLM for LLM-driven phases
    and deterministic helpers for the API_SPEC and BUILD phases. All
    session files (generated code, checkpoint, API spec) are written to
    <workflow_dir>/<session-id>/.

    Args:
        user_description: Natural-language description of the app to build.
        workflow_dir: Root directory for all session files.
        resume: Whether to resume from an existing checkpoint.
        session_id: Specific session to resume; if omitted while resuming,
            the most recent session is used.

    Returns:
        The final build state.

    Raises:
        FileNotFoundError: If session_id is given but its checkpoint is
            missing.
    """
    global _active_state, _active_checkpoint

    wf_dir = Path(workflow_dir)
    wf_dir.mkdir(parents=True, exist_ok=True)

    resumed = False
    if resume or session_id:
        if session_id:
            checkpoint_path = wf_dir / session_id / "checkpoint.json"
            if not checkpoint_path.exists():
                logger.error(
                    "No checkpoint for session %s in %s", session_id, wf_dir
                )
                raise FileNotFoundError(
                    f"Checkpoint not found: {checkpoint_path}"
                )
        else:
            checkpoint_path = _latest_checkpoint(wf_dir)
            if not checkpoint_path:
                logger.warning(
                    "--resume requested but no checkpoint found — "
                    "starting fresh"
                )
                checkpoint_path = None

        if checkpoint_path:
            state = BuildState.from_checkpoint(checkpoint_path)
            set_session_id(state.session_id)
            resumed = True
            logger.info(
                "[yellow]Resuming session %s from phase %s[/yellow]  "
                "[dim](%s)[/dim]",
                state.session_id, state.current_phase.name,
                checkpoint_path.parent.name,
            )

    if not resumed:
        session_id = str(uuid.uuid4())[:8]
        set_session_id(session_id)
        state = BuildState(
            session_id=session_id, user_description=user_description
        )
        checkpoint_path = wf_dir / session_id / "checkpoint.json"

    # The session directory is the workspace; all generated files land here.
    session_dir = checkpoint_path.parent
    session_dir.mkdir(parents=True, exist_ok=True)
    workspace = str(session_dir)

    # Wire emergency save.
    _active_state = state
    _active_checkpoint = checkpoint_path
    atexit.register(_emergency_save)
    signal.signal(signal.SIGTERM, _emergency_save)

    # Wire status callbacks so subagent tools update the checkpoint on every
    # status change.
    from agent.tools.subagent.tools import set_status_callback
    from agent.core.state import AgentStatus as _AgentStatus

    def _status_cb(agent_name: str, status_str: str) -> None:
        """Persists a subagent status change to the checkpoint.

        Args:
            agent_name: The subagent whose status changed.
            status_str: The new status as a raw string; ignored if it is
                not a valid AgentStatus.
        """
        try:
            status = _AgentStatus(status_str)
        except ValueError:
            return
        state.set_agent_status(agent_name, status)
        state.checkpoint(checkpoint_path)

    set_status_callback(_status_cb)

    build_start = time.monotonic()
    logger.info(
        "[bold]session %s[/bold] — %s",
        state.session_id, user_description[:100],
    )
    logger.info("session dir: %s", session_dir)

    messages: list[dict] = [
        {
            "role": "user",
            "content": [{
                "type": "text",
                "text": (
                    f"Build this app: {user_description}\n"
                    f"Workspace: {workspace}"
                ),
                "cache_control": {"type": "ephemeral"},
            }],
        }
    ]

    if resumed:
        logger.info("[yellow]Auditing workspace before continuing...[/yellow]")
        state, audit_report = _audit_workspace(state, workspace)
        for line in audit_report.splitlines():
            logger.info("  %s", line)
        messages.append({
            "role": "user",
            "content": [{"type": "text", "text": audit_report,
                          "cache_control": {"type": "ephemeral"}}],
        })
        state.checkpoint(checkpoint_path)
        logger.info("  checkpoint updated with audit results")

    current_phase_logged: Phase | None = None
    phase_error_counts: dict[Phase, int] = {}
    max_phase_errors = 3

    while state.current_phase != Phase.COMPLETE:
        if state.current_phase != current_phase_logged:
            phases = list(Phase)
            total = len(phases) - 1  # Exclude COMPLETE.
            idx = phases.index(state.current_phase) + 1
            logger.info(
                "[cyan]──── phase %d/%d: %s ────[/cyan]",
                idx, total, state.current_phase.name,
            )
            current_phase_logged = state.current_phase

        # API_SPEC is deterministic, so it makes no LLM call.
        if state.current_phase == Phase.API_SPEC:
            from agent.tools.plan.tools import generate_api_spec
            spec = state.app_spec
            result = generate_api_spec(
                app_name="NexusApp",
                api_routes=spec.api_routes,
                db_models=spec.db_models,
                features=spec.features,
                output_dir=str(Path(workspace) / "api"),
            )
            state.api_spec_path = result["output_path"]
            state.register_file(result["output_path"], "api_spec")
            logger.info(
                "  API spec written: %s (%d routes)",
                result["output_path"], result["route_count"],
            )
            state.current_phase = Phase.BUILD
            state.checkpoint(checkpoint_path)
            continue

        # BUILD runs as parallel subprocesses, so it makes no LLM call.
        if state.current_phase == Phase.BUILD:
            from agent.core.parallel import (
                pre_render_build_templates,
                run_build_parallel,
            )
            logger.info("  Pre-rendering Docker/K8s templates...")
            rendered = pre_render_build_templates(state.app_spec, workspace)
            for f in rendered:
                state.register_file(f, "template")
            logger.info("  Starting parallel backend + frontend build...")
            backend_result, frontend_result = run_build_parallel(
                state.app_spec, workspace, state
            )
            _update_state(
                state, "subagent.run_backend_builder", backend_result
            )
            _update_state(
                state, "subagent.run_frontend_builder", frontend_result
            )
            new_phase = _infer_next_phase(state)
            if new_phase != state.current_phase:
                logger.info(
                    "[green]✓[/green] BUILD complete → entering %s",
                    new_phase.name,
                )
                state.current_phase = new_phase
                state.checkpoint(checkpoint_path)
            else:
                # Both builders failed, so treat it as a phase error.
                count = phase_error_counts.get(Phase.BUILD, 0) + 1
                phase_error_counts[Phase.BUILD] = count
                logger.warning(
                    "BUILD phase failed (%d/%d)", count, max_phase_errors
                )
                if count >= max_phase_errors:
                    logger.error(
                        "[red]BUILD failed %d times — aborting.[/red]", count
                    )
                    state.current_phase = Phase.COMPLETE
            continue

        # LLM-driven phases.
        namespaces = PHASE_TOOLS.get(state.current_phase)
        tools = (
            registry.get_anthropic_tools(namespaces=namespaces)
            if namespaces
            else registry.get_anthropic_tools()
        )
        if tools:
            tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}

        messages = summarise_messages(messages, keep_last=10)

        response = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=8096,
            system=[{
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }],
            tools=tools,
            messages=messages,
        )
        state.add_cost(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read=getattr(
                response.usage, "cache_read_input_tokens", 0
            ),
            cache_creation=getattr(
                response.usage, "cache_creation_input_tokens", 0
            ),
            model="claude-opus-4-8",
        )

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                state.tool_call_count += 1
                display_name = (
                    registry._registry_name(block.name)
                    if "__" in block.name
                    else block.name
                )
                t0 = time.monotonic()
                logger.info(
                    "  [dim]#%d[/dim] → %s",
                    state.tool_call_count, display_name,
                )
                logger.debug("       input: %s", block.input)
                try:
                    result = registry.call(block.name, **block.input)
                    _update_state(state, block.name, result)
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    status_str = _result_summary(result)
                    logger.info(
                        "       [green]ok[/green] %s  [dim]%dms[/dim]",
                        status_str, elapsed_ms,
                    )
                    logger.debug("       full result: %s", result)
                    if isinstance(result, dict) and "error" in result:
                        count = (
                            phase_error_counts.get(state.current_phase, 0) + 1
                        )
                        phase_error_counts[state.current_phase] = count
                        logger.warning(
                            "  subagent error in %s (%d/%d): %s",
                            state.current_phase.name, count,
                            max_phase_errors, result["error"],
                        )
                        if count >= max_phase_errors:
                            logger.error(
                                "[red]Phase %s failed %d times — "
                                "aborting.[/red]",
                                state.current_phase.name, count,
                            )
                            state.current_phase = Phase.COMPLETE
                    else:
                        phase_error_counts.pop(state.current_phase, None)
                except Exception as exc:
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    logger.warning(
                        "       [red]err[/red] %s  [dim]%dms[/dim]",
                        exc, elapsed_ms,
                    )
                    logger.debug(
                        "       traceback:\n%s", traceback.format_exc()
                    )
                    result = {
                        "error": str(exc),
                        "traceback": traceback.format_exc(),
                    }
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": str(result),
                })

        messages.append({"role": "assistant", "content": response.content})
        if tool_results:
            messages.append({"role": "user", "content": tool_results})

        new_phase = _infer_next_phase(state)
        if new_phase != state.current_phase:
            logger.info(
                "[green]✓[/green] %s complete → entering %s",
                state.current_phase.name, new_phase.name,
            )
            summary = compress_phase(state, state.current_phase)
            messages.append({"role": "user", "content": summary})
            # On entering INFRA, remind the agent of already-provisioned
            # resources so it does not recreate them.
            if new_phase == Phase.INFRA and state.aws_resources:
                resource_lines = "\n".join(
                    f"  {k}: {json.dumps(v)}"
                    for k, v in state.aws_resources.items()
                )
                resource_note = (
                    "[ALREADY-PROVISIONED AWS RESOURCES — do not recreate]\n"
                    + resource_lines
                )
                messages.append(
                    {"role": "user", "content": resource_note}
                )
            state.current_phase = new_phase
            state.checkpoint(checkpoint_path)
            logger.info("  checkpoint saved: %s", checkpoint_path)

        if response.stop_reason == "end_turn" and not tool_results:
            state.current_phase = Phase.COMPLETE

    elapsed = time.monotonic() - build_start
    logger.info(
        "[bold green]BUILD COMPLETE[/bold green] — "
        "%d tool calls in %.1fs  cost=$%.4f",
        state.tool_call_count, elapsed, state.cost_tracking["total_usd"],
    )
    return state


def _result_summary(result: object) -> str:
    """Builds a short one-line summary of a tool result for logging.

    Args:
        result: The tool result, typically a dict.

    Returns:
        A compact summary string, empty if result is not a dict.
    """
    if not isinstance(result, dict):
        return ""
    if "error" in result:
        return f"error={result['error']!r}"
    keys = [k for k in result if k not in ("status",)]
    return " ".join(f"{k}={str(result[k])[:40]!r}" for k in keys[:3])


def _update_state(state: BuildState, tool_name: str, result: dict) -> None:
    """Folds a tool result into the build state.

    Populates manifests, deployment results, AWS resource records, test
    reports, and agent statuses depending on which tool produced the
    result. Unrecognized tools are ignored.

    Args:
        state: The build state to update in place.
        tool_name: The tool name in either registry or API form.
        result: The tool's result dict.
    """
    name = (
        registry._registry_name(tool_name)
        if "__" in tool_name
        else tool_name
    )
    if name == "subagent.run_planner" and "app_spec" in result:
        spec = result["app_spec"]
        state.app_spec = AppSpec(**spec) if isinstance(spec, dict) else spec
        if "cost_summary" in result:
            cs = result["cost_summary"]
            state.cost_summary = (
                CostSummary(**cs) if isinstance(cs, dict) else cs
            )
        state.set_agent_status("PlannerSubagent", AgentStatus.CODE_COMPLETED)
    elif name == "subagent.run_backend_builder" and "files_created" in result:
        state.backend_manifest = BackendManifest(**result)
        for f in result.get("files_created", []):
            state.register_file(f, "backend")
        state.set_agent_status(
            "BackendBuilderSubagent", AgentStatus.CODE_COMPLETED
        )
    elif name == "subagent.run_frontend_builder" and "files_created" in result:
        state.frontend_manifest = FrontendManifest(**result)
        for f in result.get("files_created", []):
            state.register_file(f, "frontend")
        state.set_agent_status(
            "FrontendBuilderSubagent", AgentStatus.CODE_COMPLETED
        )
    elif name == "aws.create_ecr_repo" and "repository_uri" in result:
        key = result["repository_uri"].split("/")[-1]  # The repo name.
        state.aws_resources[f"ecr:{key}"] = result
    elif name == "aws.create_eks_cluster" and "cluster_name" in result:
        state.aws_resources["eks:cluster"] = result
    elif name == "aws.get_eks_kubeconfig" and result.get("kubeconfig_updated"):
        state.aws_resources["eks:kubeconfig"] = result
    elif name == "aws.create_rds_instance" and "db_identifier" in result:
        state.aws_resources[f"rds:{result['db_identifier']}"] = result
    elif name == "aws.get_rds_endpoint" and "endpoint" in result:
        state.aws_resources.setdefault("rds:endpoint", {}).update(result)
    elif name == "aws.create_s3_bucket" and "bucket_name" in result:
        state.aws_resources[f"s3:{result['bucket_name']}"] = result
    elif name == "aws.create_cloudfront_dist" and "distribution_id" in result:
        state.aws_resources["cloudfront:dist"] = result
    elif name == "aws.create_iam_role" and "role_arn" in result:
        state.aws_resources[f"iam:{result['role_name']}"] = result
    elif name == "subagent.run_infra_provisioner" and "cluster_name" in result:
        state.deployment_result = DeploymentResult(**result)
        state.set_agent_status("InfraSubagent", AgentStatus.CODE_COMPLETED)
    elif name in ("test.run_integration_tests", "test.run_e2e_tests"):
        if state.test_report is None:
            state.test_report = TestReport(
                integration_passed=0, integration_failed=0,
                e2e_passed=0, e2e_failed=0, coverage_pct=0.0,
            )
        if name == "test.run_integration_tests":
            state.test_report.integration_passed = result.get("passed", 0)
            state.test_report.integration_failed = result.get("failed", 0)
            state.set_agent_status(
                "BackendBuilderSubagent", AgentStatus.TESTED
            )
        else:
            state.test_report.e2e_passed = 1 if result.get("passed") else 0
            state.set_agent_status(
                "FrontendBuilderSubagent", AgentStatus.TESTED
            )


def _infer_next_phase(state: BuildState) -> Phase:
    """Determines the next phase from the artifacts now present in state.

    Args:
        state: The current build state.

    Returns:
        The phase to transition to, or the current phase if no transition
        condition is met.
    """
    if (
        state.current_phase == Phase.PLANNING
        and state.app_spec
        and state.cost_summary
    ):
        return Phase.API_SPEC
    if state.current_phase == Phase.API_SPEC and state.api_spec_path:
        return Phase.BUILD
    if (
        state.current_phase == Phase.BUILD
        and state.backend_manifest
        and state.frontend_manifest
    ):
        return Phase.INFRA
    if state.current_phase == Phase.INFRA and state.deployment_result:
        return Phase.TEST
    if state.current_phase == Phase.TEST and state.test_report:
        return Phase.MONITORING
    return state.current_phase
