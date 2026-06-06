# Nexus

Nexus is an autonomous full-stack application builder. Give it a plain-English description of the app you want — it plans, scaffolds, deploys, and monitors a complete production application on AWS EKS, without manual steps.

**Stack:** React + TypeScript (frontend) · FastAPI + PostgreSQL (backend) · AWS EKS (deployment) · Telegram (alerting)

---

## How it works

A parent Claude agent (`claude-opus-4-8`) owns a `BuildState` and drives specialized subagents through seven sequential phases:

```
PLANNING → API_SPEC → BUILD → INFRA → TEST → MONITORING
```

**API_SPEC** and **BUILD** are automatic — no LLM call, no API cost:
- **API_SPEC**: generates an OpenAPI 3.0 YAML from the AppSpec deterministically
- **BUILD**: runs the backend and frontend subagents **in parallel** (two subprocesses simultaneously), then pre-renders Dockerfiles and K8s manifests from Jinja2 templates before any cloud calls

Each phase uses only the tools it needs. Tool namespaces are enforced per-phase — the parent can't accidentally call a Kubernetes tool during planning.

```
70 tools across 8 namespaces
├── plan.*      — analyze spec, estimate cost, generate OpenAPI spec, render summary
├── code.*      — file I/O + scaffold FastAPI/React/K8s from Jinja2 templates
├── docker.*    — build, tag, push images to ECR
├── aws.*       — ECR, EKS, RDS, S3, CloudFront, CloudWatch, IAM
├── k8s.*       — apply manifests, secrets, rollouts, ingress, migrations
├── test.*      — pytest, vitest, health checks, k8s manifest validation
├── alert.*     — Telegram bot, alert rules, log parsing, silencing
└── subagent.*  — spawn Planner, BackendBuilder, FrontendBuilder, Infra, Alerting
```

**Model selection by role:**
| Agent | Model |
|-------|-------|
| Orchestrator | `claude-opus-4-8` |
| BackendBuilder, FrontendBuilder, Infra | `claude-sonnet-4-6` |
| Planner, Alerting | `claude-haiku-4-5-20251001` |

**Real cost tracking:** every API call's `response.usage` is captured (input, output, cache reads) and priced using actual Anthropic rates. Total cost is shown on exit.

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

Expected: **96 tests passing**.

---

## CLI usage

### `nexus build` — build and deploy an app

```bash
# Show cost estimate only (no AWS calls, no build)
nexus build "Build a SaaS app with user login and a billing dashboard" --dry-run

# Full build and deploy to AWS (session files go to /tmp/nexus-workflow/<session-id>/)
nexus build "Build a SaaS app with user login and a billing dashboard" \
  --region us-east-1

# Use a custom root directory for session files
nexus build "Build a SaaS app..." \
  --workflow-dir ~/my-builds

# With Telegram alerts
nexus build "Build a task manager with projects, tasks, and team members" \
  --telegram-token "$TELEGRAM_BOT_TOKEN" \
  --telegram-chat "$TELEGRAM_CHAT_ID"

# Resume the last interrupted build
nexus build "Build a SaaS app..." --resume

# Resume a specific session by ID (shown in logs as "session abc12345")
nexus build "Build a SaaS app..." --resume abc12345
```

**Log verbosity** (default: `normal`):

```bash
nexus build "..." --log-level verbose   # every tool input/output
nexus build "..." --log-level normal    # phase transitions + tool results (default)
nexus build "..." --log-level bugs      # warnings and errors only
nexus build "..." --log-level silent    # quiet
```

On exit (success or interrupt), the CLI prints a cost breakdown:

```
Cost breakdown:
  Total LLM cost: $6.2341
  Input tokens:   1,234,567
  Output tokens:  89,012
  Cache reads:    987,654
  API calls:      47
  By model:
    claude-haiku-4-5-20251001: $0.0234 (8 calls)
    claude-opus-4-8:           $4.1200 (12 calls)
    claude-sonnet-4-6:         $2.0907 (27 calls)
```

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

