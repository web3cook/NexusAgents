# Nexus Parallel Build Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add agent status tracking, OpenAPI spec generation, parallel backend+frontend execution, Jinja2 Docker/K8s templates, real credit tracking, and model-per-role selection to the Nexus autonomous builder.

**Architecture:** New phase sequence `PLANNING → API_SPEC → BUILD → INFRA → TEST → MONITORING` where BUILD runs backend + frontend + Docker + K8s template rendering in parallel via `ProcessPoolExecutor`. A cost tracker accumulates real `response.usage` numbers from every API call. Jinja2 templates pre-render Dockerfiles and K8s manifests before any subagent runs, eliminating ~12 LLM tool calls.

**Tech Stack:** Python 3.11, dataclasses, Jinja2, `concurrent.futures.ProcessPoolExecutor`, `anthropic.types.Usage`, `atexit`, `signal`

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `nexus/agent/core/state.py` | Modify | `AgentStatus` enum, `AgentStatusEntry`, `FileRegistry`, `CostTracking`; new phases `API_SPEC` / `BUILD`; update `BuildState` |
| `nexus/agent/subagents/base.py` | Modify | Accumulate real token costs from `response.usage`; expose `cost_usd` property |
| `nexus/agent/core/cost.py` | Create | Pricing constants and `compute_cost(usage)` helper |
| `nexus/agent/tools/plan/tools.py` | Modify | Add `generate_api_spec` tool that emits OpenAPI 3.0 YAML |
| `nexus/templates/backend/Dockerfile.j2` | Create | Jinja2 FastAPI Dockerfile template |
| `nexus/templates/frontend/Dockerfile.j2` | Create | Jinja2 React/Nginx Dockerfile template |
| `nexus/templates/k8s/deployment.yaml.j2` | Create | Jinja2 K8s Deployment + Service template |
| `nexus/templates/k8s/ingress.yaml.j2` | Create | Jinja2 K8s Ingress template |
| `nexus/agent/core/parallel.py` | Create | `pre_render_build_templates()`, `run_build_parallel()`, worker functions |
| `nexus/agent/core/orchestrator.py` | Modify | New phase loop, status updates, file registry, atexit + signal handler, cost display |
| `nexus/agent/subagents/planner.py` | Modify | Model → `claude-haiku-4-5-20251001` |
| `nexus/agent/subagents/alerting.py` | Modify | Model → `claude-haiku-4-5-20251001` |
| `nexus/cli.py` | Modify | Print cost breakdown on exit |
| `nexus/tests/unit/test_state.py` | Modify | Tests for new state fields |
| `nexus/tests/unit/test_cost.py` | Create | Tests for `compute_cost()` |
| `nexus/tests/unit/test_parallel.py` | Create | Tests for template rendering |
| `nexus/tests/unit/tools/test_plan_tools.py` | Modify | Test for `generate_api_spec` |

---

## Task 1: Expand state model (`state.py`)

**Files:**
- Modify: `nexus/agent/core/state.py`
- Modify: `nexus/tests/unit/test_state.py`

- [ ] **Step 1: Write the failing tests**

```python
# nexus/tests/unit/test_state.py  (add to existing file)
from agent.core.state import (
    AgentStatus, AgentStatusEntry, FileRegistry, CostTracking,
    Phase, BuildState,
)

def test_agent_status_ordering():
    assert AgentStatus.PENDING != AgentStatus.ONGOING
    assert list(AgentStatus) == [
        AgentStatus.PENDING, AgentStatus.ONGOING,
        AgentStatus.CODE_COMPLETED, AgentStatus.TESTED,
    ]

def test_build_state_has_new_phases():
    assert Phase.API_SPEC in list(Phase)
    assert Phase.BUILD in list(Phase)
    # Old phases must NOT exist — they are replaced
    assert "BACKEND" not in [p.value for p in Phase]
    assert "FRONTEND" not in [p.value for p in Phase]

def test_build_state_agent_statuses():
    s = BuildState(session_id="x", user_description="test")
    assert s.agent_statuses == {}
    s.set_agent_status("BackendBuilderSubagent", AgentStatus.ONGOING)
    assert s.agent_statuses["BackendBuilderSubagent"] == AgentStatus.ONGOING.value

def test_build_state_file_registry():
    s = BuildState(session_id="x", user_description="test")
    s.register_file("/tmp/ws/backend/main.py", "backend")
    assert len(s.file_registry) == 1
    assert s.file_registry[0]["path"] == "/tmp/ws/backend/main.py"

def test_build_state_cost_tracking():
    s = BuildState(session_id="x", user_description="test")
    s.add_cost(input_tokens=1000, output_tokens=200,
                cache_read=800, cache_creation=0, model="claude-opus-4-8")
    assert s.cost_tracking["total_usd"] > 0
    assert s.cost_tracking["calls"] == 1

def test_checkpoint_roundtrip_with_new_fields():
    import tempfile, json
    from pathlib import Path
    s = BuildState(session_id="rt", user_description="roundtrip")
    s.set_agent_status("BackendBuilderSubagent", AgentStatus.CODE_COMPLETED)
    s.register_file("/tmp/x.py", "backend")
    s.add_cost(input_tokens=100, output_tokens=50,
                cache_read=0, cache_creation=0, model="claude-sonnet-4-6")
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        path = Path(f.name)
    s.checkpoint(path)
    s2 = BuildState.from_checkpoint(path)
    assert s2.agent_statuses["BackendBuilderSubagent"] == AgentStatus.CODE_COMPLETED.value
    assert len(s2.file_registry) == 1
    assert s2.cost_tracking["calls"] == 1
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd nexus && python -m pytest tests/unit/test_state.py -v --tb=short -q 2>&1 | tail -20
```

