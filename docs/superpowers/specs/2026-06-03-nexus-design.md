# Nexus — Design Spec
**Date:** 2026-06-03
**Author:** Rohit Aggarwal

---

## 1. What Nexus Is

Nexus is an autonomous full-stack app builder and deployer. The user provides a natural language description of an app; Nexus plans, builds, deploys, and monitors it end-to-end without further input.

**Input:**
```
"Build me a SaaS app with user login, an alerting dashboard, and an API key manager"
```

**Output sequence:**
1. **Pre-flight cost summary** — estimated steps, LLM token cost, monthly AWS cost. Shown immediately. Full step-by-step plan available on request.
2. **Deployed application** — auth, requested features, APIs, PostgreSQL database, all running on Kubernetes on AWS EKS.
3. **Admin monitoring dashboard** (always included) — CloudWatch metrics, AWS cost breakdown, memory/CPU per pod, error rates. Accessible at `/admin`.
4. **Telegram alerting** — a persistent agent monitors logs post-deployment and sends alerts to a configured Telegram channel when error thresholds are breached.
5. **Test report** — integration and e2e results shown after deployment.

---

## 2. Fixed Tech Stack

The stack is fixed. "General" means the features and data models vary by user input — the underlying technology never changes.

| Layer | Technology |
|---|---|
| Frontend | React + TypeScript + shadcn/ui |
| Backend | FastAPI (Python) |
| Database | PostgreSQL (RDS, accessed via K8s secret) |
| Auth | JWT (backend-issued) + protected React routes |
| Container registry | AWS ECR |
| Cluster | AWS EKS (created via AWS CLI/CDK) |
| Deployment | Kubernetes manifests + Helm |
| CDN / Static assets | CloudFront + S3 |
| Infrastructure-as-code | AWS CDK (Python), used only for EKS + ECR |
| Monitoring | CloudWatch + K8s metrics-server |
| Cost visibility | AWS Cost Explorer |
| Alerting channel | Telegram Bot API |

---

## 3. Tool Namespaces — 64 tools across 7 namespaces

### `plan.*` (6 tools)
Pre-flight planning. Runs before any code is written.

| Tool | Purpose |
|---|---|
| `plan.analyze_spec` | Parse user description → extract features, data models, API routes, pages |
| `plan.estimate_steps` | Enumerate all agent steps for this build |
| `plan.estimate_tokens` | Estimate LLM token usage per step → total cost in USD |
| `plan.estimate_aws_cost` | Estimate monthly AWS cost (EKS, RDS, ECR, CloudFront, S3) |
| `plan.render_summary` | Produce the cost summary card shown to user |
| `plan.render_full_plan` | Produce the detailed step-by-step plan (shown only if user requests) |

### `code.*` (16 tools)
Code generation and file operations. Used by BackendBuilderSubagent and FrontendBuilderSubagent.

| Tool | Purpose |
|---|---|
| `code.read_file` | Read a file from the workspace |
| `code.write_file` | Write a file to the workspace |
| `code.list_dir` | List directory contents |
| `code.delete_file` | Delete a file |
| `code.search_code` | Search for a pattern across files |
| `code.scaffold_fastapi_project` | Bootstrap FastAPI project structure |
| `code.scaffold_react_project` | Bootstrap React + TypeScript project |
| `code.scaffold_api_route` | Generate a FastAPI route from an AppSpec route definition |
| `code.scaffold_react_page` | Generate a React page or component |
| `code.scaffold_db_model` | Generate SQLAlchemy model from schema definition |
| `code.scaffold_migration` | Generate Alembic migration file |
| `code.scaffold_k8s_manifest` | Generate Kubernetes YAML (Deployment, Service, Ingress, HPA) |
| `code.scaffold_helm_chart` | Generate Helm chart for a component |
| `code.apply_patch` | Apply a targeted patch to an existing file |
| `code.run_linter` | Run ruff (Python) or eslint (TypeScript) |
| `code.run_formatter` | Run black (Python) or prettier (TypeScript) |

### `docker.*` (5 tools)
Container build and push operations.

| Tool | Purpose |
|---|---|
| `docker.build_image` | Build Docker image from a Dockerfile |
| `docker.tag_image` | Tag image for ECR push |
| `docker.push_to_ecr` | Push image to AWS ECR |
| `docker.run_local` | Run container locally for smoke test |
| `docker.inspect_image` | Inspect image layers and size |

### `aws.*` (10 tools)
AWS infrastructure. Scoped to EKS cluster creation, ECR, RDS, S3, CloudFront, and monitoring. Kubernetes handles the rest.

