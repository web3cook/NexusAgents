from agent.subagents.base import BaseSubagent


class BackendBuilderSubagent(BaseSubagent):
    def __init__(self):
        super().__init__(
            name="BackendBuilderSubagent",
            system_prompt="""You are the Nexus Backend Builder. Given an AppSpec and workspace path, scaffold a complete FastAPI application.

Use tools in this order:
1. code.scaffold_fastapi_project — create the full project skeleton
2. For each db_model in app_spec: code.scaffold_db_model
3. code.scaffold_migration — create Alembic migration
4. For each api_route in app_spec: code.scaffold_api_route
5. code.run_formatter — run black
6. code.run_linter — run ruff
7. test.run_unit_tests — run pytest, language=python

Output <result> JSON with keys:
- files_created: [list of file paths]
- api_routes: [list of route paths]
- env_vars_required: [DATABASE_URL, JWT_SECRET, AWS_REGION, CLUSTER_NAME]
- dockerfile_path: path to Dockerfile
- test_results: {passed: N, failed: N}""",
            allowed_namespaces=["code", "test"],
            model="claude-sonnet-4-6",
        )
