from __future__ import annotations
import logging
import re
import time
import uuid
from pathlib import Path
import anthropic
import agent.tools  # noqa: F401 — triggers all @registry.register decorators
from agent.tools.registry import registry
from agent.core.state import (
    BuildState, Phase, set_session_id,
    AppSpec, CostSummary, BackendManifest, FrontendManifest, DeploymentResult, TestReport,
)
from agent.core.context import compress_phase, summarise_messages

logger = logging.getLogger("nexus.orchestrator")
client = anthropic.Anthropic()

SYSTEM_PROMPT = """You are Nexus, an autonomous full-stack application builder and deployer.

Given a user's app description, you will:
1. Use subagent.run_planner to plan the build and show cost estimates
2. Use subagent.run_backend_builder to scaffold the FastAPI backend
3. Use subagent.run_frontend_builder to scaffold the React frontend
4. Build and push Docker images using docker.* tools
5. Use subagent.run_infra_provisioner to deploy to AWS EKS
6. Run tests using test.* tools
7. Use subagent.run_alerting to start persistent log monitoring

Work through phases in order: PLANNING → BACKEND → FRONTEND → INFRA → TEST → MONITORING.
After each phase, summarise what was accomplished before moving to the next.
The workspace directory is provided in the first message."""

PHASE_TOOLS = {
    Phase.PLANNING:   ["subagent", "plan"],
    Phase.BACKEND:    ["subagent", "code", "test"],
    Phase.FRONTEND:   ["subagent", "code", "test"],
    Phase.INFRA:      ["subagent", "aws", "k8s", "docker", "code"],
    Phase.TEST:       ["test"],
    Phase.MONITORING: ["subagent", "alert"],
}


def _latest_checkpoint(checkpoint_dir: Path) -> Path | None:
    """Return the most recently written checkpoint file, or None."""
    files = sorted(checkpoint_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def _phase_from_state(state: BuildState) -> Phase:
    """Return the phase the build is actually at, based on what manifests exist."""
    if state.deployment_result:
        return Phase.MONITORING if state.test_report else Phase.TEST
    if state.frontend_manifest:
        return Phase.INFRA
    if state.backend_manifest:
        return Phase.FRONTEND
    if state.app_spec and state.cost_summary:
        return Phase.BACKEND
    return Phase.PLANNING


def _audit_workspace(state: BuildState, workspace: str) -> tuple[BuildState, str]:
    """Scan workspace files to recover any manifests missing from the checkpoint.

    Returns the (possibly updated) state and a plain-text audit report to inject
    into the orchestrator's message history so it knows what's already built.
    """
    ws = Path(workspace)
    lines: list[str] = [f"[WORKSPACE AUDIT — session {state.session_id}]"]

    # ── Backend ──────────────────────────────────────────────────────────────
    if state.backend_manifest is None:
        backend_root = ws / "backend"
        py_files = sorted(backend_root.rglob("*.py")) if backend_root.exists() else []
        dockerfile = backend_root / "Dockerfile"
        if py_files and dockerfile.exists():
            # Extract route prefixes from APIRouter definitions in route files
            routes: list[str] = []
            routes_dir = backend_root / "app" / "routes"
            if routes_dir.exists():
                for route_file in routes_dir.glob("*.py"):
                    for m in re.finditer(r'prefix\s*=\s*["\']([^"\']+)["\']', route_file.read_text()):
                        routes.append(m.group(1))
            state.backend_manifest = BackendManifest(
                files_created=[str(f) for f in py_files] + [str(dockerfile)],
                api_routes=routes or ["/api"],
                env_vars_required=["DATABASE_URL", "JWT_SECRET", "AWS_REGION", "CLUSTER_NAME"],
                dockerfile_path=str(dockerfile),
                test_results={"passed": 0, "failed": 0},
            )
            lines.append(
                f"Backend: {len(py_files)} .py files found — backend_manifest populated"
                f" ({len(routes)} routes detected)"
            )
        elif backend_root.exists():
            lines.append(f"Backend: directory exists but incomplete ({len(py_files)} .py files, Dockerfile={'yes' if dockerfile.exists() else 'no'})")
        else:
            lines.append("Backend: not started")
    else:
        lines.append(f"Backend: already in checkpoint ({len(state.backend_manifest.files_created)} files)")

    # ── Frontend ─────────────────────────────────────────────────────────────
    if state.frontend_manifest is None:
        # Handle double-nesting (frontend/frontend/) created by wrong workspace path
        frontend_root = ws / "frontend"
        if (frontend_root / "frontend").exists():
            frontend_root = frontend_root / "frontend"
            lines.append("Frontend: detected double-nested path (frontend/frontend/) — using inner dir")

        tsx_files = sorted(frontend_root.rglob("*.tsx")) if frontend_root.exists() else []
        ts_files  = sorted(frontend_root.rglob("*.ts"))  if frontend_root.exists() else []
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
                f"Frontend: {len(tsx_files)} .tsx + {len(ts_files)} .ts files found"
                " — frontend_manifest populated"
            )
        elif frontend_root.exists():
            lines.append(f"Frontend: directory exists but incomplete ({len(tsx_files)} .tsx files, Dockerfile={'yes' if dockerfile.exists() else 'no'})")
        else:
            lines.append("Frontend: not started")
    else:
        lines.append(f"Frontend: already in checkpoint ({len(state.frontend_manifest.files_created)} files)")

    # ── Phase correction ─────────────────────────────────────────────────────
    correct_phase = _phase_from_state(state)
    if correct_phase != state.current_phase:
        lines.append(
            f"Phase corrected: {state.current_phase.name} → {correct_phase.name}"
            " (based on what was found on disk)"
        )
        state.current_phase = correct_phase
    else:
        lines.append(f"Phase confirmed: {state.current_phase.name}")

    lines.append("Continuing build from the phase above — do not redo completed work.")
    return state, "\n".join(lines)