| Tool | Purpose |
|---|---|
| `aws.create_ecr_repo` | Create ECR repository for an image |
| `aws.create_eks_cluster` | Provision EKS cluster via AWS CLI |
| `aws.get_eks_kubeconfig` | Fetch kubeconfig for the cluster |
| `aws.create_rds_instance` | Provision PostgreSQL RDS instance |
| `aws.get_rds_endpoint` | Get RDS connection string |
| `aws.create_s3_bucket` | Create S3 bucket for static assets |
| `aws.create_cloudfront_dist` | Create CloudFront distribution pointing to S3 |
| `aws.get_cost_estimate` | Query Cost Explorer for cost breakdown by service |
| `aws.get_cloudwatch_metrics` | Pull CloudWatch metrics (CPU, memory, error count) |
| `aws.create_iam_role` | Create IAM role for EKS service account (IRSA) |

### `k8s.*` (13 tools)
All Kubernetes operations post-cluster creation. Used by InfraSubagent.

| Tool | Purpose |
|---|---|
| `k8s.apply_manifest` | `kubectl apply` a manifest file |
| `k8s.delete_manifest` | `kubectl delete` a manifest |
| `k8s.create_namespace` | Create a K8s namespace |
| `k8s.create_secret` | Create a K8s secret (DB credentials, JWT signing key) |
| `k8s.create_configmap` | Create a K8s ConfigMap |
| `k8s.deploy_helm_chart` | `helm install` or `helm upgrade` a chart |
| `k8s.get_pod_status` | Check pod health and readiness |
| `k8s.get_pod_logs` | Fetch logs from a pod |
| `k8s.wait_for_rollout` | Block until a deployment rollout is complete |
| `k8s.scale_deployment` | Scale replicas up or down |
| `k8s.get_ingress_address` | Get external IP/hostname from ingress |
| `k8s.run_migration_job` | Run a K8s Job to execute Alembic migrations |
| `k8s.get_resource_usage` | Query metrics-server for CPU/memory per pod |

### `test.*` (7 tools)
Test execution and validation.

| Tool | Purpose |
|---|---|
| `test.run_unit_tests` | Run pytest (backend) or vitest (frontend) |
| `test.run_integration_tests` | Hit live API endpoints, validate responses |
| `test.run_e2e_tests` | Playwright smoke test on deployed frontend URL |
| `test.check_coverage` | Generate coverage report |
| `test.run_lint_check` | Full lint pass across generated codebase |
| `test.validate_k8s_manifests` | Run kubeval / kubectl dry-run on all manifests |
| `test.health_check_endpoints` | HTTP health checks on all deployed services |

### `alert.*` (7 tools)
Telegram alerting. Used exclusively by the AlertingSubagent.

| Tool | Purpose |
|---|---|
| `alert.setup_telegram_bot` | Configure Telegram bot token and target chat ID |
| `alert.send_telegram_message` | Send a formatted alert to the Telegram channel |
| `alert.create_alert_rule` | Define a rule: metric + threshold + time window + severity |
| `alert.list_alert_rules` | List all active rules for this deployment |
| `alert.query_recent_logs` | Pull recent CloudWatch log entries |
| `alert.parse_log_for_errors` | Extract error patterns, status codes, stack traces from logs |
| `alert.silence_alert` | Silence a rule for a configurable duration (spam prevention) |

---

## 4. Subagent Architecture

Five subagents. Four are task subagents (run once, return structured output). One is persistent.

### SubAgent 1: `PlannerSubagent`
- **Triggered by:** Parent on user input
- **Scoped tools:** `plan.*` only
- **Input:** `{ "user_description": str }`
- **Output:** `AppSpec` + `CostSummary` + `FullPlan[]`
- **Note:** Output consumed by all subsequent subagents — primary composability chain anchor

### SubAgent 2: `BackendBuilderSubagent`
- **Triggered by:** Parent after PlannerSubagent returns
- **Scoped tools:** `code.*`, `test.run_unit_tests`, `test.run_lint_check`
- **Input:** `AppSpec`
- **Output:** `BackendManifest` — file list, API routes, required env vars, Dockerfile path, unit test results

### SubAgent 3: `FrontendBuilderSubagent`
- **Triggered by:** Parent after BackendBuilderSubagent returns
- **Scoped tools:** `code.*`, `test.run_unit_tests`, `test.run_lint_check`
- **Input:** `AppSpec` + `BackendManifest.api_routes`
- **Output:** `FrontendManifest` — file list, Dockerfile path, build command, unit test results
- **Note:** Always includes the admin monitoring dashboard regardless of user spec