Expected: `FAILED` — `AgentStatus`, `Phase.API_SPEC`, new methods not found.

- [ ] **Step 3: Implement state.py changes**

Replace `nexus/agent/core/state.py` with:

```python
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
    __test__ = False
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
    # New fields
    agent_statuses: dict[str, str] = field(default_factory=dict)   # agent_name → AgentStatus.value
    file_registry: list[dict] = field(default_factory=list)         # [{path, category, created_at}]
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
        ct["total_usd"]            += usd
        ct["input_tokens"]         += input_tokens
        ct["output_tokens"]        += output_tokens
        ct["cache_read_tokens"]    += cache_read
        ct["cache_creation_tokens"]+= cache_creation
        ct["calls"]                += 1
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
        # Migrate old phase names that no longer exist
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
        data.setdefault("cost_tracking", {
            "total_usd": 0.0, "input_tokens": 0, "output_tokens": 0,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "calls": 0, "by_model": {},
        })
        return cls(**data)
```

- [ ] **Step 4: Run tests**

```bash
cd nexus && python -m pytest tests/unit/test_state.py -v --tb=short -q 2>&1 | tail -25
```

Expected: all new tests pass (will fail on `compute_cost` import until Task 2 — comment out `test_build_state_cost_tracking` for now).

- [ ] **Step 5: Commit**

```bash
git add nexus/agent/core/state.py nexus/tests/unit/test_state.py
git commit -m "feat: expand BuildState with AgentStatus, file registry, cost tracking, API_SPEC+BUILD phases"
```

---

## Task 2: Pricing module (`cost.py`)

**Files:**
- Create: `nexus/agent/core/cost.py`
- Create: `nexus/tests/unit/test_cost.py`

- [ ] **Step 1: Write failing tests**

```python
# nexus/tests/unit/test_cost.py
from agent.core.cost import compute_cost

def test_opus_cost_nonzero():
    usd = compute_cost(
        input_tokens=1000, output_tokens=200,
        cache_read=800, cache_creation=0,
        model="claude-opus-4-8",
    )
    assert usd > 0

def test_cache_read_cheaper_than_input():
    base = compute_cost(1000, 0, 0, 0, "claude-opus-4-8")
    cached = compute_cost(0, 0, 1000, 0, "claude-opus-4-8")
    assert cached < base

def test_unknown_model_falls_back():
    usd = compute_cost(1000, 200, 0, 0, "some-unknown-model")
    assert usd >= 0

def test_zero_tokens_is_zero():
    assert compute_cost(0, 0, 0, 0, "claude-sonnet-4-6") == 0.0
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd nexus && python -m pytest tests/unit/test_cost.py -v --tb=short -q 2>&1 | tail -10
```

- [ ] **Step 3: Implement cost.py**

Create `nexus/agent/core/cost.py`:

```python
from __future__ import annotations

# USD per 1M tokens — source: Anthropic pricing page (2026-06)
# Format: {model_id: (input, output, cache_read, cache_creation)}
_PRICING: dict[str, tuple[float, float, float, float]] = {
    "claude-opus-4-8":          (15.00,  75.00,  1.50, 18.75),
    "claude-sonnet-4-6":         (3.00,  15.00,  0.30,  3.75),
    "claude-haiku-4-5-20251001": (0.80,   4.00,  0.08,  1.00),
    # Fallback — use Sonnet pricing for unknown models
    "_default":                  (3.00,  15.00,  0.30,  3.75),
}

_M = 1_000_000


def compute_cost(
    input_tokens: int,
    output_tokens: int,
    cache_read: int,
    cache_creation: int,
    model: str,
) -> float:
    inp, out, cr, cc = _PRICING.get(model, _PRICING["_default"])
    return (
        input_tokens    * inp / _M
        + output_tokens * out / _M
        + cache_read    * cr  / _M
        + cache_creation * cc / _M
    )
```

- [ ] **Step 4: Run tests**

```bash
cd nexus && python -m pytest tests/unit/test_cost.py -v --tb=short -q 2>&1 | tail -10
```

Expected: 4 passed.

- [ ] **Step 5: Uncomment cost test in test_state.py, re-run state tests**

```bash
cd nexus && python -m pytest tests/unit/test_state.py -v --tb=short -q 2>&1 | tail -20
```

Expected: all state tests pass.

- [ ] **Step 6: Commit**

```bash
git add nexus/agent/core/cost.py nexus/tests/unit/test_cost.py nexus/tests/unit/test_state.py
git commit -m "feat: add pricing module and wire cost tracking into BuildState"
```

---

## Task 3: Jinja2 Dockerfile and K8s templates

**Files:**
- Create: `nexus/templates/backend/Dockerfile.j2`
- Create: `nexus/templates/frontend/Dockerfile.j2`
- Create: `nexus/templates/k8s/deployment.yaml.j2`
- Create: `nexus/templates/k8s/ingress.yaml.j2`
- Create: `nexus/agent/core/parallel.py` (template rendering part only)
- Create: `nexus/tests/unit/test_parallel.py`

The `templates/` directory already exists with `fastapi/`, `k8s/`, and `react/` subdirs.

- [ ] **Step 1: Check what's in templates already**

```bash
find nexus/templates -type f | sort
```

Note existing files — don't overwrite anything useful.

- [ ] **Step 2: Write failing test for template rendering**