def run(
    user_description: str,
    workspace: str,
    checkpoint_dir: Path | None = None,
    resume: bool = False,
    session_id: str | None = None,
) -> BuildState:
    checkpoint_dir = checkpoint_dir or Path("/tmp/nexus-checkpoints")
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    resumed = False
    if resume or session_id:
        if session_id:
            checkpoint_path = checkpoint_dir / f"{session_id}.json"
            if not checkpoint_path.exists():
                logger.error("No checkpoint found for session %s in %s", session_id, checkpoint_dir)
                raise FileNotFoundError(f"Checkpoint not found: {checkpoint_path}")
        else:
            checkpoint_path = _latest_checkpoint(checkpoint_dir)
            if not checkpoint_path:
                logger.warning("--resume requested but no checkpoint found in %s — starting fresh", checkpoint_dir)
                checkpoint_path = None

        if checkpoint_path:
            state = BuildState.from_checkpoint(checkpoint_path)
            set_session_id(state.session_id)
            resumed = True
            logger.info(
                "[yellow]Resuming session %s from phase %s[/yellow]  [dim](%s)[/dim]",
                state.session_id, state.current_phase.name, checkpoint_path.name,
            )

    if not resumed:
        session_id = str(uuid.uuid4())[:8]
        set_session_id(session_id)
        state = BuildState(session_id=session_id, user_description=user_description)
        checkpoint_path = checkpoint_dir / f"{session_id}.json"

    build_start = time.monotonic()
    logger.info("[bold]session %s[/bold] — %s", state.session_id, user_description[:100])
    logger.info("workspace: %s", workspace)

    messages: list[dict] = [
        {
            "role": "user",
            "content": [{"type": "text",
                          "text": f"Build this app: {user_description}\nWorkspace: {workspace}",
                          "cache_control": {"type": "ephemeral"}}],
        }
    ]

    if resumed:
        logger.info("[yellow]Auditing workspace before continuing...[/yellow]")
        state, audit_report = _audit_workspace(state, workspace)
        for line in audit_report.splitlines():
            logger.info("  %s", line)
        # Inject audit as a cached user message — it's stable context the model needs every turn
        messages.append({
            "role": "user",
            "content": [{"type": "text", "text": audit_report,
                          "cache_control": {"type": "ephemeral"}}],
        })
        # Save corrected state immediately
        state.checkpoint(checkpoint_path)
        logger.info("  checkpoint updated with audit results")

    current_phase_logged: Phase | None = None
    phase_error_counts: dict[Phase, int] = {}
    MAX_PHASE_ERRORS = 3  # bail out if the same phase keeps returning subagent errors

    while state.current_phase != Phase.COMPLETE:
        if state.current_phase != current_phase_logged:
            phases = list(Phase)
            total = len(phases) - 1  # exclude COMPLETE
            idx = phases.index(state.current_phase) + 1
            logger.info(
                "[cyan]──── phase %d/%d: %s ────[/cyan]",
                idx, total, state.current_phase.name,
            )
            current_phase_logged = state.current_phase

        namespaces = PHASE_TOOLS.get(state.current_phase)
        tools = registry.get_anthropic_tools(namespaces=namespaces) if namespaces else registry.get_anthropic_tools()
        # Cache the full tool list — it's static per phase and the largest repeated payload.
        if tools:
            tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}

        messages = summarise_messages(messages, keep_last=10)

        response = client.messages.create(
            model="claude-opus-4-8",
            max_tokens=8096,
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=tools,
            messages=messages,
        )

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                state.tool_call_count += 1
                display_name = registry._registry_name(block.name) if "__" in block.name else block.name
                t0 = time.monotonic()
                logger.info("  [dim]#%d[/dim] → %s", state.tool_call_count, display_name)
                logger.debug("       input: %s", block.input)
                try:
                    result = registry.call(block.name, **block.input)
                    _update_state(state, block.name, result)
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    status_str = _result_summary(result)
                    logger.info("       [green]ok[/green] %s  [dim]%dms[/dim]", status_str, elapsed_ms)
                    logger.debug("       full result: %s", result)
                    if isinstance(result, dict) and "error" in result:
                        count = phase_error_counts.get(state.current_phase, 0) + 1
                        phase_error_counts[state.current_phase] = count
                        logger.warning(
                            "  subagent returned error in phase %s (%d/%d): %s",
                            state.current_phase.name, count, MAX_PHASE_ERRORS, result["error"],
                        )
                        if count >= MAX_PHASE_ERRORS:
                            logger.error(
                                "[red]Phase %s failed %d times — aborting build.[/red] "
                                "Re-run with --resume after fixing the issue.",
                                state.current_phase.name, count,
                            )
                            state.current_phase = Phase.COMPLETE
                    else:
                        phase_error_counts.pop(state.current_phase, None)
                except Exception as exc:
                    elapsed_ms = int((time.monotonic() - t0) * 1000)
                    logger.warning("       [red]err[/red] %s  [dim]%dms[/dim]", exc, elapsed_ms)
                    result = {"error": str(exc)}
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
            state.current_phase = new_phase
            state.checkpoint(checkpoint_path)
            logger.info("  checkpoint saved: %s", checkpoint_path)

        if response.stop_reason == "end_turn" and not tool_results:
            state.current_phase = Phase.COMPLETE

    elapsed = time.monotonic() - build_start
    logger.info(
        "[bold green]BUILD COMPLETE[/bold green] — %d tool calls in %.1fs",
        state.tool_call_count, elapsed,
    )
    return state


