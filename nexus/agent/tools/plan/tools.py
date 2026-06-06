"""Planning tools that estimate cost and generate the API spec."""

from __future__ import annotations

import os

from agent.core.observability import instrument
from agent.core.retry import rate_limit
from agent.tools.registry import registry

# USD per token (not per million).
_PRICING = {
    "claude-opus-4-8": {"input": 15 / 1_000_000, "output": 75 / 1_000_000},
    "claude-sonnet-4-6": {"input": 3 / 1_000_000, "output": 15 / 1_000_000},
}

AWS_BASE_COSTS = {
    "eks_monthly_usd": 73.0,  # EKS control plane.
    "ecr_monthly_usd": 2.0,
    "cloudfront_monthly_usd": 5.0,
    "s3_monthly_usd": 1.0,
    "rds_monthly_usd": 25.0,  # db.t3.micro.
    "data_transfer_usd": 5.0,
}

STEP_SEQUENCE = [
    "plan.analyze_spec", "plan.estimate_steps", "plan.estimate_tokens",
    "plan.estimate_aws_cost", "plan.render_summary",
    "code.scaffold_fastapi_project", "code.scaffold_db_model",
    "code.scaffold_migration", "code.scaffold_api_route",
    "code.run_linter", "test.run_unit_tests",
    "code.scaffold_react_project", "code.scaffold_react_page",
    "code.run_linter", "test.run_unit_tests",
    "docker.build_image", "docker.push_to_ecr",
    "docker.build_image", "docker.push_to_ecr",
    "aws.create_eks_cluster", "aws.get_eks_kubeconfig",
    "k8s.create_namespace", "k8s.create_secret",
    "k8s.apply_manifest", "k8s.apply_manifest",
    "k8s.run_migration_job", "k8s.wait_for_rollout",
    "k8s.get_ingress_address",
    "test.run_integration_tests", "test.run_e2e_tests",
]


@registry.register(
    name="plan.analyze_spec",
    description=(
        "Parse user description and extract app features, data models, "
        "API routes, and pages"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "user_description": {"type": "string"}
        },
        "required": ["user_description"],
    },
)
@instrument(namespace="plan", tool="analyze_spec")
def analyze_spec(user_description: str) -> dict:
    """Extracts features, models, routes, and pages from a description.

    Uses simple keyword matching and falls back to a generic CRUD spec
    when no known keyword is present.

    Args:
        user_description: The natural-language app description.

    Returns:
        A dict with features, db_models, api_routes, pages, auth_required,
        and admin_dashboard.
    """
    rate_limit("plan")
    desc = user_description.lower()
    features, db_models, api_routes, pages = [], [], [], []

    if any(w in desc for w in ["login", "auth", "sign in", "register"]):
        features.append("auth")
        db_models.append("User")
        api_routes.extend(["/auth/login", "/auth/register"])
        pages.extend(["Login", "Register"])

    if "dashboard" in desc:
        features.append("dashboard")
        pages.append("Dashboard")

    if "alert" in desc:
        features.append("alerting")
        db_models.append("Alert")
        api_routes.append("/alerts")
        pages.append("Alerts")

    if "api key" in desc:
        features.append("api_keys")
        db_models.append("ApiKey")
        api_routes.append("/keys")
        pages.append("ApiKeys")

    if not features:
        features = ["custom"]
        db_models = ["Item"]
        api_routes = ["/items"]
        pages = ["Items"]

    return {
        "features": features,
        "db_models": db_models,
        "api_routes": api_routes,
        "pages": pages,
        "auth_required": "auth" in features,
        "admin_dashboard": True,
    }


@registry.register(
    name="plan.estimate_steps",
    description=(
        "Estimate total agent steps for the build based on feature and "
        "model count"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "feature_count": {"type": "integer"},
            "model_count": {"type": "integer"},
        },
        "required": ["feature_count", "model_count"],
    },
)
@instrument(namespace="plan", tool="estimate_steps")
def estimate_steps(feature_count: int, model_count: int) -> dict:
    """Estimates total build steps from feature and model counts.

    Args:
        feature_count: Number of features in the spec.
        model_count: Number of data models in the spec.

    Returns:
        A dict with the total step count and the base step breakdown.
    """
    rate_limit("plan")
    base = len(STEP_SEQUENCE)
    extra = max(0, (feature_count - 1) * 2 + (model_count - 1) * 2)
    total = base + extra
    return {"steps": total, "breakdown": STEP_SEQUENCE}


