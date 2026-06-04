from __future__ import annotations
from agent.tools.registry import registry
from agent.core.observability import instrument
from agent.core.retry import rate_limit

TOKENS_PER_DOLLAR = {
    "claude-opus-4-8":    {"input": 1_000_000 / 15, "output": 1_000_000 / 75},
    "claude-sonnet-4-6":  {"input": 1_000_000 / 3,  "output": 1_000_000 / 15},
}

AWS_BASE_COSTS = {
    "eks_monthly_usd":        73.0,   # EKS control plane
    "ecr_monthly_usd":        2.0,
    "cloudfront_monthly_usd": 5.0,
    "s3_monthly_usd":         1.0,
    "rds_monthly_usd":        25.0,   # db.t3.micro
    "data_transfer_usd":      5.0,
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
    description="Parse user description and extract app features, data models, API routes, and pages",
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
    description="Estimate total agent steps for the build based on feature and model count",
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
    rate_limit("plan")
    base = len(STEP_SEQUENCE)
    extra = (feature_count - 1) * 2 + (model_count - 1) * 2
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
        "required": ["steps", "avg_tokens_per_step"],
    },
)
@instrument(namespace="plan", tool="estimate_tokens")
def estimate_tokens(steps: int, avg_tokens_per_step: int = 6000) -> dict:
    rate_limit("plan")
    total = steps * avg_tokens_per_step
    rate = TOKENS_PER_DOLLAR["claude-sonnet-4-6"]["input"]
    cost = total / rate
    return {"total_tokens": total, "cost_usd": round(cost, 4)}


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
    rate_limit("plan")
    costs = dict(AWS_BASE_COSTS)
    if not include_rds:
        costs["rds_monthly_usd"] = 0.0
    costs["total_monthly_usd"] = round(sum(costs.values()), 2)
    return costs


@registry.register(
    name="plan.render_summary",
    description="Render the cost summary card shown to the user before build starts",
    input_schema={
        "type": "object",
        "properties": {
            "aws_monthly_usd": {"type": "number"},
            "llm_cost_usd": {"type": "number"},
            "steps_estimated": {"type": "integer"},
            "llm_tokens_estimated": {"type": "integer"},
        },
        "required": ["aws_monthly_usd", "llm_cost_usd", "steps_estimated", "llm_tokens_estimated"],
    },
)
@instrument(namespace="plan", tool="render_summary")
def render_summary(aws_monthly_usd: float, llm_cost_usd: float, steps_estimated: int, llm_tokens_estimated: int) -> dict:
    rate_limit("plan")
    summary = (
        f"╔══════════════════════════════════════╗\n"
        f"║         NEXUS BUILD ESTIMATE         ║\n"
        f"╠══════════════════════════════════════╣\n"
        f"║  AWS cost:    ${aws_monthly_usd:.2f}/month          ║\n"
        f"║  LLM cost:    ${llm_cost_usd:.4f} (this run)    ║\n"
        f"║  Steps:       {steps_estimated}                       ║\n"
        f"║  Tokens:      {llm_tokens_estimated:,}               ║\n"
        f"╚══════════════════════════════════════╝"
    )
    return {"summary": summary}


@registry.register(
    name="plan.render_full_plan",
    description="Render the detailed step-by-step build plan (shown only if user requests it)",
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
    rate_limit("plan")
    return {"plan": steps, "total": len(steps)}
