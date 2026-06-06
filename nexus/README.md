# Nexus

Nexus is an autonomous full-stack application builder. Give it a plain-English description of the app you want — it plans, scaffolds, deploys, and monitors a complete production application on AWS EKS, without manual steps.

**Stack:** React + TypeScript (frontend) · FastAPI + PostgreSQL (backend) · AWS EKS (deployment) · Telegram (alerting)

---

## How it works

A parent Claude agent (`claude-opus-4-8`) owns a `BuildState` and drives five specialized subagents through seven sequential phases:

```
PLANNING → API_SPEC → BUILD → INFRA → TEST → MONITORING → COMPLETE
```

`API_SPEC` and `BUILD` are deterministic (no LLM call): the orchestrator generates the OpenAPI spec directly from the parsed `AppSpec`, then runs backend and frontend scaffolding in parallel via `ProcessPoolExecutor`. Every other phase runs its own subagent. Tool namespaces are enforced per-phase — the parent can't call a Kubernetes tool during planning, or a scaffolding tool during deployment.

```
69 tools across 8 namespaces
├── plan.*      — analyze spec, generate OpenAPI spec, estimate cost, render summary
├── code.*      — file I/O + scaffold FastAPI/React/K8s from Jinja2 templates
├── docker.*    — build, tag, push images to ECR
├── aws.*       — ECR, EKS, RDS, S3, CloudFront, CloudWatch, IAM
├── k8s.*       — apply manifests, secrets, rollouts, ingress, migrations
├── test.*      — pytest, vitest, health checks, k8s manifest validation
├── alert.*     — Telegram bot, alert rules, log parsing, silencing
└── subagent.*  — spawn Planner, BackendBuilder, FrontendBuilder, Infra, Alerting
```

### Model selection

| Role | Model | Reason |
|---|---|---|
| Orchestrator | `claude-opus-4-8` | Drives all phases, highest reasoning |
| BackendBuilder, FrontendBuilder, Infra | `claude-sonnet-4-6` | Code generation, balanced speed/quality |
| Planner, Alerting | `claude-haiku-4-5-20251001` | Structured extraction, fast + cheap |

---

## Setup

**Requirements:** Python 3.11+, an Anthropic API key.

```bash
# Clone and enter the project
git clone <repo-url>
cd nexus

# Create and activate venv (requires Python 3.11+)
python3.13 -m venv .venv          # or python3.11 / python3.12
source .venv/bin/activate

# Install dependencies (also registers the `nexus` CLI command)
pip install -e ".[dev]"

# Verify the CLI is available
nexus --help

# Set your Anthropic API key
export ANTHROPIC_API_KEY="sk-ant-..."
```

The `pip install -e .` step registers `nexus` as a shell command via the `[project.scripts]` entry point in `pyproject.toml`. After that, `nexus build` and `nexus eval-cmd` work from any directory as long as the venv is active.

If you want only the runtime (no test/dev tools):

```bash
pip install -e .
```

For real deployments you also need:
- AWS credentials configured (`aws configure` or environment variables)
- `eksctl` installed for EKS cluster creation
- `kubectl` installed for Kubernetes operations
- Docker daemon running for image builds

### AWS MCP (optional)