```python
# nexus/tests/unit/test_parallel.py
import tempfile
from pathlib import Path
from agent.core.parallel import pre_render_build_templates
from agent.core.state import AppSpec

SPEC = AppSpec(
    features=["auth", "dashboard"],
    db_models=["User", "Post"],
    api_routes=["/auth", "/posts"],
    pages=["Login", "Dashboard"],
)

def test_pre_render_creates_dockerfiles():
    with tempfile.TemporaryDirectory() as ws:
        pre_render_build_templates(SPEC, ws)
        assert (Path(ws) / "backend" / "Dockerfile").exists()
        assert (Path(ws) / "frontend" / "Dockerfile").exists()

def test_pre_render_creates_k8s_manifests():
    with tempfile.TemporaryDirectory() as ws:
        pre_render_build_templates(SPEC, ws)
        k8s = Path(ws) / "k8s"
        assert (k8s / "backend-deployment.yaml").exists()
        assert (k8s / "frontend-deployment.yaml").exists()
        assert (k8s / "ingress.yaml").exists()

def test_pre_render_injects_spec_values():
    with tempfile.TemporaryDirectory() as ws:
        pre_render_build_templates(SPEC, ws)
        content = (Path(ws) / "k8s" / "backend-deployment.yaml").read_text()
        assert "nexus-backend" in content

def test_pre_render_returns_file_list():
    with tempfile.TemporaryDirectory() as ws:
        files = pre_render_build_templates(SPEC, ws)
        assert len(files) >= 5
        assert all(Path(f).exists() for f in files)
```

- [ ] **Step 3: Run to confirm failure**

```bash
cd nexus && python -m pytest tests/unit/test_parallel.py -v --tb=short -q 2>&1 | tail -10
```

- [ ] **Step 4: Create backend Dockerfile template**

Create `nexus/templates/backend/Dockerfile.j2`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE {{ port | default(8000) }}

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "{{ port | default(8000) }}"]
```

- [ ] **Step 5: Create frontend Dockerfile template**

Create `nexus/templates/frontend/Dockerfile.j2`:

```dockerfile
FROM node:20-alpine AS build

WORKDIR /app
COPY package*.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

- [ ] **Step 6: Create K8s deployment template**

Create `nexus/templates/k8s/deployment.yaml.j2`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ name }}
  namespace: {{ namespace | default("default") }}
spec:
  replicas: {{ replicas | default(2) }}
  selector:
    matchLabels:
      app: {{ name }}
  template:
    metadata:
      labels:
        app: {{ name }}
    spec:
      containers:
      - name: {{ name }}
        image: {{ image }}
        ports:
        - containerPort: {{ port | default(8000) }}
        env:
{% for key, value in env_vars.items() %}
        - name: {{ key }}
          value: "{{ value }}"
{% endfor %}
        resources:
          requests:
            memory: "256Mi"
            cpu: "100m"
          limits:
            memory: "512Mi"
            cpu: "500m"
---
apiVersion: v1
kind: Service
metadata:
  name: {{ name }}-svc
  namespace: {{ namespace | default("default") }}
spec:
  selector:
    app: {{ name }}
  ports:
  - port: 80
    targetPort: {{ port | default(8000) }}
  type: ClusterIP
```

- [ ] **Step 7: Create K8s ingress template**

Create `nexus/templates/k8s/ingress.yaml.j2`:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: nexus-ingress
  namespace: {{ namespace | default("default") }}
  annotations:
    kubernetes.io/ingress.class: "alb"
    alb.ingress.kubernetes.io/scheme: internet-facing
    alb.ingress.kubernetes.io/target-type: ip
spec:
  rules:
  - http:
      paths:
      - path: /api
        pathType: Prefix
        backend:
          service:
            name: nexus-backend-svc
            port:
              number: 80
      - path: /
        pathType: Prefix
        backend:
          service:
            name: nexus-frontend-svc
            port:
              number: 80
```

- [ ] **Step 8: Create parallel.py (template rendering part)**

Create `nexus/agent/core/parallel.py`:

```python
from __future__ import annotations
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from agent.core.state import AppSpec

logger = logging.getLogger("nexus.parallel")

_TEMPLATES_DIR = Path(__file__).parent.parent.parent / "templates"


def _render(template_path: Path, **ctx) -> str:
    from jinja2 import Environment, FileSystemLoader, StrictUndefined
    env = Environment(
        loader=FileSystemLoader(str(template_path.parent)),
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    return env.get_template(template_path.name).render(**ctx)


def pre_render_build_templates(app_spec: AppSpec, workspace: str) -> list[str]:
    """Render Dockerfile and K8s templates from AppSpec without any LLM call.

    Returns a list of absolute paths of files written.
    """
    ws = Path(workspace)
    written: list[str] = []

    # ── Backend Dockerfile ───────────────────────────────────────────────────
    tmpl = _TEMPLATES_DIR / "backend" / "Dockerfile.j2"
    if tmpl.exists():
        backend_dir = ws / "backend"
        backend_dir.mkdir(parents=True, exist_ok=True)
        out = backend_dir / "Dockerfile"
        out.write_text(_render(tmpl, port=8000))
        written.append(str(out))
        logger.debug("rendered %s", out)

    # ── Frontend Dockerfile ──────────────────────────────────────────────────
    tmpl = _TEMPLATES_DIR / "frontend" / "Dockerfile.j2"
    if tmpl.exists():
        frontend_dir = ws / "frontend"
        frontend_dir.mkdir(parents=True, exist_ok=True)
        out = frontend_dir / "Dockerfile"
        out.write_text(_render(tmpl))
        written.append(str(out))
        logger.debug("rendered %s", out)

    # ── K8s manifests ────────────────────────────────────────────────────────
    k8s_dir = ws / "k8s"
    k8s_dir.mkdir(parents=True, exist_ok=True)

    depl_tmpl = _TEMPLATES_DIR / "k8s" / "deployment.yaml.j2"
    if depl_tmpl.exists():
        for role, port in [("nexus-backend", 8000), ("nexus-frontend", 80)]:
            out = k8s_dir / f"{role}-deployment.yaml"
            out.write_text(_render(
                depl_tmpl,
                name=role,
                image=f"<ECR_REGISTRY>/{role}:latest",
                port=port,
                env_vars={"AWS_REGION": "us-east-2"},
            ))
            written.append(str(out))

    ingress_tmpl = _TEMPLATES_DIR / "k8s" / "ingress.yaml.j2"
    if ingress_tmpl.exists():
        out = k8s_dir / "ingress.yaml"
        out.write_text(_render(ingress_tmpl))
        written.append(str(out))

    logger.info("pre_render_build_templates: %d files written", len(written))
    return written
```

