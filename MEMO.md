# MEMO.md — Nexus

## What I Built

Nexus is an autonomous full-stack app builder. Given a natural language description, it plans, scaffolds, deploys, and monitors a complete web application on AWS EKS without manual intervention.

**Core loop:** A parent Claude agent (`claude-opus-4-8`) holds a `BuildState` dataclass and orchestrates five specialized subagents across seven phases (PLANNING → API_SPEC → BUILD → INFRA → TEST → MONITORING → COMPLETE). Each subagent is a real isolated Claude call with a scoped tool set; it cannot see tools from other namespaces.

**What works:**
- Pre-flight cost estimation (LLM tokens + AWS monthly) shown before any build starts
- 69 tools registered across 8 namespaces (plan, code, docker, aws, k8s, test, alert, subagent)
- Full React + FastAPI + PostgreSQL project scaffolding via Jinja2 templates + Tailwind CSS
- OpenAPI spec generated deterministically in `API_SPEC` phase passed to both builders as the single source of truth for field names and endpoint contracts
- `code.generate_api_client` overwrites frontend auth files (api.ts, AuthContext.tsx, Login.tsx, Register.tsx) to exactly match spec field names
- `code.generate_fastapi_auth` overwrites backend auth.py and user.py to exactly match spec field names — eliminates LLM hallucinating field name mismatches between builders
- AWS EKS provisioning, Docker builds, Kubernetes deployment
- `docker-compose.yml` generated in the BUILD phase for local testing before any AWS spend
- Persistent Telegram alerting subagent that polls logs and silences repeat alerts
- Admin monitoring dashboard (CloudWatch metrics, cost, pod health) always included
- Phase checkpointing interrupted runs resume from last completed phase (`--resume`)
- Prompt caching on all subagent system prompts 60–80% cost reduction on retries
- Eval harness with Check.* assertions runnable in mock mode (no AWS spend)
- Unit tests (pytest + vitest) and integration tests (pytest + moto)
- Infra agent is able to provision resources on AWS for deployment of the system
- EKS node group provisioning: timeouts extended to 35+ minutes with per-minute progress logging (eksctl silently takes 30 min; was timing out at 10)

## What I Cut

- **Multi-cloud:** AWS only. The tool interface is cloud-agnostic; GCP/Azure is a new namespace.
- **Real-time build streaming:** Progress is logged to stdout, not streamed over websocket.
- **Multi-region:** Single region. Adding a `region` field to `DeploymentResult` would enable it.
- **Slack / PagerDuty alerting:** Telegram only. The `send_telegram_message` tool is the only integration point; adding Slack is an additional registered tool.
- **Frontend test coverage:** vitest setup is scaffolded but component tests are stubs. A full React Testing Library suite would take another day.

## What Additional Time Would Have Addressed

- Improving agents for lesser error and listing down errors and fixes for RAG training, video walkthrough of the monitoring dashboard, and hardening the InfraSubagent against EKS provisioning edge cases.

- Streaming build progress over a websocket to a terminal UI, so users see each tool call result in real time rather than waiting for phase completion. Also: multi-region support and Slack alerting integration.

- Multi cloud and DNS setup support as well to deploy application for production use. Also, adding proper access control and gaurdrails to save cost and reduce production errors

## One Design Decision I Would Defend

**Fixed stack vs. dynamic stack selection.**

The parent orchestrator never asks the LLM "which framework should we use?" The stack is locked: React + FastAPI + PostgreSQL + EKS. An engineer might reasonably argue this makes Nexus less "general" after all, a truly intelligent builder should choose the right tool for the job.

The counter: stack selection errors cascade. If the LLM picks a template the scaffolding system doesn't support, every subsequent tool call produces broken output. The 5-day constraint forces a choice between breadth (more stacks) and depth (better subagent isolation, better eval harness, better observability). Depth wins because a system that reliably builds one stack with production-grade scaffolding is more useful than one that unreliably attempts five.

The fixed stack is a constraint on the *agent*, not on the *user*. Users still get a general builder: they describe any features, models, and pages they want. The agent figures out how to build those features within the opinionated stack. That's the right scope for a 5-day build.

## Two Bugs I'm Proud of Catching

**eksctl exits 0 when it creates 0 nodegroups.**

On EKS cluster creation, eksctl occasionally runs a "fix compatibility" task instead of creating the nodegroup, exits 0, and returns no error. Every downstream K8s operation then fails because no nodes exist. The fix: after every `eksctl create nodegroup` call, re-verify with `eks.list_nodegroups()` and raise a retryable `NexusError` if the list is empty. This turns a silent infrastructure corruption into a recoverable retry.

**implement checkpoint.json**

I implemented `checkpoint.json` containing all the relevant information of the project. It handles the state, relevant files and other necessary information required for execution. Every agent updates its state here and if the projects faces and error or is stopped in between, this file can be used to resume operation without carrying out previous steps. This saves a lot of cost in tokens as well cloud as services are not redeployed


## One Design Decision I Would Revisit

**Parallel subprocess build with no shared auth contract.**

Backend and frontend builders run concurrently in `ProcessPoolExecutor` workers which cuts build time in half. But they share no state. In the first implementation, neither builder read the OpenAPI spec: the backend LLM generated `username` where the frontend sent `name`, causing 422 errors at runtime that were invisible at scaffold time.

The fix (generating auth files deterministically from the spec via `generate_api_client` and `generate_fastapi_auth`) is the right call. But the real lesson is that "parallel with shared contract" requires the contract to be a file on disk, not a convention the spec YAML is the source of truth, and both builders must overwrite their auth files from it, not from LLM memory.
