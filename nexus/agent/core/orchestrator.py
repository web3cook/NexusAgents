from __future__ import annotations
import logging
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


def run(user_description: str, workspace: str, checkpoint_dir: Path | None = None) -> BuildState:
    session_id = str(uuid.uuid4())[:8]
    set_session_id(session_id)
    checkpoint_dir = checkpoint_dir or Path("/tmp/nexus-checkpoints")
    checkpoint_dir.mkdir(parents=True, exist_ok=True)

    state = BuildState(session_id=session_id, user_description=user_description)
    checkpoint_path = checkpoint_dir / f"{session_id}.json"

    resumed = checkpoint_path.exists()
    if resumed:
        state = BuildState.from_checkpoint(checkpoint_path)

    build_start = time.monotonic()
    logger.info(
        "[bold]session %s[/bold] — %s%s",
        session_id,
        user_description[:100],
        "  [yellow](resuming)[/yellow]" if resumed else "",
    )
    logger.info("workspace: %s", workspace)

    messages: list[dict] = [
        {"role": "user", "content": f"Build this app: {user_description}\nWorkspace: {workspace}"}
    ]

    current_phase_logged: Phase | None = None

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