- [ ] **Step 9: Run template tests**

```bash
cd nexus && python -m pytest tests/unit/test_parallel.py -v --tb=short -q 2>&1 | tail -15
```

Expected: 4 passed.

- [ ] **Step 10: Commit**

```bash
git add nexus/templates/backend/Dockerfile.j2 nexus/templates/frontend/Dockerfile.j2 \
        nexus/templates/k8s/deployment.yaml.j2 nexus/templates/k8s/ingress.yaml.j2 \
        nexus/agent/core/parallel.py nexus/tests/unit/test_parallel.py
git commit -m "feat: Jinja2 Docker/K8s templates and pre_render_build_templates()"
```

---

## Task 4: OpenAPI spec generator tool

**Files:**
- Modify: `nexus/agent/tools/plan/tools.py`
- Modify: `nexus/tests/unit/tools/test_plan_tools.py`

- [ ] **Step 1: Write failing test**

```python
# add to nexus/tests/unit/tools/test_plan_tools.py
import yaml

def test_generate_api_spec_returns_valid_openapi():
    from agent.tools.plan.tools import generate_api_spec
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
    import tempfile, os
    from agent.tools.plan.tools import generate_api_spec
    with tempfile.TemporaryDirectory() as d:
        result = generate_api_spec(
            app_name="TestApp",
            api_routes=["/health"],
            db_models=["Item"],
            features=[],
            output_dir=d,
        )
        assert os.path.exists(result["output_path"])
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd nexus && python -m pytest tests/unit/tools/test_plan_tools.py::test_generate_api_spec_returns_valid_openapi -v --tb=short 2>&1 | tail -10
```

- [ ] **Step 3: Add `generate_api_spec` to plan/tools.py**

Open `nexus/agent/tools/plan/tools.py` and add the following after the existing `render_full_plan` function (and add `import yaml` at the top of file along with `import os`):

```python
@registry.register(
    name="plan.generate_api_spec",
    description="Generate an OpenAPI 3.0 YAML spec from the AppSpec — deterministic, no LLM call",
    input_schema={
        "type": "object",
        "properties": {
            "app_name":   {"type": "string"},
            "api_routes": {"type": "array", "items": {"type": "string"}},
            "db_models":  {"type": "array", "items": {"type": "string"}},
            "features":   {"type": "array", "items": {"type": "string"}},
            "output_dir": {"type": "string"},
        },
        "required": ["app_name", "api_routes", "db_models", "features"],
    },
)
@instrument(namespace="plan", tool="generate_api_spec")
def generate_api_spec(
    app_name: str,
    api_routes: list[str],
    db_models: list[str],
    features: list[str],
    output_dir: str = "/tmp",
) -> dict:
    import yaml as _yaml

    paths: dict = {}
    for route in api_routes:
        # Infer HTTP methods from route name
        methods: list[str] = ["get"]
        if any(k in route for k in ["/login", "/register", "/token"]):
            methods = ["post"]
        elif route.count("/") == 1:
            methods = ["get", "post"]
        else:
            methods = ["get", "put", "delete"]

        path_item: dict = {}
        for method in methods:
            tag = route.strip("/").split("/")[0] or "default"
            path_item[method] = {
                "tags": [tag],
                "summary": f"{method.upper()} {route}",
                "operationId": f"{method}_{route.strip('/').replace('/', '_')}",
                "responses": {
                    "200": {"description": "Successful response",
                            "content": {"application/json": {"schema": {"type": "object"}}}},
                    "400": {"description": "Bad request"},
                    "401": {"description": "Unauthorized"},
                },
            }
            if method in ("post", "put"):
                # Try to find a matching model
                model_name = next(
                    (m for m in db_models if m.lower() in route.lower()), db_models[0] if db_models else "Item"
                )
                path_item[method]["requestBody"] = {
                    "required": True,
                    "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{model_name}Request"}}},
                }
        paths[route] = path_item

    # Build schemas for each model
    schemas: dict = {}
    for model in db_models:
        schemas[model] = {
            "type": "object",
            "properties": {
                "id":         {"type": "integer", "example": 1},
                "created_at": {"type": "string", "format": "date-time"},
            },
            "required": ["id"],
        }
        schemas[f"{model}Request"] = {
            "type": "object",
            "properties": {},
            "required": [],
        }

    if "auth" in features:
        paths.setdefault("/auth/login", {})["post"] = {
            "tags": ["auth"],
            "summary": "POST /auth/login",
            "operationId": "post_auth_login",
            "requestBody": {
                "required": True,
                "content": {"application/json": {"schema": {
                    "type": "object",
                    "properties": {
                        "email":    {"type": "string", "example": "user@example.com"},
                        "password": {"type": "string", "example": "secret"},
                    },
                    "required": ["email", "password"],
                }}},
            },
            "responses": {
                "200": {"description": "JWT token",
                        "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {"access_token": {"type": "string"}, "token_type": {"type": "string"}},
                        }}}},
                "401": {"description": "Invalid credentials"},
            },
        }

    spec = {
        "openapi": "3.0.0",
        "info": {"title": app_name, "version": "1.0.0"},
        "paths": paths,
        "components": {"schemas": schemas},
    }

    yaml_text = _yaml.dump(spec, default_flow_style=False, allow_unicode=True, sort_keys=True)

    import os
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, "openapi.yaml")
    with open(out_path, "w") as fh:
        fh.write(yaml_text)

    return {"openapi_yaml": yaml_text, "output_path": out_path, "route_count": len(paths)}
```

