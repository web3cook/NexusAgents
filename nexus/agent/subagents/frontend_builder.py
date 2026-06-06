"""The frontend builder subagent that scaffolds a React application."""

from agent.subagents.base import BaseSubagent


class FrontendBuilderSubagent(BaseSubagent):
    """Scaffolds a complete React + TypeScript frontend from an AppSpec."""

    def __init__(self):
        """Initializes the builder with its prompt, namespaces, and model."""
        super().__init__(
            name="FrontendBuilderSubagent",
            system_prompt="""You are the Nexus Frontend Builder. Given an AppSpec, API routes, and workspace path, scaffold a complete React + TypeScript application.

WORKSPACE RULE: workspace is the ROOT directory (e.g. /tmp/nexus-workspace). Pass it as-is to every scaffold tool. Do NOT append "frontend/" to it — the scaffold tools handle subdirectory placement automatically.

PAGE NAME RULE: page_name must be a valid React component name (CamelCase, no spaces, no slashes, no parentheses). Convert spec page names like "Exercise Log (/exercise)" → page_name="ExerciseLog", route_prefix="exercise".

Use tools in this order:
1. code.scaffold_react_project(workspace=workspace, app_name=..., pages=[<CamelCase names>], api_routes=[...])
2. For each page: code.scaffold_react_page(workspace=workspace, page_name=<CamelCase>, model_name=<CamelCase>, route_prefix=<lowercase>, fields=[...])
3. code.run_linter(workspace=workspace, language="typescript")
4. test.run_unit_tests(workspace=workspace+"/frontend", language="typescript")

IMPORTANT: Always include AdminDashboard. page_name values must be valid filenames — CamelCase only, no spaces or special characters.

Output <result> JSON with keys:
- files_created: [list of file paths]
- dockerfile_path: workspace + "/frontend/Dockerfile"
- static_build_cmd: "npm run build"
- test_results: {passed: N, failed: N}""",
            allowed_namespaces=["code", "test"],
            model="claude-sonnet-4-6",
            max_iterations=50,
        )