### SubAgent 4: `InfraSubagent`
- **Triggered by:** Parent after both images are pushed to ECR
- **Scoped tools:** `aws.*`, `k8s.*`, `docker.*`
- **Input:** `AppSpec` + ECR image URIs + `env_vars_required`
- **Output:** `DeploymentResult` — frontend URL, backend URL, RDS endpoint, resource ARNs

### SubAgent 5: `AlertingSubagent` *(persistent)*
- **Triggered by:** Parent after InfraSubagent returns and Telegram is configured
- **Scoped tools:** `alert.*`, `aws.get_cloudwatch_metrics`
- **Input:** `DeploymentResult.cluster_name` + alert rules + Telegram bot config
- **Behaviour:** Polls CloudWatch logs every 60s, evaluates rules, sends Telegram messages on breach, silences repeat alerts
- **Output (on escalation):** `AlertEvent` — rule fired, message sent, timestamp

---

## 5. Context Management Strategy

### The problem
~30 tool calls per build. Each returns output that accumulates in context. Without management: context overflow and subagents inheriting irrelevant parent history.

### Solution: `BuildState` with phase checkpointing

```python
@dataclass
class BuildState:
    session_id: str
    user_description: str
    current_phase: Phase  # PLANNING | BACKEND | FRONTEND | INFRA | TEST | MONITORING

    app_spec: AppSpec | None = None
    cost_summary: CostSummary | None = None
    backend_manifest: BackendManifest | None = None
    frontend_manifest: FrontendManifest | None = None
    deployment_result: DeploymentResult | None = None
    test_report: TestReport | None = None

    errors: list[NexusError] = field(default_factory=list)
    tool_call_count: int = 0
    checkpointed_at: datetime | None = None
```

**Progressive compression:** After each phase, raw tool outputs are replaced by compact typed manifests. The model sees `BackendManifest` (8 fields), not 500 lines of generated code.

**Phase checkpointing:** `BuildState` is serialized to disk after every phase completion. Failed runs resume from last checkpoint rather than restarting.

**Scoped subagent context:** Each subagent receives only the `BuildState` slice it needs. `FrontendBuilderSubagent` gets `AppSpec` + `api_routes`. It never sees AWS credentials, cost data, or K8s state.

---

## 6. Long-Horizon Execution — 30-Step Trace

| Step | Tool | Output |
|---|---|---|
| 1 | `plan.analyze_spec` | raw feature list |
| 2 | `plan.estimate_steps` | step count |
| 3 | `plan.estimate_tokens` | token + LLM cost |
| 4 | `plan.estimate_aws_cost` | AWS monthly cost |
| 5 | `plan.render_summary` | **CostSummary** → shown to user |
| 6 | `code.scaffold_fastapi_project` | project skeleton |
| 7 | `code.scaffold_db_model` ×N | SQLAlchemy models |
| 8 | `code.scaffold_migration` | Alembic migration |
| 9 | `code.scaffold_api_route` ×N | FastAPI routes |
| 10 | `code.run_linter` | lint results |
| 11 | `test.run_unit_tests` | **BackendManifest** |
| 12 | `code.scaffold_react_project` | React skeleton |
| 13 | `code.scaffold_react_page` ×N | pages + admin dashboard |
| 14 | `code.run_linter` | lint results |
| 15 | `test.run_unit_tests` | **FrontendManifest** |
| 16 | `docker.build_image` (backend) | image ID |
| 17 | `docker.push_to_ecr` (backend) | ECR URI |
| 18 | `docker.build_image` (frontend) | image ID |
| 19 | `docker.push_to_ecr` (frontend) | ECR URI |
| 20 | `aws.create_eks_cluster` | cluster ARN |
| 21 | `aws.get_eks_kubeconfig` | kubeconfig |
| 22 | `k8s.create_namespace` | namespace |
| 23 | `k8s.create_secret` | DB + JWT secrets |
| 24 | `k8s.apply_manifest` (backend) | backend deployment |
| 25 | `k8s.apply_manifest` (frontend) | frontend deployment |
| 26 | `k8s.run_migration_job` | migration result |
| 27 | `k8s.wait_for_rollout` | ready status |
| 28 | `k8s.get_ingress_address` | **DeploymentResult** |
| 29 | `test.run_integration_tests` | API test results |
| 30 | `test.run_e2e_tests` | **TestReport** → shown to user |

---

## 7. Composability Chain

Satisfies requirement 5 (tools composing into chains):

```
plan.analyze_spec → AppSpec
  → code.scaffold_fastapi_project(AppSpec) → BackendManifest
    → docker.build_image(BackendManifest) → ImageID
      → docker.push_to_ecr(ImageID) → ECRImageURI
        → code.scaffold_k8s_manifest(ECRImageURI) → K8sManifest
          → k8s.apply_manifest(K8sManifest) → DeployedService
            → test.run_integration_tests(DeployedService.endpoint) → TestResults
```

