# NexusAgents

I am fascinated by Claude and wanted to see what maximum extraction I could do with Claude agents on a $20 subscription and API credits. In a span of 5 days I created a repo that could generate full-scale applications and deploy them on a production-ready Kubernetes cluster.

To run the repo, go to [nexus/README.md](/nexus/README.md) for setup instructions.

---

## How it works

You give Nexus a plain-English description. It plans, scaffolds, builds, deploys, and monitors a complete production application on AWS EKS — no manual steps.

**Stack:** React + TypeScript · FastAPI + PostgreSQL · AWS EKS · Telegram alerting

A parent Claude agent owns a `BuildState` and drives six specialized subagents through seven sequential phases:

```
PLANNING → API_SPEC → BUILD → INFRA → TEST → MONITORING → COMPLETE
```

`API_SPEC` and `BUILD` are fully deterministic (no LLM call): the orchestrator generates the OpenAPI spec directly from the parsed plan, then runs backend and frontend scaffolding in parallel via `ProcessPoolExecutor`. Every other phase runs its own subagent with a locked tool namespace — the parent can't call a Kubernetes tool during planning, or a scaffolding tool during deployment.

---

## Agents

| # | Agent | Model | Role |
|---|---|---|---|
| 1 | **Orchestrator** | `claude-opus-4-8` | Owns the build state machine, drives all phases, makes all architectural decisions |
| 2 | **Planner** | `claude-haiku-4-5` | Extracts `AppSpec` from the description — features, DB models, API routes, pages — and estimates cost |
| 3 | **Backend Builder** | `claude-sonnet-4-6` | Scaffolds FastAPI project: models, routes, auth, migrations, Dockerfile — from Jinja2 templates |
| 4 | **Frontend Builder** | `claude-sonnet-4-6` | Scaffolds React + TypeScript: pages, routing, auth context, API client — runs in parallel with Backend Builder |
| 5 | **Infra Agent** | `claude-sonnet-4-6` | Provisions RDS, S3, EKS, deploys K8s manifests, runs Alembic migrations, wires CloudFront |
| 6 | **Alerting Agent** | `claude-haiku-4-5` | Persistent polling loop — parses live pod logs, evaluates rules, fires Telegram alerts |

Backend and Frontend builders run in parallel subprocesses — they share no state and communicate only through the workspace directory. The OpenAPI spec generated in `API_SPEC` is passed to both as the source of truth for field names and endpoint contracts.

---

## Session state & resumability

1. Every build gets a unique **session ID** (e.g. `6a26ba13`)
2. The session folder at `/tmp/nexus-workflow/<session-id>/` contains:
   - `checkpoint.json` — full `BuildState`: current phase, agent statuses, file registry, AWS resources created, cost totals
   - `api/openapi.yaml` — the generated OpenAPI spec
   - `backend/` and `frontend/` — all scaffolded source files
   - `k8s/` — Kubernetes manifests
   - `docker-compose.yml` — for local testing
3. If the build stops for any reason (crash, timeout, interrupt), it saves state to `checkpoint.json`. Pass `--resume` to pick up exactly where it left off:
   ```bash
   nexus build "..." --resume              # resumes last session
   nexus build "..." --resume <session-id> # resumes a specific session
   ```
4. **Prompt caching** is enabled on all subagent system prompts — cache hits are tracked in `cost_tracking` and reduce repeat-run cost by 60–80%.

---

## Tool namespaces

69 tools across 8 namespaces, enforced per phase:

```
├── plan.*      — analyze_spec, generate_api_spec, estimate_steps/tokens/cost, render_summary
├── code.*      — file I/O, scaffold FastAPI/React/K8s from Jinja2 templates, generate_api_client
├── docker.*    — build, tag, push images to ECR
├── aws.*       — ECR, EKS (via eksctl), RDS, S3, CloudFront, CloudWatch, IAM
├── k8s.*       — apply manifests, secrets, rollouts, ingress, wait_for_nodes, migrations
├── test.*      — pytest, vitest, health checks, K8s manifest validation
├── alert.*     — Telegram bot, alert rules, log parsing, silencing
└── subagent.*  — spawn Planner, BackendBuilder, FrontendBuilder, Infra, Alerting
```

---

## Key engineering decisions

- **Fixed stack (React + FastAPI + PostgreSQL + EKS)** — keeps templates deterministic and avoids the LLM choosing incompatible combinations
- **OpenAPI spec as shared contract** — generated deterministically in `API_SPEC`, passed to both builders so frontend field names cannot diverge from backend
- **Parallel build** — backend and frontend scaffold concurrently in separate `ProcessPoolExecutor` workers, cutting build time roughly in half
- **Idempotent AWS tools** — every `create_*` tool returns the existing resource if it already exists, so retries and resumes don't duplicate infrastructure
- **ExitCode-0 nodegroup bug** — `eksctl` exits 0 even when it creates 0 nodegroups (runs a "fix compatibility" task instead). Nexus re-verifies with `eks.list_nodegroups()` after every eksctl call and raises a retryable error if empty
- **Retryable error hierarchy** — `TransientAwsError` and `NetworkError` trigger exponential backoff; `NexusError(retryable=True)` does the same for K8s waits; all other errors surface immediately
