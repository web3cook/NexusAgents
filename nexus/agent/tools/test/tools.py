"""Test execution tools for pytest, vitest, integration tests, and e2e."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import httpx

from agent.core.observability import instrument
from agent.core.retry import rate_limit
from agent.tools.registry import registry


def _run(
    cmd: list[str], cwd: str | None = None, timeout: int = 120
) -> subprocess.CompletedProcess:
    """Runs a subprocess with a hard timeout and no interactive stdin.

    Args:
        cmd: The command and arguments to run.
        cwd: Working directory for the command.
        timeout: Hard timeout in seconds.

    Returns:
        A CompletedProcess; on timeout, returncode is 1 and stdout
        describes the failure.
    """
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=cwd, stdin=subprocess.DEVNULL, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        result = subprocess.CompletedProcess(cmd, returncode=1)
        result.stdout = f"timed out after {timeout}s"
        result.stderr = ""
        return result


@registry.register(
    name="test.run_unit_tests",
    description=(
        "Run pytest (Python) or vitest (TypeScript) unit tests "
        "in the workspace"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "workspace": {"type": "string"},
            "language": {"type": "string", "enum": ["python", "typescript"]},
        },
        "required": ["workspace", "language"],
    },
)
@instrument(namespace="test", tool="run_unit_tests")
def run_unit_tests(workspace: str, language: str) -> dict:
    """Runs pytest (Python) or vitest (TypeScript) unit tests.

    Skips vitest if node_modules are not installed.

    Args:
        workspace: The root workspace directory.
        language: "python" or "typescript".

    Returns:
        A dict with passed, failed, returncode, and truncated stdout.
    """
    rate_limit("test")
    if language == "python":
        result = _run(
            [sys.executable, "-m", "pytest", workspace,
             "-v", "--tb=short", "-q"],
            timeout=120,
        )
        passed = result.stdout.count(" passed")
        failed = result.stdout.count(" failed")
    else:
        ws = Path(workspace)
        vitest_bin = ws / "node_modules" / ".bin" / "vitest"
        if not vitest_bin.exists():
            return {
                "passed": 0,
                "failed": 0,
                "returncode": 0,
                "stdout": "vitest skipped — node_modules not installed",
            }
        result = _run(
            [str(vitest_bin), "run", "--reporter=verbose"],
            cwd=workspace, timeout=120,
        )
        passed = result.stdout.count("✓") + result.stdout.count("PASS")
        failed = result.stdout.count("✗") + result.stdout.count("FAIL")
    return {
        "passed": passed,
        "failed": failed,
        "returncode": result.returncode,
        "stdout": result.stdout[-1000:],
    }


@registry.register(
    name="test.run_integration_tests",
    description="Hit live API endpoints and validate responses",
    input_schema={
        "type": "object",
        "properties": {
            "base_url": {"type": "string"},
            "endpoints": {"type": "array", "items": {"type": "object"}},
        },
        "required": ["base_url", "endpoints"],
    },
)
@instrument(namespace="test", tool="run_integration_tests")
def run_integration_tests(
    base_url: str, endpoints: list[dict]
) -> dict:
    """Hits live API endpoints and validates responses.

    Args:
        base_url: The base URL for all endpoints.
        endpoints: List of dicts with path, method, expected_status,
            and optional body.

    Returns:
        A dict with per-endpoint results, passed count, and failed count.
    """
    rate_limit("test")
    results = []
    for ep in endpoints:
        method = ep.get("method", "GET").upper()
        url = base_url.rstrip("/") + ep["path"]
        expected_status = ep.get("expected_status", 200)
        try:
            resp = httpx.request(
                method, url, json=ep.get("body"), timeout=10.0
            )
            results.append({
                "path": ep["path"],
                "status": resp.status_code,
                "passed": resp.status_code == expected_status,
            })
        except Exception as e:
            results.append({
                "path": ep["path"],
                "status": 0,
                "passed": False,
                "error": str(e),
            })
    passed = sum(1 for r in results if r["passed"])
    return {
        "results": results,
        "passed": passed,
        "failed": len(results) - passed,
    }


@registry.register(
    name="test.run_e2e_tests",
    description=(
        "Run Playwright smoke tests against the deployed frontend URL"
    ),
    input_schema={
        "type": "object",
        "properties": {"frontend_url": {"type": "string"}},
        "required": ["frontend_url"],
    },
)
@instrument(namespace="test", tool="run_e2e_tests")
def run_e2e_tests(frontend_url: str) -> dict:
    """Runs Playwright smoke tests against the deployed frontend.

    Args:
        frontend_url: The base URL of the deployed frontend.

    Returns:
        A dict with passed flag, stdout, and stderr.
    """
    rate_limit("test")
    script = f"""
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto("{frontend_url}/login")
    assert page.title() != "", "Page title is empty"
    page.goto("{frontend_url}/admin")
    browser.close()