---

## 8. Production Scaffolding

### Observability
Every tool call wrapped in `@instrument` decorator. Emits structured JSON to stdout:
```json
{ "session_id": "...", "phase": "INFRA", "tool": "k8s.apply_manifest",
  "duration_ms": 340, "status": "ok", "error": null }
```

### Retries with exponential backoff
```python
@retry(
    max_attempts=4,
    base_delay_seconds=1.0,
    max_delay_seconds=30.0,
    backoff_factor=2.0,
    retryable_on=[RateLimitError, TransientAwsError, NetworkError]
)
```
Applied to all AWS, K8s, Docker, and Telegram calls.

### Rate limiting
Token-bucket per namespace, declared in code:
```python
RATE_LIMITS = {
    "aws":    RateLimit(calls_per_second=5,  burst=10),
    "k8s":    RateLimit(calls_per_second=20, burst=50),
    "alert":  RateLimit(calls_per_second=1,  burst=3),
    "docker": RateLimit(calls_per_second=2,  burst=5),
}
```

### Typed error handling
```python
class NexusError(Exception): ...
class PlanningError(NexusError): ...
class BuildError(NexusError):
    phase: Literal["backend", "frontend"]
    files_created: list[str]
class DeploymentError(NexusError):
    last_successful_step: str
    cluster_name: str | None
class TestFailure(NexusError):
    report: TestReport
class AlertingError(NexusError): ...
```

---

## 9. Evaluation Harness

```python
EVAL_CASE = EvalCase(
    description="Build a SaaS app with login and dashboard",
    checks=[
        Check.http_200("frontend_url"),
        Check.http_200("backend_url/health"),
        Check.auth_flow_works("backend_url"),
        Check.k8s_pods_healthy("cluster_name"),
        Check.telegram_alert_fires(inject_error=True),
        Check.cost_summary_present(),
        Check.tool_call_count_gte(20),
    ]
)
```

**Mock mode** (local, free): AWS calls intercepted via `moto`. Runs in CI.
**Real mode** (AWS): Spins up live EKS cluster, verifies URLs, tears down. Run before submission.

### Test suite

| Layer | Framework | Coverage |
|---|---|---|
| Unit | pytest | Each tool function in isolation, mocked externals |
| Integration | pytest + moto | Full phase runs: planning, build, deploy |
| E2E eval | pytest + real AWS | Full build against known spec |
| K8s manifests | kubeval | All generated manifests validated before apply |
| Frontend | vitest | Generated React components render correctly |

---

## 10. Project Structure

```
nexus/
├── agent/
│   ├── core/
│   │   ├── state.py          # BuildState, Phase enum, typed manifests
│   │   ├── orchestrator.py   # parent agent loop
│   │   ├── context.py        # compression, checkpointing
│   │   └── retry.py          # retry + rate limit decorators
│   ├── subagents/
│   │   ├── planner.py
│   │   ├── backend_builder.py
│   │   ├── frontend_builder.py
│   │   ├── infra.py
│   │   └── alerting.py
│   └── tools/
│       ├── plan/
│       ├── code/
│       ├── docker/
│       ├── aws/
│       ├── k8s/
│       ├── test/
│       └── alert/
├── eval/
│   ├── harness.py
│   └── cases/
│       └── basic_saas.py
├── tests/
│   ├── unit/
│   └── integration/
├── templates/                # Jinja2 templates for FastAPI + React scaffolding
├── MEMO.md
└── pyproject.toml
```

---

## 11. What Is Cut (5-day scope)

- **Multi-cloud:** AWS only. GCP/Azure support is a config change, not a design change.
- **Dynamic stack selection:** Stack is fixed. The agent does not choose between frameworks.
- **Real-time streaming:** Build progress is polled, not streamed via websocket.
- **Multi-region deployment:** Single AWS region only.
- **Slack / other alert channels:** Telegram only. Channel abstraction is in the design; second channel is a 2-hour addition.

---

## 12. One Design Decision Worth Defending

**Fixed stack vs. dynamic stack selection.**

An engineer might reasonably argue that a truly general app builder should let the LLM choose the tech stack. This would make the agent more flexible.

The counter-argument: stack selection errors cascade. If the LLM picks a framework the scaffolding templates don't fully support, every subsequent tool call produces broken output. Debugging this in a 5-day window while also hitting the 50-tool, subagent, and scaffolding requirements is a losing bet. A fixed opinionated stack lets all complexity budget go toward depth — better subagent isolation, better eval harness, better observability — rather than breadth that adds risk without adding evaluator signal.
