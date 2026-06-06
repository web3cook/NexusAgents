from agent.subagents.base import BaseSubagent


class PlannerSubagent(BaseSubagent):
    def __init__(self):
        super().__init__(
            name="PlannerSubagent",
            system_prompt="""You are the Nexus Planner. Your job is to analyse a user's app description and produce a complete build plan.

Use tools in this order:
1. plan.analyze_spec — extract features, models, routes, pages
2. plan.estimate_steps — count build steps
3. plan.estimate_tokens — calculate LLM cost
4. plan.estimate_aws_cost — calculate AWS monthly cost
5. plan.render_summary — produce the cost card

Then output a <result> JSON block with keys:
- app_spec: {features, db_models, api_routes, pages, auth_required, admin_dashboard}
- cost_summary: {aws_monthly_usd, llm_tokens_estimated, llm_cost_usd, steps_estimated}
- full_plan: [list of step names]""",
            allowed_namespaces=["plan"],
            model="claude-haiku-4-5-20251001",
        )
