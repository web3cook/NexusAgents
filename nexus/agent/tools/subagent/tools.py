from __future__ import annotations
from typing import Callable
from agent.tools.registry import registry
from agent.core.observability import instrument
from agent.core.retry import rate_limit

_status_callback: Callable[[str, str], None] | None = None


def set_status_callback(cb: Callable[[str, str], None]) -> None:
    global _status_callback
    _status_callback = cb


def _notify(agent_name: str, status: str) -> None:
    if _status_callback:
        _status_callback(agent_name, status)


@registry.register(
    name="subagent.run_planner",
    description="Spawn the PlannerSubagent to parse the user description and produce AppSpec + CostSummary",
    input_schema={
        "type": "object",
        "properties": {"user_description": {"type": "string"}},
        "required": ["user_description"],
    },
)
@instrument(namespace="subagent", tool="run_planner")
def run_planner(user_description: str) -> dict:
    rate_limit("subagent")
    from agent.subagents.planner import PlannerSubagent
    _notify("PlannerSubagent", "Ongoing")
    result = PlannerSubagent().run({"user_description": user_description})
    _notify("PlannerSubagent", "Code Completed" if "error" not in result else "Ongoing")
    return result


@registry.register(
    name="subagent.run_backend_builder",
    description="Spawn the BackendBuilderSubagent to scaffold the FastAPI backend from AppSpec",
    input_schema={
        "type": "object",
        "properties": {
            "app_spec": {"type": "object"},
            "workspace": {"type": "string"},
        },
        "required": ["app_spec", "workspace"],
    },
)
@instrument(namespace="subagent", tool="run_backend_builder")
def run_backend_builder(app_spec: dict, workspace: str) -> dict:
    rate_limit("subagent")
    from agent.subagents.backend_builder import BackendBuilderSubagent
    _notify("BackendBuilderSubagent", "Ongoing")
    result = BackendBuilderSubagent().run({"app_spec": app_spec, "workspace": workspace})
    _notify("BackendBuilderSubagent", "Code Completed" if "error" not in result else "Ongoing")
    return result


@registry.register(
    name="subagent.run_frontend_builder",
    description="Spawn the FrontendBuilderSubagent to scaffold the React frontend from AppSpec + API routes",
    input_schema={
        "type": "object",
        "properties": {
            "app_spec": {"type": "object"},
            "api_routes": {"type": "array", "items": {"type": "string"}},
            "workspace": {"type": "string"},
        },
        "required": ["app_spec", "api_routes", "workspace"],
    },
)
@instrument(namespace="subagent", tool="run_frontend_builder")
def run_frontend_builder(app_spec: dict, api_routes: list[str], workspace: str) -> dict:
    rate_limit("subagent")
    from agent.subagents.frontend_builder import FrontendBuilderSubagent
    _notify("FrontendBuilderSubagent", "Ongoing")
    result = FrontendBuilderSubagent().run({"app_spec": app_spec, "api_routes": api_routes, "workspace": workspace})
    _notify("FrontendBuilderSubagent", "Code Completed" if "error" not in result else "Ongoing")
    return result


@registry.register(
    name="subagent.run_infra_provisioner",
    description="Spawn the InfraSubagent to provision EKS, RDS, ECR, and deploy all K8s manifests",
    input_schema={
        "type": "object",
        "properties": {
            "app_spec": {"type": "object"},
            "backend_ecr_uri": {"type": "string"},
            "frontend_ecr_uri": {"type": "string"},
            "env_vars_required": {"type": "array", "items": {"type": "string"}},
            "workspace": {"type": "string"},
            "region": {"type": "string"},
        },
        "required": ["app_spec", "backend_ecr_uri", "frontend_ecr_uri", "env_vars_required", "workspace", "region"],
    },
)
@instrument(namespace="subagent", tool="run_infra_provisioner")
def run_infra_provisioner(app_spec: dict, backend_ecr_uri: str, frontend_ecr_uri: str,
                           env_vars_required: list[str], workspace: str, region: str) -> dict:
    rate_limit("subagent")
    from agent.subagents.infra import InfraSubagent
    _notify("InfraSubagent", "Ongoing")
    result = InfraSubagent().run({
        "app_spec": app_spec, "backend_ecr_uri": backend_ecr_uri,
        "frontend_ecr_uri": frontend_ecr_uri, "env_vars_required": env_vars_required,
        "workspace": workspace, "region": region,
    })
    _notify("InfraSubagent", "Code Completed" if "error" not in result else "Ongoing")
    return result


@registry.register(
    name="subagent.run_alerting",
    description="Spawn the persistent AlertingSubagent to monitor logs and send Telegram alerts",
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "namespace": {"type": "string"},
            "telegram_bot_token": {"type": "string"},
            "telegram_chat_id": {"type": "string"},
        },
        "required": ["cluster_name", "namespace", "telegram_bot_token", "telegram_chat_id"],
    },
)
@instrument(namespace="subagent", tool="run_alerting")
def run_alerting(cluster_name: str, namespace: str, telegram_bot_token: str, telegram_chat_id: str) -> dict:
    rate_limit("subagent")
    import threading
    from agent.subagents.alerting import AlertingSubagent
    _notify("AlertingSubagent", "Ongoing")
    agent = AlertingSubagent()
    thread = threading.Thread(
        target=agent.run,
        args=({"cluster_name": cluster_name, "namespace": namespace,
               "telegram_bot_token": telegram_bot_token, "telegram_chat_id": telegram_chat_id},),
        daemon=True,
    )
    thread.start()
    return {"started": True, "cluster_name": cluster_name, "namespace": namespace}
