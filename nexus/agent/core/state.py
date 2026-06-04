from __future__ import annotations
from contextvars import ContextVar
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import json

_session_id_var: ContextVar[str] = ContextVar("session_id", default="unknown")

def set_session_id(sid: str) -> None:
    _session_id_var.set(sid)

def get_session_id() -> str:
    return _session_id_var.get()

class Phase(str, Enum):
    PLANNING = "PLANNING"
    BACKEND = "BACKEND"
    FRONTEND = "FRONTEND"
    INFRA = "INFRA"
    TEST = "TEST"
    MONITORING = "MONITORING"
    COMPLETE = "COMPLETE"

@dataclass
class AppSpec:
    features: list[str]
    db_models: list[str]
    api_routes: list[str]
    pages: list[str]
    auth_required: bool = True
    admin_dashboard: bool = True

@dataclass
class CostSummary:
    aws_monthly_usd: float
    llm_tokens_estimated: int
    llm_cost_usd: float
    steps_estimated: int

@dataclass
class BackendManifest:
    files_created: list[str]
    api_routes: list[str]
    env_vars_required: list[str]
    dockerfile_path: str
    test_results: dict[str, int]

@dataclass
class FrontendManifest:
    files_created: list[str]
    dockerfile_path: str
    static_build_cmd: str
    test_results: dict[str, int]

@dataclass
class DeploymentResult:
    cluster_name: str
    frontend_url: str
    backend_url: str
    rds_endpoint: str
    resource_arns: dict[str, str]

@dataclass
class TestReport:
    __test__ = False  # prevent pytest from collecting this dataclass as a test suite
    integration_passed: int
    integration_failed: int
    e2e_passed: int
    e2e_failed: int
    coverage_pct: float

@dataclass
class BuildState:
    session_id: str
    user_description: str
    current_phase: Phase = Phase.PLANNING
    app_spec: AppSpec | None = None
    cost_summary: CostSummary | None = None
    backend_manifest: BackendManifest | None = None
    frontend_manifest: FrontendManifest | None = None
    deployment_result: DeploymentResult | None = None
    test_report: TestReport | None = None
    errors: list[dict] = field(default_factory=list)
    tool_call_count: int = 0
    checkpointed_at: datetime | None = None

    def checkpoint(self, path: Path) -> None:
        self.checkpointed_at = datetime.now(timezone.utc)
        data = asdict(self)
        data["current_phase"] = self.current_phase.value
        data["checkpointed_at"] = self.checkpointed_at.isoformat()
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def from_checkpoint(cls, path: Path) -> BuildState:
        data = json.loads(path.read_text())
        data["current_phase"] = Phase(data["current_phase"])
        if data.get("checkpointed_at"):
            data["checkpointed_at"] = datetime.fromisoformat(data["checkpointed_at"])
        for key, klass in [
            ("app_spec", AppSpec), ("cost_summary", CostSummary),
            ("backend_manifest", BackendManifest), ("frontend_manifest", FrontendManifest),
            ("deployment_result", DeploymentResult), ("test_report", TestReport),
        ]:
            if data.get(key):
                data[key] = klass(**data[key])
        return cls(**data)