- [ ] **Step 4: Run tests**

```bash
cd nexus && python -m pytest tests/unit/tools/test_plan_tools.py -v --tb=short -q 2>&1 | tail -15
```

Expected: all plan tool tests pass.

- [ ] **Step 5: Commit**

```bash
git add nexus/agent/tools/plan/tools.py nexus/tests/unit/tools/test_plan_tools.py
git commit -m "feat: add plan.generate_api_spec — deterministic OpenAPI 3.0 YAML from AppSpec"
```

---

## Task 5: Real cost tracking in BaseSubagent

**Files:**
- Modify: `nexus/agent/subagents/base.py`

- [ ] **Step 1: Write failing test**

```python
# nexus/tests/unit/test_subagent_base.py  (new file)
from unittest.mock import MagicMock, patch
from agent.subagents.base import BaseSubagent

def _make_usage(inp=100, out=50, cr=80, cc=0):
    u = MagicMock()
    u.input_tokens = inp
    u.output_tokens = out
    u.cache_read_input_tokens = cr
    u.cache_creation_input_tokens = cc
    return u

def test_subagent_accumulates_cost():
    agent = BaseSubagent(
        name="Test", system_prompt="test", allowed_namespaces=[], model="claude-haiku-4-5-20251001"
    )
    agent._accumulate_usage(_make_usage(inp=1000, out=200, cr=800, cc=0))
    assert agent.total_cost_usd > 0
    assert agent.total_input_tokens == 1000
    assert agent.total_output_tokens == 200

def test_subagent_cost_summary_dict():
    agent = BaseSubagent(
        name="Test", system_prompt="test", allowed_namespaces=[], model="claude-sonnet-4-6"
    )
    agent._accumulate_usage(_make_usage(inp=500, out=100, cr=400, cc=0))
    summary = agent.cost_summary()
    assert "total_cost_usd" in summary
    assert "calls" in summary
    assert summary["calls"] == 1
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd nexus && python -m pytest tests/unit/test_subagent_base.py -v --tb=short -q 2>&1 | tail -10
```

- [ ] **Step 3: Add cost tracking to base.py**

In `nexus/agent/subagents/base.py`, add the following to `__init__` and new methods:

```python
# In __init__, after self._logger = ...:
self.total_cost_usd: float = 0.0
self.total_input_tokens: int = 0
self.total_output_tokens: int = 0
self.total_cache_read: int = 0
self.total_cache_creation: int = 0
self._api_calls: int = 0

# New methods on BaseSubagent:
def _accumulate_usage(self, usage) -> None:
    from agent.core.cost import compute_cost
    inp  = getattr(usage, "input_tokens", 0)
    out  = getattr(usage, "output_tokens", 0)
    cr   = getattr(usage, "cache_read_input_tokens", 0)
    cc   = getattr(usage, "cache_creation_input_tokens", 0)
    self.total_input_tokens    += inp
    self.total_output_tokens   += out
    self.total_cache_read      += cr
    self.total_cache_creation  += cc
    self._api_calls            += 1
    self.total_cost_usd        += compute_cost(inp, out, cr, cc, self.model)

def cost_summary(self) -> dict:
    return {
        "total_cost_usd": round(self.total_cost_usd, 6),
        "input_tokens": self.total_input_tokens,
        "output_tokens": self.total_output_tokens,
        "cache_read_tokens": self.total_cache_read,
        "calls": self._api_calls,
        "model": self.model,
    }
```

Then in the `run()` method, after `response = client.messages.create(...)`, add:

```python
self._accumulate_usage(response.usage)
```

- [ ] **Step 4: Run tests**

```bash
cd nexus && python -m pytest tests/unit/test_subagent_base.py -v --tb=short -q 2>&1 | tail -10
```

Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add nexus/agent/subagents/base.py nexus/tests/unit/test_subagent_base.py
git commit -m "feat: accumulate real token costs from response.usage in BaseSubagent"
```

---

## Task 6: Parallel BUILD execution (`parallel.py` — worker functions)

**Files:**
- Modify: `nexus/agent/core/parallel.py`

This adds the parallel subprocess runner to the module created in Task 3.

- [ ] **Step 1: Write failing test**

```python
# nexus/tests/unit/test_parallel.py  (add to file from Task 3)
from unittest.mock import patch, MagicMock

def test_run_build_parallel_returns_both_manifests():
    from agent.core.parallel import run_build_parallel
    from agent.core.state import AppSpec, BuildState

    spec = AppSpec(
        features=["auth"], db_models=["User"],
        api_routes=["/auth"], pages=["Login"],
    )
    state = BuildState(session_id="t", user_description="test")
    state.app_spec = spec

    mock_manifest = {
        "files_created": ["/tmp/x.py"],
        "api_routes": ["/auth"],
        "env_vars_required": [],
        "dockerfile_path": "/tmp/Dockerfile",
        "test_results": {"passed": 1, "failed": 0},
    }

    with patch("agent.core.parallel._run_backend_subprocess", return_value=mock_manifest), \
         patch("agent.core.parallel._run_frontend_subprocess", return_value={
             "files_created": ["/tmp/App.tsx"],
             "dockerfile_path": "/tmp/Dockerfile",
             "static_build_cmd": "npm run build",
             "test_results": {"passed": 1, "failed": 0},
         }):
        backend, frontend = run_build_parallel(spec, "/tmp/ws", state)

    assert backend["api_routes"] == ["/auth"]
    assert frontend["files_created"] == ["/tmp/App.tsx"]
