from __future__ import annotations

from agent.core.state import BuildState, Phase


def compress_phase(state: BuildState, phase: Phase) -> str:
    """Builds a compact text summary of a completed phase.

    The summary is injected into the orchestrator context so later phases
    retain the salient results of earlier ones without their full history.

    Args:
        state: The current build state.
        phase: The phase to summarize.

    Returns:
        A one-line summary string, or a generic marker if the phase has no
        recorded results.
    """
    if phase == Phase.PLANNING and state.app_spec and state.cost_summary:
        spec = state.app_spec
        cost = state.cost_summary
        return (
            f"[PLANNING COMPLETE] Features: {spec.features}. "
            f"Models: {spec.db_models}. Routes: {spec.api_routes}. "
            f"Pages: {spec.pages}. "
            f"AWS: ${cost.aws_monthly_usd:.2f}/month. "
            f"LLM: ${cost.llm_cost_usd:.4f}. "
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
            f"Build: {m.static_build_cmd}. "
            f"Tests: {m.test_results.get('passed', 0)} passed."
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
            f"[TEST COMPLETE] Integration: {r.integration_passed} passed, "
            f"{r.integration_failed} failed. "
            f"E2E: {r.e2e_passed} passed. Coverage: {r.coverage_pct:.1f}%."
        )
    return f"[{phase.value} COMPLETE]"


def summarise_messages(messages: list[dict], keep_last: int = 8) -> list[dict]:
    """Keeps the last N messages, prepending a summary of dropped ones.

    The cut point is always aligned to a clean boundary: a user message
    whose content is plain text (not tool_result blocks). Cutting between
    an assistant tool_use and the user tool_result that answers it would
    cause a 400 from the Anthropic API.

    Args:
        messages: The full conversation history.
        keep_last: The minimum number of trailing messages to retain.

    Returns:
        A possibly shortened message list. The original list is returned
        unchanged when it cannot be safely truncated.
    """
    if len(messages) <= keep_last:
        return messages

    # Start at the naive cut point and walk forward until we land on a user
    # message with non-tool_result content (safe to start a conversation
    # from).
    cut = len(messages) - keep_last
    while cut < len(messages):
        msg = messages[cut]
        content = msg.get("content", "")
        is_plain_user = (
            msg["role"] == "user"
            and (
                isinstance(content, str)
                or (
                    isinstance(content, list)
                    and content
                    and content[0].get("type") != "tool_result"
                )
            )
        )
        if is_plain_user:
            break
        cut += 1

    if cut >= len(messages):
        # Every remaining message is a tool pair, so keep them all.
        return messages

    dropped = messages[:cut]
    if not dropped:
        return messages

    summary_text = (
        f"[CONTEXT SUMMARY: {len(dropped)} earlier messages omitted. "
        "Work continues from previous phases.]"
    )
    return [{"role": "user", "content": summary_text}] + messages[cut:]