print("E2E PASSED")
"""
    result = _run([sys.executable, "-c", script], timeout=120)
    return {
        "passed": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr[:500],
    }


@registry.register(
    name="test.check_coverage",
    description="Generate a pytest coverage report for the workspace",
    input_schema={
        "type": "object",
        "properties": {"workspace": {"type": "string"}},
        "required": ["workspace"],
    },
)
@instrument(namespace="test", tool="check_coverage")
def check_coverage(workspace: str) -> dict:
    """Generates a pytest coverage report for the workspace.

    Args:
        workspace: The root workspace directory.

    Returns:
        A dict with coverage_pct and truncated stdout.
    """
    rate_limit("test")
    result = _run(
        [
            sys.executable, "-m", "pytest", workspace,
            "--cov", workspace, "--cov-report", "term-missing", "-q",
        ],
        timeout=120,
    )
    pct = 0.0
    for line in result.stdout.splitlines():
        if "TOTAL" in line:
            parts = line.split()
            try:
                pct = float(parts[-1].strip("%"))
            except (ValueError, IndexError):
                pass
    return {"coverage_pct": pct, "stdout": result.stdout[-1000:]}


@registry.register(
    name="test.run_lint_check",
    description=(
        "Run ruff + black check (Python) across the generated codebase"
    ),
    input_schema={
        "type": "object",
        "properties": {"workspace": {"type": "string"}},
        "required": ["workspace"],
    },
)
@instrument(namespace="test", tool="run_lint_check")
def run_lint_check(workspace: str) -> dict:
    """Runs ruff and black checks across the generated codebase.

    Args:
        workspace: The root workspace directory.

    Returns:
        A dict with ruff_passed, black_passed, and overall passed flag.
    """
    rate_limit("test")
    ruff = _run(["ruff", "check", workspace], timeout=60)
    black = _run(["black", "--check", workspace], timeout=60)
    return {
        "ruff_passed": ruff.returncode == 0,
        "black_passed": black.returncode == 0,
        "passed": ruff.returncode == 0 and black.returncode == 0,
    }


@registry.register(
    name="test.validate_k8s_manifests",
    description=(
        "Run kubectl dry-run on all YAML manifests to validate "
        "before applying"
    ),
    input_schema={
        "type": "object",
        "properties": {"manifests_dir": {"type": "string"}},
        "required": ["manifests_dir"],
    },
)
@instrument(namespace="test", tool="validate_k8s_manifests")
def validate_k8s_manifests(manifests_dir: str) -> dict:
    """Runs kubectl dry-run on all YAML manifests to validate them.

    Args:
        manifests_dir: Directory to search recursively for *.yaml files.

    Returns:
        A dict with per-file results and an all_valid flag.
    """
    rate_limit("test")
    results = []
    for yaml_file in Path(manifests_dir).rglob("*.yaml"):
        r = _run(
            ["kubectl", "apply", "--dry-run=client", "-f", str(yaml_file)],
            timeout=30,
        )
        results.append({
            "file": str(yaml_file),
            "valid": r.returncode == 0,
            "error": r.stderr[:200],
        })
    return {
        "results": results,
        "all_valid": all(r["valid"] for r in results),
    }


@registry.register(
    name="test.health_check_endpoints",
    description=(
        "HTTP health check on a list of endpoints, "
        "returns per-endpoint status"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "endpoints": {
                "type": "array",
                "items": {"type": "string"},
            }
        },
        "required": ["endpoints"],
    },
)
@instrument(namespace="test", tool="health_check_endpoints")
def health_check_endpoints(endpoints: list[str]) -> dict:
    """HTTP health checks a list of URLs.

    Args:
        endpoints: List of URL strings to check.

    Returns:
        A dict with per-URL results and an all_healthy flag.
    """
    rate_limit("test")
    results = []
    for url in endpoints:
        try:
            resp = httpx.get(url, timeout=5.0)
            results.append({
                "url": url,
                "status": resp.status_code,
                "healthy": resp.status_code == 200,
            })
        except Exception as e:
            results.append({
                "url": url,
                "status": 0,
                "healthy": False,
                "error": str(e),
            })
    return {
        "results": results,
        "all_healthy": all(r["healthy"] for r in results),
    }