```

- [ ] **Step 2: Add worker functions and `run_build_parallel` to parallel.py**

Append to `nexus/agent/core/parallel.py`:

```python
import json
import sys
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed


def _run_backend_subprocess(app_spec_dict: dict, workspace: str) -> dict:
    """Spawn a child Python process running the BackendBuilderSubagent."""
    script = f"""
import json, sys
sys.path.insert(0, "{Path(__file__).parent.parent.parent}")
from agent.subagents.backend_builder import BackendBuilderSubagent
subagent = BackendBuilderSubagent()
result = subagent.run({{"app_spec": {json.dumps(app_spec_dict)}, "workspace": "{workspace}"}})
print("__RESULT__" + json.dumps(result))
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=900,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__"):])
    return {"error": f"subprocess failed: {proc.stderr[-500:]}"}


def _run_frontend_subprocess(app_spec_dict: dict, workspace: str) -> dict:
    """Spawn a child Python process running the FrontendBuilderSubagent."""
    script = f"""
import json, sys
sys.path.insert(0, "{Path(__file__).parent.parent.parent}")
from agent.subagents.frontend_builder import FrontendBuilderSubagent
subagent = FrontendBuilderSubagent()
result = subagent.run({{"app_spec": {json.dumps(app_spec_dict)}, "workspace": "{workspace}"}})
print("__RESULT__" + json.dumps(result))
"""
    proc = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=900,
    )
    for line in proc.stdout.splitlines():
        if line.startswith("__RESULT__"):
            return json.loads(line[len("__RESULT__"):])
    return {"error": f"subprocess failed: {proc.stderr[-500:]}"}


def run_build_parallel(
    app_spec,
    workspace: str,
    state,
) -> tuple[dict, dict]:
    """Run backend + frontend subagents in parallel subprocesses.

    Returns (backend_result, frontend_result).
    Updates state.agent_statuses for both agents.
    """
    from agent.core.state import AgentStatus
    from dataclasses import asdict

    spec_dict = asdict(app_spec)
    state.set_agent_status("BackendBuilderSubagent", AgentStatus.ONGOING)
    state.set_agent_status("FrontendBuilderSubagent", AgentStatus.ONGOING)

    with ProcessPoolExecutor(max_workers=2) as pool:
        fut_backend  = pool.submit(_run_backend_subprocess,  spec_dict, workspace)
        fut_frontend = pool.submit(_run_frontend_subprocess, spec_dict, workspace)

        backend_result  = fut_backend.result(timeout=960)
        frontend_result = fut_frontend.result(timeout=960)

    if "error" not in backend_result:
        state.set_agent_status("BackendBuilderSubagent", AgentStatus.CODE_COMPLETED)
    if "error" not in frontend_result:
        state.set_agent_status("FrontendBuilderSubagent", AgentStatus.CODE_COMPLETED)

    return backend_result, frontend_result
```

- [ ] **Step 3: Run all parallel tests**

```bash
cd nexus && python -m pytest tests/unit/test_parallel.py -v --tb=short -q 2>&1 | tail -20
```

Expected: all tests pass (the new one uses mocks).

- [ ] **Step 4: Commit**

```bash
git add nexus/agent/core/parallel.py nexus/tests/unit/test_parallel.py
git commit -m "feat: parallel BUILD subprocess execution via ProcessPoolExecutor"
```

---

## Task 7: Orchestrator overhaul

**Files:**
- Modify: `nexus/agent/core/orchestrator.py`

This is the biggest change. The orchestrator gets:
- New phase sequence: `PLANNING → API_SPEC → BUILD → INFRA → TEST → MONITORING`
- Status updates before/after each phase
- `atexit` + `SIGTERM` handler for auto-save
- Real cost accumulation from subagent results
- Parallel BUILD phase using `run_build_parallel`

- [ ] **Step 1: Read the current orchestrator**

```bash
cat -n nexus/agent/core/orchestrator.py
```

Confirm `PHASE_TOOLS`, `_update_state`, `_infer_next_phase`.

- [ ] **Step 2: Update PHASE_TOOLS and imports**

Replace the `PHASE_TOOLS` dict in `orchestrator.py`:

```python
PHASE_TOOLS = {
    Phase.PLANNING:   ["subagent", "plan"],
    Phase.API_SPEC:   ["plan"],          # generate_api_spec only — no subagent
    Phase.BUILD:      [],                # handled by run_build_parallel, not LLM tools
    Phase.INFRA:      ["subagent", "aws", "k8s", "docker", "code"],
    Phase.TEST:       ["test"],
    Phase.MONITORING: ["subagent", "alert"],
}
```

Add to the imports at the top:

```python
import atexit
import signal
from agent.core.cost import compute_cost
```

- [ ] **Step 3: Add auto-save handler**

Add this function before `run()` in orchestrator.py:

```python
_active_state: BuildState | None = None
_active_checkpoint: Path | None = None


def _emergency_save(signum=None, frame=None) -> None:
    if _active_state and _active_checkpoint:
        try:
            _active_state.checkpoint(_active_checkpoint)
            logger.info("[yellow]Emergency checkpoint saved: %s[/yellow]", _active_checkpoint)
        except Exception as exc:
            logger.error("Emergency save failed: %s", exc)
```

Then in `run()`, after `checkpoint_path` is established:

```python
global _active_state, _active_checkpoint
_active_state = state
_active_checkpoint = checkpoint_path
atexit.register(_emergency_save)
signal.signal(signal.SIGTERM, _emergency_save)
```

- [ ] **Step 4: Add BUILD phase handler to the main loop**

Inside the `while state.current_phase != Phase.COMPLETE:` loop, add a `BUILD` phase branch before calling the LLM:

```python
# ── BUILD phase — parallel subprocess, no LLM call ──────────────────────
if state.current_phase == Phase.BUILD:
    from agent.core.parallel import pre_render_build_templates, run_build_parallel
    logger.info("  Pre-rendering Docker/K8s templates...")
    rendered = pre_render_build_templates(state.app_spec, workspace)
    for f in rendered:
        state.register_file(f, "template")
    logger.info("  Starting parallel backend + frontend build...")
    backend_result, frontend_result = run_build_parallel(state.app_spec, workspace, state)
    _update_state(state, "subagent.run_backend_builder", backend_result)
    _update_state(state, "subagent.run_frontend_builder", frontend_result)
    new_phase = _infer_next_phase(state)
    if new_phase != state.current_phase:
        state.current_phase = new_phase
        state.checkpoint(checkpoint_path)
    continue  # skip LLM call for BUILD phase
```

- [ ] **Step 5: Add API_SPEC phase handler**

Add before the BUILD phase block in the loop:

```python
# ── API_SPEC phase — deterministic, no LLM call ─────────────────────────
if state.current_phase == Phase.API_SPEC:
    from agent.tools.plan.tools import generate_api_spec
    spec = state.app_spec
    result = generate_api_spec(
        app_name="NexusApp",
        api_routes=spec.api_routes,
        db_models=spec.db_models,
        features=spec.features,
        output_dir=str(Path(workspace) / "api"),
    )
    state.api_spec_path = result["output_path"]
    state.register_file(result["output_path"], "api_spec")
    logger.info("  API spec written: %s (%d routes)", result["output_path"], result["route_count"])
    state.current_phase = Phase.BUILD
    state.checkpoint(checkpoint_path)
    continue
```

- [ ] **Step 6: Update `_infer_next_phase` for new phase sequence**

Replace the function:

```python
def _infer_next_phase(state: BuildState) -> Phase:
    if state.current_phase == Phase.PLANNING and state.app_spec and state.cost_summary:
        return Phase.API_SPEC
    if state.current_phase == Phase.API_SPEC and state.api_spec_path:
        return Phase.BUILD
    if state.current_phase == Phase.BUILD and state.backend_manifest and state.frontend_manifest:
        return Phase.INFRA
    if state.current_phase == Phase.INFRA and state.deployment_result:
        return Phase.TEST
    if state.current_phase == Phase.TEST and state.test_report:
        return Phase.MONITORING
    return state.current_phase
```

- [ ] **Step 7: Add cost accumulation from LLM responses**

In the main loop, after `response = client.messages.create(...)`:

```python
state.add_cost(
    input_tokens=response.usage.input_tokens,
    output_tokens=response.usage.output_tokens,
    cache_read=getattr(response.usage, "cache_read_input_tokens", 0),
    cache_creation=getattr(response.usage, "cache_creation_input_tokens", 0),
    model="claude-opus-4-8",
)
```

- [ ] **Step 8: Update `_phase_from_state` for new phases**

```python
def _phase_from_state(state: BuildState) -> Phase:
    if state.deployment_result:
        return Phase.MONITORING if state.test_report else Phase.TEST
    if state.frontend_manifest and state.backend_manifest:
        return Phase.INFRA
    if state.app_spec and state.cost_summary and state.api_spec_path:
        return Phase.BUILD
    if state.app_spec and state.cost_summary:
        return Phase.API_SPEC
    return Phase.PLANNING
```

- [ ] **Step 9: Commit**

```bash
git add nexus/agent/core/orchestrator.py
git commit -m "feat: orchestrator overhaul — API_SPEC+BUILD phases, parallel build, atexit save, real cost tracking"
```

---

## Task 8: Model selection and planner/alerting → Haiku

**Files:**
- Modify: `nexus/agent/subagents/planner.py`
- Modify: `nexus/agent/subagents/alerting.py`

- [ ] **Step 1: Change PlannerSubagent model**

In `nexus/agent/subagents/planner.py`, change `model="claude-sonnet-4-6"` to `model="claude-haiku-4-5-20251001"`.

- [ ] **Step 2: Change AlertingSubagent model**

In `nexus/agent/subagents/alerting.py`, change the model parameter (currently whatever it is) to `model="claude-haiku-4-5-20251001"`.

- [ ] **Step 3: Verify orchestrator uses Opus**

In `nexus/agent/core/orchestrator.py`, confirm `model="claude-opus-4-8"` in the `client.messages.create()` call.

- [ ] **Step 4: Add model labels to log output**

In `nexus/agent/subagents/base.py`, update the "starting" log line to include model:

```python
self._logger.info(
    "[bold cyan]%s[/bold cyan] starting  [dim]namespaces=%s model=%s[/dim]",
    self.name, self.allowed_namespaces, self.model,
)
```

- [ ] **Step 5: Commit**

```bash
git add nexus/agent/subagents/planner.py nexus/agent/subagents/alerting.py \
        nexus/agent/subagents/base.py
git commit -m "feat: model selection — Haiku for planner/alerting, Sonnet for builders, Opus for orchestrator"
```

---

## Task 9: CLI cost display on exit

**Files:**
- Modify: `nexus/cli.py`

- [ ] **Step 1: Add cost breakdown to the `build` command's success path**

In `nexus/cli.py`, after the `console.print(f"\nTool calls: {state.tool_call_count}")` line:

```python
# Cost breakdown
ct = state.cost_tracking
console.print(f"\n[bold]Cost breakdown:[/bold]")
console.print(f"  Total:        [green]${ct['total_usd']:.4f}[/green]")
console.print(f"  Input tokens: {ct['input_tokens']:,}")
console.print(f"  Output tokens:{ct['output_tokens']:,}")
console.print(f"  Cache reads:  {ct['cache_read_tokens']:,}")
console.print(f"  API calls:    {ct['calls']}")
if ct.get("by_model"):
    console.print("  By model:")
    for model, info in sorted(ct["by_model"].items()):
        console.print(f"    {model}: ${info['usd']:.4f} ({info['calls']} calls)")
```

- [ ] **Step 2: Also print cost when build fails or is interrupted**

In the `except KeyboardInterrupt` block, before `sys.exit(130)`:

```python
if 'state' in dir():
    ct = state.cost_tracking
    console.print(f"\n[yellow]Cost so far: ${ct['total_usd']:.4f}[/yellow]")
```

- [ ] **Step 3: Commit**

```bash
git add nexus/cli.py
git commit -m "feat: show real cost breakdown on CLI exit (success, failure, or interrupt)"
```

---

## Task 10: Wire agent status updates into subagent tools

**Files:**
- Modify: `nexus/agent/tools/subagent/tools.py`

The orchestrator calls subagent tools via `registry.call()`. These tools construct and run the subagent. We need them to update `BuildState.agent_statuses` before/after. Since tools don't have direct state access, we'll use a module-level callback pattern.

- [ ] **Step 1: Read the subagent tools file**

```bash
cat nexus/agent/tools/subagent/tools.py
```

- [ ] **Step 2: Add status callback mechanism to subagent tools**

At the top of `nexus/agent/tools/subagent/tools.py` add:

```python
from typing import Callable
_status_callback: Callable[[str, str], None] | None = None

def set_status_callback(cb: Callable[[str, str], None]) -> None:
    global _status_callback
    _status_callback = cb

def _notify_status(agent_name: str, status: str) -> None:
    if _status_callback:
        _status_callback(agent_name, status)
```

Then in each subagent tool function (e.g. `run_backend_builder`, `run_frontend_builder`), wrap the `subagent.run()` call:

```python
_notify_status("BackendBuilderSubagent", "Ongoing")
result = subagent.run(input_data)
status = "Code Completed" if "error" not in result else "Ongoing"
_notify_status("BackendBuilderSubagent", status)
return result
```

- [ ] **Step 3: Wire the callback in orchestrator.run()**

In `nexus/agent/core/orchestrator.py`, after state is established:

```python
from agent.tools.subagent.tools import set_status_callback
from agent.core.state import AgentStatus

def _status_cb(agent_name: str, status_str: str) -> None:
    try:
        status = AgentStatus(status_str)
    except ValueError:
        return
    state.set_agent_status(agent_name, status)
    state.checkpoint(checkpoint_path)

set_status_callback(_status_cb)
```

- [ ] **Step 4: Commit**

```bash
git add nexus/agent/tools/subagent/tools.py nexus/agent/core/orchestrator.py
git commit -m "feat: agent status callbacks wire subagent Ongoing/Code Completed into checkpoint"
```

---

## Task 11: Full test suite pass

- [ ] **Step 1: Run full unit test suite**

```bash
cd nexus && python -m pytest tests/unit/ -v --tb=short -q 2>&1 | tail -40
```

Expected: all tests pass. Fix any regressions.

- [ ] **Step 2: Run lint**

```bash
cd nexus && ruff check agent/ cli.py && black --check agent/ cli.py
```

Fix any issues, then:

```bash
cd nexus && ruff check agent/ cli.py --fix && black agent/ cli.py
git add -u && git commit -m "fix: lint cleanup after parallel build implementation"
```

- [ ] **Step 3: Smoke test dry-run**

```bash
cd nexus && python -m nexus build "a simple todo app" --dry-run 2>&1 | tail -20
```

Expected: cost estimate prints, no errors.

- [ ] **Step 4: Final commit tag**

```bash
git tag v0.3.0-parallel-build
```

---

## Self-Review Checklist

| Requirement | Task |
|-------------|------|
| AgentStatus (Pending/Ongoing/Code Completed/Tested) | Task 1, Task 10 |
| OpenAPI 3.0 YAML before frontend+backend | Task 4 (generates spec), Task 7 (API_SPEC phase) |
| Status updated before starting agent and throughout | Task 5 (base), Task 10 (tools), Task 7 (orchestrator) |
| All files tracked in session file | Task 1 (`register_file`), Task 7 (orchestrator calls it) |
| Auto-save on exit/error | Task 7 (`atexit` + `SIGTERM`) |
| Real credit tracking from API responses | Task 2 (pricing), Task 5 (base accumulate), Task 7 (orchestrator) |
| Pre-built Dockerfile templates (Jinja2) | Task 3 |
| Pre-built K8s deployment templates (Jinja2) | Task 3 |
| Parallel execution backend+frontend+docker+k8s | Task 6 (`run_build_parallel`) |
| Model selection (Haiku/Sonnet/Opus) | Task 8 |
| Prompt caching | Already implemented in previous session; preserved |
| No Batch API | Not included — confirmed skip |
