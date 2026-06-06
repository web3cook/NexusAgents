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


class AgentStatus(str, Enum):
    PENDING        = "Pending"
    ONGOING        = "Ongoing"
    CODE_COMPLETED = "Code Completed"
    TESTED         = "Tested"


class Phase(str, Enum):
    PLANNING   = "PLANNING"
    API_SPEC   = "API_SPEC"
    BUILD      = "BUILD"
    INFRA      = "INFRA"
    TEST       = "TEST"
    MONITORING = "MONITORING"
    COMPLETE   = "COMPLETE"


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
    api_spec_path: str | None = None          # path to generated openapi.yaml
    backend_manifest: BackendManifest | None = None
    frontend_manifest: FrontendManifest | None = None
    deployment_result: DeploymentResult | None = None
    test_report: TestReport | None = None
    errors: list[dict] = field(default_factory=list)
    tool_call_count: int = 0
    checkpointed_at: datetime | None = None
    # Agent lifecycle tracking
    agent_statuses: dict[str, str] = field(default_factory=dict)   # agent_name → AgentStatus.value
    file_registry: list[dict] = field(default_factory=list)         # [{path, category, created_at}]
    aws_resources: dict = field(default_factory=dict)               # resource_type → {id, arn, ...}
    cost_tracking: dict = field(default_factory=lambda: {
        "total_usd": 0.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "calls": 0,
        "by_model": {},
    })

    # ── status helpers ────────────────────────────────────────────────────────

    def set_agent_status(self, agent_name: str, status: AgentStatus) -> None:
        self.agent_statuses[agent_name] = status.value

    def register_file(self, path: str, category: str) -> None:
        self.file_registry.append({
            "path": path,
            "category": category,
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    def add_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cache_read: int,
        cache_creation: int,
        model: str,
    ) -> None:
        from agent.core.cost import compute_cost
        usd = compute_cost(input_tokens, output_tokens, cache_read, cache_creation, model)
        ct = self.cost_tracking
        ct["total_usd"]             += usd
        ct["input_tokens"]          += input_tokens
        ct["output_tokens"]         += output_tokens
        ct["cache_read_tokens"]     += cache_read
        ct["cache_creation_tokens"] += cache_creation
        ct["calls"]                 += 1
        ct["by_model"].setdefault(model, {"usd": 0.0, "calls": 0})
        ct["by_model"][model]["usd"]   += usd
        ct["by_model"][model]["calls"] += 1

    # ── persistence ───────────────────────────────────────────────────────────

    def checkpoint(self, path: Path) -> None:
        self.checkpointed_at = datetime.now(timezone.utc)
        data = asdict(self)
        data["current_phase"] = self.current_phase.value
        data["checkpointed_at"] = self.checkpointed_at.isoformat()
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def from_checkpoint(cls, path: Path) -> BuildState:
        data = json.loads(path.read_text())
        raw_phase = data["current_phase"]
        # Migrate old phase names that no longer exist in the new sequence
        _phase_migration = {"BACKEND": "BUILD", "FRONTEND": "BUILD"}
        raw_phase = _phase_migration.get(raw_phase, raw_phase)
        data["current_phase"] = Phase(raw_phase)
        if data.get("checkpointed_at"):
            data["checkpointed_at"] = datetime.fromisoformat(data["checkpointed_at"])
        for key, klass in [
            ("app_spec", AppSpec), ("cost_summary", CostSummary),
            ("backend_manifest", BackendManifest), ("frontend_manifest", FrontendManifest),
            ("deployment_result", DeploymentResult), ("test_report", TestReport),
        ]:
            if data.get(key):
                data[key] = klass(**data[key])
        # Ensure new fields exist when loading old checkpoints
        data.setdefault("agent_statuses", {})
        data.setdefault("file_registry", [])
        data.setdefault("api_spec_path", None)
        data.setdefault("aws_resources", {})
        data.setdefault("cost_tracking", {
            "total_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "calls": 0, "by_model": {},
        })
        return cls(**data)
