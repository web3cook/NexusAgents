from eval.harness import EvalCase, Check

EVAL_CASE = EvalCase(
    description="Build a SaaS app with login, alerting dashboard, and API key manager",
    checks=[
        Check.http_200("frontend_url"),
        Check.http_200("backend_url"),
        Check.auth_flow_works("backend_url"),
        Check.k8s_pods_healthy(),
        Check.telegram_alert_fires(inject_error=True),
        Check.cost_summary_present(),
        Check.tool_call_count_gte(20),
    ],
)