## Session layout

Every build gets its own directory under the workflow root:

```
/tmp/nexus-workflow/
├── abc12345/               ← session ID (printed at start of every run)
│   ├── checkpoint.json     ← full session state, saved after every phase
│   ├── api/
│   │   └── openapi.yaml    ← generated API spec
│   ├── backend/            ← generated FastAPI app
│   ├── frontend/           ← generated React app
│   └── k8s/               ← rendered K8s manifests
└── def67890/
    └── ...
```

On `--resume`, Nexus:
1. Loads `checkpoint.json` from the specified (or latest) session directory
2. Scans the session directory to recover any manifests that were built but not checkpointed (e.g. if the process was killed mid-phase)
3. Corrects the current phase based on what's actually on disk
4. Continues from where it left off without redoing completed work

`checkpoint.json` is also written automatically on `SIGTERM` or any unexpected exit.

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

Run `--dry-run` first to see what a build will cost before committing:

```bash
nexus build "Your app description here" --dry-run
```

Output:
```
╔══════════════════════════════════════╗
║         NEXUS BUILD ESTIMATE         ║
╠══════════════════════════════════════╣
║  AWS cost:    $111.00/month          ║
║  LLM cost:    ~$22.14 (estimate)     ║
║  Steps:        30                    ║
║  Tokens:       5,916,000             ║
╚══════════════════════════════════════╝
```

Note: the estimate is conservative (1.5× safety factor, 60% cache hit rate assumed). Actual cost is shown at the end of every real run using live token counts.

---

## Project structure

```
nexus/
├── agent/
│   ├── core/
│   │   ├── state.py          # BuildState, Phase, AgentStatus, file registry, cost tracking
│   │   ├── cost.py           # per-model pricing, compute_cost()
│   │   ├── parallel.py       # parallel subprocess runner + Jinja2 template renderer
│   │   ├── orchestrator.py   # parent agent loop, phase-gated tool scoping
│   │   ├── context.py        # phase compression + message summarisation
│   │   ├── errors.py         # NexusError hierarchy
│   │   ├── retry.py          # exponential backoff + token-bucket rate limiting
│   │   └── observability.py  # JSON structured logging decorator
│   ├── subagents/
│   │   ├── base.py           # BaseSubagent — tool-scoped loop + real cost accumulation
│   │   ├── planner.py        # Haiku
│   │   ├── backend_builder.py # Sonnet
│   │   ├── frontend_builder.py # Sonnet
│   │   ├── infra.py          # Sonnet
│   │   └── alerting.py       # Haiku — persistent polling
│   └── tools/
│       ├── registry.py       # ToolRegistry singleton (namespace__tool API naming)
│       ├── plan/tools.py     # analyze_spec, estimate_*, generate_api_spec, render_*
│       ├── code/tools.py
│       ├── docker/tools.py
│       ├── aws/tools.py
│       ├── k8s/tools.py
│       ├── test/tools.py
│       ├── alert/tools.py
│       └── subagent/tools.py # status callbacks wired to BuildState
├── templates/
│   ├── fastapi/              # main.py, model, route, auth, admin, Dockerfile
│   ├── react/                # App.tsx, AuthContext, api.ts, Login, AdminDashboard, Dockerfile
│   └── k8s/                  # deployment, service, ingress, migration-job
├── eval/
│   ├── harness.py            # Check.* assertions + run_eval()
│   └── cases/basic_saas.py
├── tests/
│   ├── unit/                 # 92 tests (no external deps)
│   └── integration/          # 4 tests (moto for mock AWS)
├── cli.py
├── MEMO.md                   # design decisions and trade-offs
└── pyproject.toml
```

---

## Design notes

See [MEMO.md](MEMO.md) for the full design rationale, what was cut, and one defended design decision (fixed stack vs. dynamic stack selection).
