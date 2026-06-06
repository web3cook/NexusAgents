"""Integration test: planning phase runs all plan.* tools end-to-end."""
import pytest
from agent.tools.plan.tools import (
    analyze_spec, estimate_steps, estimate_tokens, estimate_aws_cost, render_summary
)


def test_full_planning_pipeline():
    desc = "Build a SaaS app with user login, alerting dashboard, and API key manager"

    spec = analyze_spec(user_description=desc)
    assert "auth" in spec["features"]
    assert spec["admin_dashboard"] is True

    steps = estimate_steps(feature_count=len(spec["features"]), model_count=len(spec["db_models"]))
    assert steps["steps"] >= 20

    tokens = estimate_tokens(steps=steps["steps"], avg_tokens_per_step=6000)
    assert tokens["total_tokens"] > 0
    assert tokens["cost_usd"] > 0

    aws = estimate_aws_cost(region="us-east-1", include_rds=True)
    assert aws["total_monthly_usd"] > 0

    summary = render_summary(
        aws_monthly_usd=aws["total_monthly_usd"],
        llm_cost_usd=tokens["cost_usd"],
        steps_estimated=steps["steps"],
        llm_tokens_estimated=tokens["total_tokens"],
    )
    assert "NEXUS BUILD ESTIMATE" in summary["summary"]
