# Nexus

Nexus is an autonomous full-stack application builder. Give it a plain-English description of the app you want — it plans, scaffolds, deploys, and monitors a complete production application on AWS EKS, without manual steps.

**Stack:** React + TypeScript (frontend) · FastAPI + PostgreSQL (backend) · AWS EKS (deployment) · Telegram (alerting)

---

## How it works

A parent Claude agent (`claude-opus-4-8`) owns a `BuildState` and drives five specialized subagents through six sequential phases:

```
PLANNING → BACKEND → FRONTEND → INFRA → TEST → MONITORING
```

Each phase uses only the tools it needs. The parent can't accidentally call a Kubernetes tool during planning, or a scaffolding tool during deployment. Tool namespaces are enforced per-phase.

```
69 tools across 8 namespaces
├── plan.*      — analyze spec, estimate cost, render summary
├── code.*      — file I/O + scaffold FastAPI/React/K8s from Jinja2 templates
├── docker.*    — build, tag, push images to ECR
├── aws.*       — ECR, EKS, RDS, S3, CloudFront, CloudWatch, IAM
├── k8s.*       — apply manifests, secrets, rollouts, ingress, migrations
├── test.*      — pytest, vitest, health checks, k8s manifest validation
├── alert.*     — Telegram bot, alert rules, log parsing, silencing
└── subagent.*  — spawn Planner, BackendBuilder, FrontendBuilder, Infra, Alerting
```

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

Expected: **75 tests passing**.

---

## CLI usage

### `nexus build` — build and deploy an app

```bash
# Show cost estimate only (no AWS calls, no build)
nexus build "Build a SaaS app with user login and a billing dashboard" --dry-run

# Full build and deploy to AWS
nexus build "Build a SaaS app with user login and a billing dashboard" \
  --workspace /tmp/my-app \
  --region us-east-1

# With Telegram alerts
nexus build "Build a task manager with projects, tasks, and team members" \
  --telegram-token "$TELEGRAM_BOT_TOKEN" \
  --telegram-chat "$TELEGRAM_CHAT_ID"

# Resume an interrupted build from the last checkpoint
nexus build "Build a SaaS app..." --resume
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

---

## Project structure

```
nexus/
├── agent/
│   ├── core/
│   │   ├── state.py          # BuildState + phase enums + manifest dataclasses
│   │   ├── errors.py         # NexusError hierarchy
│   │   ├── retry.py          # exponential backoff + token-bucket rate limiting
│   │   ├── observability.py  # JSON structured logging decorator
│   │   ├── context.py        # phase compression + message summarisation
│   │   └── orchestrator.py   # parent agent loop
│   ├── subagents/
│   │   ├── base.py           # BaseSubagent (tool-scoped Anthropic API loop)
│   │   ├── planner.py
│   │   ├── backend_builder.py
│   │   ├── frontend_builder.py
│   │   ├── infra.py
│   │   └── alerting.py       # persistent polling subagent
│   └── tools/
│       ├── registry.py       # ToolRegistry singleton
│       ├── plan/tools.py
│       ├── code/tools.py
│       ├── docker/tools.py
│       ├── aws/tools.py
│       ├── k8s/tools.py
│       ├── test/tools.py
│       ├── alert/tools.py
│       └── subagent/tools.py
├── templates/
│   ├── fastapi/              # main.py, model, route, auth, admin, Dockerfile
│   ├── react/                # App.tsx, AuthContext, api.ts, Login, AdminDashboard, Dockerfile
│   └── k8s/                  # deployment, service, ingress, migration-job
├── eval/
│   ├── harness.py            # Check.* assertions + run_eval()
│   └── cases/basic_saas.py   # reference eval case
├── tests/
│   ├── unit/                 # 71 tests (no external deps)
│   └── integration/          # 4 tests (moto for mock AWS)
├── cli.py
├── MEMO.md                   # design decisions and trade-offs
└── pyproject.toml
```

---

## Design notes

See [MEMO.md](MEMO.md) for the full design rationale, what was cut, and one defended design decision (fixed stack vs. dynamic stack selection).