@registry.register(
    name="plan.estimate_tokens",
    description="Estimate total LLM token usage and cost in USD",
    input_schema={
        "type": "object",
        "properties": {
            "steps": {"type": "integer"},
            "avg_tokens_per_step": {"type": "integer"},
        },
        "required": ["steps"],
    },
)
@instrument(namespace="plan", tool="estimate_tokens")
def estimate_tokens(steps: int, avg_tokens_per_step: int = 6000) -> dict:
    """Estimates total LLM token usage and cost in USD.

    Args:
        steps: Estimated number of build steps.
        avg_tokens_per_step: Average tokens per step; currently unused but
            kept for signature stability.

    Returns:
        A dict with total_tokens, cost_usd, and a per-component breakdown.
    """
    rate_limit("plan")
    # Orchestrator (claude-opus-4-8): roughly 12 turns, each carrying the
    # system prompt, tools, and history at about 40K input and 3K output.
    orch_turns = max(12, steps // 3)
    orch_in = orch_turns * 40_000
    orch_out = orch_turns * 3_000
    opus = _PRICING["claude-opus-4-8"]
    orch_cost = orch_in * opus["input"] + orch_out * opus["output"]

    # Subagents (claude-sonnet-4-6): the builder agents run about 45
    # iterations each at roughly 20K input and 4K output.
    subagent_iterations = 5 * 45
    sub_in = subagent_iterations * 20_000
    sub_out = subagent_iterations * 4_000
    sonnet = _PRICING["claude-sonnet-4-6"]
    sub_cost = sub_in * sonnet["input"] + sub_out * sonnet["output"]

    # Prompt caching cuts repeated token costs by roughly 60%, plus a 1.5x
    # safety multiplier.
    cache_factor = 0.40  # 60% savings.
    safety_factor = 1.5
    total_cost = (orch_cost + sub_cost) * cache_factor * safety_factor
    total_tokens = orch_in + orch_out + sub_in + sub_out

    return {
        "total_tokens": total_tokens,
        "cost_usd": round(total_cost, 2),
        "breakdown": {
            "orchestrator_usd": round(
                orch_cost * cache_factor * safety_factor, 2
            ),
            "subagents_usd": round(
                sub_cost * cache_factor * safety_factor, 2
            ),
            "note": (
                "estimate assumes 60% cache hit rate; actual cost varies"
            ),
        },
    }


@registry.register(
    name="plan.estimate_aws_cost",
    description="Estimate monthly AWS infrastructure cost",
    input_schema={
        "type": "object",
        "properties": {
            "region": {"type": "string"},
            "include_rds": {"type": "boolean"},
        },
        "required": ["region"],
    },
)
@instrument(namespace="plan", tool="estimate_aws_cost")
def estimate_aws_cost(region: str, include_rds: bool = True) -> dict:
    """Estimates monthly AWS infrastructure cost.

    Args:
        region: The target AWS region (informational only).
        include_rds: Whether to include the RDS line item.

    Returns:
        The base cost dict plus a total_monthly_usd entry.
    """
    rate_limit("plan")
    costs = dict(AWS_BASE_COSTS)
    if not include_rds:
        costs["rds_monthly_usd"] = 0.0
    costs["total_monthly_usd"] = round(sum(costs.values()), 2)
    return costs


@registry.register(
    name="plan.render_summary",
    description=(
        "Render the cost summary card shown to the user before build starts"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "aws_monthly_usd": {"type": "number"},
            "llm_cost_usd": {"type": "number"},
            "steps_estimated": {"type": "integer"},
            "llm_tokens_estimated": {"type": "integer"},
        },
        "required": [
            "aws_monthly_usd",
            "llm_cost_usd",
            "steps_estimated",
            "llm_tokens_estimated",
        ],
    },
)
@instrument(namespace="plan", tool="render_summary")
def render_summary(
    aws_monthly_usd: float,
    llm_cost_usd: float,
    steps_estimated: int,
    llm_tokens_estimated: int,
) -> dict:
    """Renders the cost-estimate card shown before the build starts.

    Args:
        aws_monthly_usd: Estimated monthly AWS cost.
        llm_cost_usd: Estimated LLM cost.
        steps_estimated: Estimated number of build steps.
        llm_tokens_estimated: Estimated total LLM token usage.

    Returns:
        A dict with the rendered "summary" string.
    """
    rate_limit("plan")
    width = 38  # Inner content width between the ║ borders.

    def row(label: str, value: str) -> str:
        """Formats one bordered row of the summary card."""
        content = f"  {label}{value}"
        return f"║{content:<{width}}║"

    summary = "\n".join([
        "╔══════════════════════════════════════╗",
        f"║{'NEXUS BUILD ESTIMATE':^{width}}║",
        "╠══════════════════════════════════════╣",
        row("AWS cost:    $", f"{aws_monthly_usd:.2f}/month"),
        row("LLM cost:    ~$", f"{llm_cost_usd:.2f} (estimate)"),
        row("Steps:        ", str(steps_estimated)),
        row("Tokens:       ", f"{llm_tokens_estimated:,}"),
        "╚══════════════════════════════════════╝",
    ])
    return {"summary": summary}


@registry.register(
    name="plan.render_full_plan",
    description=(
        "Render the detailed step-by-step build plan (shown only if user "
        "requests it)"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "steps": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["steps"],
    },
)
@instrument(namespace="plan", tool="render_full_plan")
def render_full_plan(steps: list[str]) -> dict:
    """Returns the full build-plan step list with a total count.

    Args:
        steps: The ordered list of step names.

    Returns:
        A dict with the plan and its total length.
    """
    rate_limit("plan")
    return {"plan": steps, "total": len(steps)}