A project-level `.mcp.json` at the repo root configures the [AWS MCP server](https://aws-mcp.us-east-1.api.aws/mcp) for Claude Code. This gives you direct AWS tool access in Claude Code sessions without leaving the editor — useful for inspecting resources, debugging deployments, and running ad-hoc AWS operations while developing Nexus.

```json
// .mcp.json (already present at repo root)
{
  "mcpServers": {
    "aws": {
      "type": "http",
      "url": "https://aws-mcp.us-east-1.api.aws/mcp"
    }
  }
}
```

The MCP server reads credentials from the same sources as the AWS CLI: `~/.aws/credentials`, `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_SESSION_TOKEN` environment variables, or an IAM instance profile. No extra configuration is needed if `aws configure` is already set up.

---

## Running tests

```bash
# Unit tests (no AWS, no Docker required)
pytest tests/unit/ -v

# Integration tests (no AWS, uses moto for mock AWS calls)
pytest tests/integration/ -v

# All tests
pytest -v
```

Expected: **100 tests passing** (96 unit, 4 integration).

---

## CLI usage

### `nexus build` — build and deploy an app

```bash
# Show cost estimate only (no AWS calls, no build)
nexus build "Build a SaaS app with user login and a billing dashboard" --dry-run

# Full build and deploy to AWS
nexus build "Build a SaaS app with user login and a billing dashboard" \
  --workflow-dir /tmp/nexus-workflow \
  --region us-east-1

# With Telegram alerts
nexus build "Build a task manager with projects, tasks, and team members" \
  --telegram-token "$TELEGRAM_BOT_TOKEN" \
  --telegram-chat "$TELEGRAM_CHAT_ID"

# Resume the most recent interrupted session
nexus build "Build a SaaS app..." --resume

# Resume a specific session by ID
nexus build "Build a SaaS app..." --resume f33bd627
```

Each session gets its own directory under `--workflow-dir`:

```
/tmp/nexus-workflow/
└── f33bd627/                  ← session ID (printed when build starts)
    ├── checkpoint.json        ← full BuildState, updated after every phase
    ├── backend/               ← generated FastAPI app
    ├── frontend/              ← generated React app
    └── k8s/                   ← rendered Kubernetes manifests
```

### Local testing with docker-compose

Nexus generates a `docker-compose.yml` during the BUILD phase (before any AWS calls). Use it to smoke-test the full stack locally before deploying to EKS:

```bash
# After nexus build completes or is interrupted mid-INFRA:
cd /tmp/nexus-workflow/<session-id>/

# Build and start all three services
docker compose up --build

# Services:
#   frontend  → http://localhost:3000
#   backend   → http://localhost:8000
#   db        → localhost:5432  (user: nexus  pass: nexuspassword  db: nexusdb)

# Health check
curl http://localhost:8000/health

# Tear down
docker compose down -v    # -v removes the postgres volume too
```

The compose file wires:
- `db` (postgres:15-alpine) with a healthcheck — backend waits for it
- `backend` (FastAPI) depends on `db:service_healthy`, exposes port 8000
- `frontend` (Nginx serving built React) depends on `backend:service_healthy`, exposes port 3000

The React app's API base URL defaults to `http://localhost:8000`, so no extra config is needed for local testing.

---

### `nexus eval-cmd` — run the eval harness in mock mode

```bash
# Runs Check.* assertions against a synthetic BuildState (no AWS spend)
nexus eval-cmd "Build a SaaS app with login and dashboard"
```

### Direct Python invocation

```bash
# From inside the nexus/ directory
python cli.py build "Build an app" --dry-run
python cli.py --help
```

---

## How to write prompts

Nexus extracts features by keyword matching, then uses those features to drive scaffolding. Be explicit about what the app needs.

### Supported keywords

| Keyword in your prompt | What gets built |
|------------------------|-----------------|
| `login`, `auth`, `sign in`, `register` | Auth routes, JWT, User model, Login/Register pages |
| `dashboard` | Dashboard page (always includes AdminDashboard) |
| `alert`, `alerting` | Alert model, `/alerts` route, Alerts page |
| `api key`, `api keys` | ApiKey model, `/keys` route, ApiKeys page |

Everything else → a generic `Item` model with CRUD routes.

### Good prompt examples

```
Build a SaaS app with user login, an alerting dashboard, and an API key manager
```
→ Builds: auth + dashboard + alerting + api_keys features, 4 models, full CRUD routes

```
Build a project management tool with user login and a dashboard
```
→ Builds: auth + dashboard features

```
Build a monitoring platform with alerting and an API key manager
```
→ Builds: alerting + api_keys features (no auth — add "with user login" if you want it)

### Tips

- **Be specific about features** — "with user login" is better than "users can log in"
- **Always mention login** if you want authentication — it's not assumed by default
- **The admin dashboard is always included** regardless of your prompt, at `/admin`
- **The stack is fixed** — React + FastAPI + PostgreSQL + EKS. You can't change the framework via the prompt, but you can describe any domain, data models, or features you want

### Cost estimation before building

Run `--dry-run` first to see exactly what a build will cost before committing:

```bash
nexus build "Your app description here" --dry-run
```

Output:
```
╔══════════════════════════════════════╗
║         NEXUS BUILD ESTIMATE         ║
╠══════════════════════════════════════╣
║  AWS cost:    $111.00/month          ║
║  LLM cost:    $0.5400 (this run)     ║
║  Steps:        30                    ║
║  Tokens:       180,000               ║
╚══════════════════════════════════════╝
```

### Real cost tracking

After a real build, Nexus prints an itemized LLM cost breakdown using actual token counts from the Anthropic API (including prompt cache hits):

```
Cost breakdown:
  Total LLM cost: $0.3821
  Input tokens:   48,200
  Output tokens:  12,400
  Cache reads:    210,000
  API calls:      34
  By model:
    claude-haiku-4-5-20251001: $0.0142 (8 calls)
    claude-opus-4-8:           $0.2109 (14 calls)
    claude-sonnet-4-6:          $0.1570 (12 calls)
```

The same data is persisted in `checkpoint.json` under `cost_tracking` so interrupted sessions preserve the running total.

---

## Project structure

```
nexus/
├── agent/
│   ├── core/
│   │   ├── state.py          # BuildState + phase/agent enums + manifest dataclasses
│   │   ├── errors.py         # NexusError hierarchy
│   │   ├── cost.py           # per-model token pricing + compute_cost()
│   │   ├── parallel.py       # ProcessPoolExecutor BUILD phase + Jinja2 pre-rendering
│   │   ├── retry.py          # exponential backoff + token-bucket rate limiting
│   │   ├── observability.py  # JSON structured logging decorator
│   │   ├── context.py        # phase compression + message summarisation
│   │   └── orchestrator.py   # parent agent loop
│   ├── subagents/
│   │   ├── base.py           # BaseSubagent (tool-scoped Anthropic API loop + cost tracking)
│   │   ├── planner.py        # haiku
│   │   ├── backend_builder.py # sonnet
│   │   ├── frontend_builder.py # sonnet
│   │   ├── infra.py          # sonnet
│   │   └── alerting.py       # haiku — persistent polling subagent
│   └── tools/
│       ├── registry.py       # ToolRegistry singleton
│       ├── plan/tools.py     # analyze_spec, generate_api_spec, estimate_*, render_summary
│       ├── code/tools.py
│       ├── docker/tools.py   # _docker() helper, retry=3, realistic timeouts
│       ├── aws/tools.py      # retry=3, idempotency on all create_* tools
│       ├── k8s/tools.py      # _kube() helper, retry=3, realistic timeouts
│       ├── test/tools.py
│       ├── alert/tools.py
│       └── subagent/tools.py # status callbacks → checkpoint on every status change
├── templates/
│   ├── fastapi/              # main.py, model, route, auth, admin, Dockerfile
│   ├── react/                # App.tsx, AuthContext, api.ts, Login, AdminDashboard, Dockerfile
│   └── k8s/                  # deployment, service, ingress, migration-job
├── eval/
│   ├── harness.py            # Check.* assertions + run_eval()
│   └── cases/basic_saas.py   # reference eval case
├── tests/
│   ├── unit/                 # 96 tests (no external deps)
│   └── integration/          # 4 tests (moto for mock AWS)
├── cli.py
├── MEMO.md                   # design decisions and trade-offs
└── pyproject.toml
```

---

## Design notes

See [MEMO.md](MEMO.md) for the full design rationale, what was cut, and one defended design decision (fixed stack vs. dynamic stack selection).