def _result_summary(result: object) -> str:
    """One-line summary of a tool result for logging."""
    if not isinstance(result, dict):
        return ""
    if "error" in result:
        return f"error={result['error']!r}"
    keys = [k for k in result if k not in ("status",)]
    return " ".join(f"{k}={str(result[k])[:40]!r}" for k in keys[:3])


def _update_state(state: BuildState, tool_name: str, result: dict) -> None:
    # Normalize API form ('namespace__tool') to registry form ('namespace.tool')
    name = registry._registry_name(tool_name) if "__" in tool_name else tool_name
    if name == "subagent.run_planner" and "app_spec" in result:
        spec = result["app_spec"]
        state.app_spec = AppSpec(**spec) if isinstance(spec, dict) else spec
        if "cost_summary" in result:
            cs = result["cost_summary"]
            state.cost_summary = CostSummary(**cs) if isinstance(cs, dict) else cs
    elif name == "subagent.run_backend_builder" and "files_created" in result:
        state.backend_manifest = BackendManifest(**result)
    elif name == "subagent.run_frontend_builder" and "files_created" in result:
        state.frontend_manifest = FrontendManifest(**result)
    elif name == "subagent.run_infra_provisioner" and "cluster_name" in result:
        state.deployment_result = DeploymentResult(**result)
    elif name in ("test.run_integration_tests", "test.run_e2e_tests"):
        if state.test_report is None:
            state.test_report = TestReport(
                integration_passed=0, integration_failed=0,
                e2e_passed=0, e2e_failed=0, coverage_pct=0.0,
            )
        if name == "test.run_integration_tests":
            state.test_report.integration_passed = result.get("passed", 0)
            state.test_report.integration_failed = result.get("failed", 0)
        else:
            state.test_report.e2e_passed = 1 if result.get("passed") else 0


def _infer_next_phase(state: BuildState) -> Phase:
    if state.current_phase == Phase.PLANNING and state.app_spec and state.cost_summary:
        return Phase.BACKEND
    if state.current_phase == Phase.BACKEND and state.backend_manifest:
        return Phase.FRONTEND
    if state.current_phase == Phase.FRONTEND and state.frontend_manifest:
        return Phase.INFRA
    if state.current_phase == Phase.INFRA and state.deployment_result:
        return Phase.TEST
    if state.current_phase == Phase.TEST and state.test_report:
        return Phase.MONITORING
    return state.current_phase