@registry.register(
    name="plan.generate_api_spec",
    description=(
        "Generate an OpenAPI 3.0 YAML spec from AppSpec — deterministic, "
        "no LLM call"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "app_name": {"type": "string"},
            "api_routes": {"type": "array", "items": {"type": "string"}},
            "db_models": {"type": "array", "items": {"type": "string"}},
            "features": {"type": "array", "items": {"type": "string"}},
            "output_dir": {"type": "string"},
        },
        "required": ["app_name", "api_routes", "db_models", "features"],
    },
)
@instrument(namespace="plan", tool="generate_api_spec")
def generate_api_spec(
    app_name: str,
    api_routes: list[str],
    db_models: list[str],
    features: list[str],
    output_dir: str = "/tmp",
) -> dict:
    """Generates an OpenAPI 3.0 YAML spec from an AppSpec.

    Infers HTTP methods per route, builds request and response schemas for
    each model, adds an auth login path when "auth" is a feature, and
    writes the spec to openapi.yaml under output_dir.

    Args:
        app_name: The application title for the spec.
        api_routes: The route paths to document.
        db_models: The model names used to build schemas.
        features: The feature list; "auth" adds a login endpoint.
        output_dir: Directory the openapi.yaml is written to.

    Returns:
        A dict with the rendered YAML, output path, and route count.
    """
    import yaml as _yaml

    paths: dict = {}
    for route in api_routes:
        methods: list[str]
        if any(k in route for k in ["/login", "/register", "/token"]):
            methods = ["post"]
        elif route.count("/") == 1:
            methods = ["get", "post"]
        else:
            methods = ["get", "put", "delete"]

        path_item: dict = {}
        for method in methods:
            tag = route.strip("/").split("/")[0] or "default"
            operation_id = (
                f"{method}_{route.strip('/').replace('/', '_')}"
            )
            path_item[method] = {
                "tags": [tag],
                "summary": f"{method.upper()} {route}",
                "operationId": operation_id,
                "responses": {
                    "200": {
                        "description": "Successful response",
                        "content": {
                            "application/json": {
                                "schema": {"type": "object"}
                            }
                        },
                    },
                    "400": {"description": "Bad request"},
                    "401": {"description": "Unauthorized"},
                },
            }
            if method in ("post", "put"):
                model_name = next(
                    (m for m in db_models if m.lower() in route.lower()),
                    db_models[0] if db_models else "Item",
                )
                schema_ref = (
                    f"#/components/schemas/{model_name}Request"
                )
                path_item[method]["requestBody"] = {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": schema_ref},
                        }
                    },
                }
        paths[route] = path_item

    schemas: dict = {}
    for model in db_models:
        schemas[model] = {
            "type": "object",
            "properties": {
                "id": {"type": "integer", "example": 1},
                "created_at": {"type": "string", "format": "date-time"},
            },
            "required": ["id"],
        }
        schemas[f"{model}Request"] = {
            "type": "object",
            "properties": {},
            "required": [],
        }

    if "auth" in features:
        _auth_response_schema = {
            "type": "object",
            "properties": {
                "access_token": {"type": "string"},
                "token_type": {"type": "string"},
            },
        }
        paths.setdefault("/auth/login", {})["post"] = {
            "tags": ["auth"],
            "summary": "POST /auth/login",
            "operationId": "post_auth_login",
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "email": {
                                    "type": "string",
                                    "example": "user@example.com",
                                },
                                "password": {
                                    "type": "string",
                                    "example": "secret",
                                },
                            },
                            "required": ["email", "password"],
                        }
                    }
                },
            },
            "responses": {
                "200": {
                    "description": "JWT token",
                    "content": {
                        "application/json": {"schema": _auth_response_schema}
                    },
                },
                "401": {"description": "Invalid credentials"},
            },
        }
        paths.setdefault("/auth/register", {})["post"] = {
            "tags": ["auth"],
            "summary": "POST /auth/register",
            "operationId": "post_auth_register",
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "email": {
                                    "type": "string",
                                    "example": "user@example.com",
                                },
                                "password": {
                                    "type": "string",
                                    "example": "secret",
                                },
                                "name": {
                                    "type": "string",
                                    "example": "Jane Doe",
                                },
                            },
                            "required": ["email", "password", "name"],
                        }
                    }
                },
            },
            "responses": {
                "201": {
                    "description": "Account created — returns JWT token",
                    "content": {
                        "application/json": {"schema": _auth_response_schema}
                    },
                },
                "400": {"description": "Email already registered"},
            },
        }

    spec = {
        "openapi": "3.0.0",
        "info": {"title": app_name, "version": "1.0.0"},
        "paths": paths,
        "components": {"schemas": schemas},
    }

    yaml_text = _yaml.dump(
        spec, default_flow_style=False, allow_unicode=True, sort_keys=True
    )
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, "openapi.yaml")
    with open(out_path, "w") as fh:
        fh.write(yaml_text)

    return {
        "openapi_yaml": yaml_text,
        "output_path": out_path,
        "route_count": len(paths),
    }
