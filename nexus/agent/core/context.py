from __future__ import annotations
from agent.core.state import BuildState, Phase


def compress_phase(state: BuildState, phase: Phase) -> str:
    """Return a compact text summary of a completed phase for injection into context."""
    if phase == Phase.PLANNING and state.app_spec and state.cost_summary:
        spec = state.app_spec
        cost = state.cost_summary
        return (
            f"[PLANNING COMPLETE] Features: {spec.features}. "
            f"Models: {spec.db_models}. Routes: {spec.api_routes}. Pages: {spec.pages}. "
            f"AWS: ${cost.aws_monthly_usd:.2f}/month. LLM: ${cost.llm_cost_usd:.4f}. "
            f"Steps: {cost.steps_estimated}."
        )
    if phase == Phase.BACKEND and state.backend_manifest:
        m = state.backend_manifest
        return (
            f"[BACKEND COMPLETE] {len(m.files_created)} files. "
            f"Routes: {m.api_routes}. Env: {m.env_vars_required}. "
            f"Tests: {m.test_results.get('passed', 0)} passed."
        )
    if phase == Phase.FRONTEND and state.frontend_manifest:
        m = state.frontend_manifest
        return (
            f"[FRONTEND COMPLETE] {len(m.files_created)} files. "
            f"Build: {m.static_build_cmd}. Tests: {m.test_results.get('passed', 0)} passed."
        )
    if phase == Phase.INFRA and state.deployment_result:
        d = state.deployment_result
        return (
            f"[INFRA COMPLETE] Cluster: {d.cluster_name}. "
            f"Frontend: {d.frontend_url}. Backend: {d.backend_url}."
        )
    if phase == Phase.TEST and state.test_report:
        r = state.test_report
        return (
            f"[TEST COMPLETE] Integration: {r.integration_passed} passed, {r.integration_failed} failed. "
            f"E2E: {r.e2e_passed} passed. Coverage: {r.coverage_pct:.1f}%."
        )
    return f"[{phase.value} COMPLETE]"


def summarise_messages(messages: list[dict], keep_last: int = 8) -> list[dict]:
    """Keep the last N messages, prepend a summary of dropped messages."""
    if len(messages) <= keep_last:
        return messages
    dropped = messages[:-keep_last]
    summary_text = f"[CONTEXT SUMMARY: {len(dropped)} earlier messages omitted. Work continues from previous phases.]"
    return [{"role": "user", "content": summary_text}] + messages[-keep_last:]
