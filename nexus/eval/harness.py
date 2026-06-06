from __future__ import annotations
import subprocess
import httpx
from dataclasses import dataclass, field
from typing import Callable
from agent.core.state import BuildState


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class EvalCase:
    description: str
    checks: list[Callable[[BuildState], CheckResult]] = field(default_factory=list)


class Check:
    @staticmethod
    def http_200(url_attr: str) -> Callable[[BuildState], CheckResult]:
        def _check(state: BuildState) -> CheckResult:
            if not state.deployment_result:
                return CheckResult(f"http_200({url_attr})", False, "No deployment result")
            url = getattr(state.deployment_result, url_attr, None)
            if not url:
                return CheckResult(f"http_200({url_attr})", False, f"Attribute {url_attr} not set")
            try:
                resp = httpx.get(url, timeout=10.0)
                return CheckResult(f"http_200({url_attr})", resp.status_code == 200, f"status={resp.status_code}")
            except Exception as exc:
                return CheckResult(f"http_200({url_attr})", False, str(exc))
        return _check

    @staticmethod
    def auth_flow_works(url_attr: str) -> Callable[[BuildState], CheckResult]:
        def _check(state: BuildState) -> CheckResult:
            if not state.deployment_result:
                return CheckResult("auth_flow_works", False, "No deployment result")
            base = getattr(state.deployment_result, url_attr, None)
            if not base:
                return CheckResult("auth_flow_works", False, "No backend URL")
            try:
                reg = httpx.post(f"{base}/auth/register",
                    json={"email": "eval@nexus.test", "password": "EvalPass123!", "name": "Eval User"}, timeout=10.0)
                login = httpx.post(f"{base}/auth/login",
                    json={"email": "eval@nexus.test", "password": "EvalPass123!"}, timeout=10.0)
                token = login.json().get("access_token", "")
                return CheckResult("auth_flow_works", bool(token), f"register={reg.status_code} login={login.status_code}")
            except Exception as exc:
                return CheckResult("auth_flow_works", False, str(exc))
        return _check

    @staticmethod
    def k8s_pods_healthy(attr: str = "cluster_name") -> Callable[[BuildState], CheckResult]:
        def _check(state: BuildState) -> CheckResult:
            if not state.deployment_result:
                return CheckResult("k8s_pods_healthy", False, "No deployment result")
            result = subprocess.run(
                ["kubectl", "get", "pods", "--all-namespaces", "--no-headers"],
                capture_output=True, text=True,
            )
            lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
            unhealthy = [ln for ln in lines if "Running" not in ln and "Completed" not in ln]
            return CheckResult("k8s_pods_healthy", len(unhealthy) == 0,
                               f"{len(lines)} pods, {len(unhealthy)} unhealthy")
        return _check

    @staticmethod
    def telegram_alert_fires(inject_error: bool = True) -> Callable[[BuildState], CheckResult]:
        def _check(state: BuildState) -> CheckResult:
            try:
                from agent.tools.alert.tools import parse_log_for_errors
                result = parse_log_for_errors(log_text="ERROR: connection refused\n" * 10)
                return CheckResult("telegram_alert_fires", result["error_count"] >= 10,
                                   f"error_count={result['error_count']}")
            except Exception as exc:
                return CheckResult("telegram_alert_fires", False, str(exc))
        return _check

    @staticmethod
    def cost_summary_present() -> Callable[[BuildState], CheckResult]:
        def _check(state: BuildState) -> CheckResult:
            ok = state.cost_summary is not None
            detail = f"aws=${state.cost_summary.aws_monthly_usd:.2f}" if ok else "missing"
            return CheckResult("cost_summary_present", ok, detail)
        return _check

    @staticmethod
    def tool_call_count_gte(n: int) -> Callable[[BuildState], CheckResult]:
        def _check(state: BuildState) -> CheckResult:
            return CheckResult(f"tool_call_count_gte({n})", state.tool_call_count >= n,
                               f"actual={state.tool_call_count}")
        return _check


def run_eval(eval_case: EvalCase, state: BuildState) -> dict:
    results = [check(state) for check in eval_case.checks]
    passed = sum(1 for r in results if r.passed)
    return {
        "description": eval_case.description,
        "passed": passed,
        "failed": len(results) - passed,
        "total": len(results),
        "results": [{"name": r.name, "passed": r.passed, "detail": r.detail} for r in results],
    }
