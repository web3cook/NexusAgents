import yaml
from agent.tools.plan.tools import analyze_spec, estimate_steps, estimate_tokens, estimate_aws_cost, render_summary, render_full_plan, generate_api_spec

def test_analyze_spec_extracts_features():
    result = analyze_spec(user_description="Build a SaaS app with user login, an alerting dashboard, and an API key manager")
    assert "auth" in result["features"] or "login" in result["features"]
    assert len(result["db_models"]) >= 1
    assert len(result["api_routes"]) >= 1
    assert len(result["pages"]) >= 1

def test_estimate_steps_returns_int():
    result = estimate_steps(feature_count=3, model_count=2)
    assert isinstance(result["steps"], int)
    assert result["steps"] >= 20

def test_estimate_tokens_returns_cost():
    result = estimate_tokens(steps=28)
    assert result["total_tokens"] > 1_000_000   # realistic multi-model context
    assert result["cost_usd"] > 1.0             # at least $1 for any real build
    assert "breakdown" in result
    assert result["breakdown"]["orchestrator_usd"] > 0
    assert result["breakdown"]["subagents_usd"] > 0

def test_estimate_aws_cost_returns_breakdown():
    result = estimate_aws_cost(region="us-east-1", include_rds=True)
    assert "eks_monthly_usd" in result
    assert "rds_monthly_usd" in result
    assert "total_monthly_usd" in result

def test_render_summary_returns_string():
    result = render_summary(
        aws_monthly_usd=47.20,
        llm_cost_usd=2.16,
        steps_estimated=28,
        llm_tokens_estimated=180000,
    )
    assert "47.20" in result["summary"]
    assert "2.16" in result["summary"]

def test_render_full_plan_lists_steps():
    result = render_full_plan(steps=["plan.analyze_spec", "code.scaffold_fastapi_project"])
    assert len(result["plan"]) == 2


def test_generate_api_spec_returns_valid_openapi():
    result = generate_api_spec(
        app_name="TestApp",
        api_routes=["/auth/login", "/users", "/posts"],
        db_models=["User", "Post"],
        features=["auth"],
    )
    assert "openapi_yaml" in result
    assert "output_path" in result
    spec = yaml.safe_load(result["openapi_yaml"])
    assert spec["openapi"] == "3.0.0"
    assert "/auth/login" in spec["paths"]
    assert "components" in spec
    assert "schemas" in spec["components"]
    assert "User" in spec["components"]["schemas"]


def test_generate_api_spec_writes_file():
    import tempfile
    import os
    with tempfile.TemporaryDirectory() as d:
        result = generate_api_spec(
            app_name="TestApp",
            api_routes=["/health"],
            db_models=["Item"],
            features=[],
            output_dir=d,
        )
        assert os.path.exists(result["output_path"])
        assert result["route_count"] >= 1
