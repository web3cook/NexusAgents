# MEMO.md — Nexus

## What I Built

Nexus is an autonomous full-stack app builder. Given a natural language description, it plans, scaffolds, deploys, and monitors a complete web application on AWS EKS — without manual intervention.

**Core loop:** A parent Claude agent (`claude-opus-4-8`) holds a `BuildState` dataclass and orchestrates five specialized subagents across six phases (Planning → Backend → Frontend → Infra → Test → Monitoring). Each subagent is a real isolated Claude call with a scoped tool set; it cannot see tools from other namespaces.

**What works:**
- Pre-flight cost estimation (LLM tokens + AWS monthly) shown before any build starts
- 69 tools registered across 8 namespaces (plan, code, docker, aws, k8s, test, alert, subagent)
- Full React + FastAPI + PostgreSQL project scaffolding via Jinja2 templates
- AWS EKS provisioning, Docker builds, Kubernetes deployment
- Persistent Telegram alerting subagent that polls logs and silences repeat alerts
- Admin monitoring dashboard (CloudWatch metrics, cost, pod health) always included
- Phase checkpointing — interrupted runs resume from last completed phase
- Eval harness with Check.* assertions runnable in mock mode (no AWS spend)
- Unit tests (pytest + vitest) and integration tests (pytest + moto)

## What I Cut

- **Multi-cloud:** AWS only. The tool interface is cloud-agnostic; GCP/Azure is a new namespace.
- **Real-time build streaming:** Progress is logged to stdout, not streamed over websocket.
- **Multi-region:** Single region. Adding a `region` field to `DeploymentResult` would enable it.
- **Slack / PagerDuty alerting:** Telegram only. The `send_telegram_message` tool is the only integration point; adding Slack is an additional registered tool.
- **Frontend test coverage:** vitest setup is scaffolded but component tests are stubs. A full React Testing Library suite would take another day.

## What Additional Time Would Have Addressed

**Day 6:** A live end-to-end eval run against a real AWS account, video walkthrough of the monitoring dashboard, and hardening the InfraSubagent against EKS provisioning edge cases (cluster creation can take 15+ minutes).

**Day 7:** Streaming build progress over a websocket to a terminal UI, so users see each tool call result in real time rather than waiting for phase completion.

## One Design Decision I Would Defend

**Fixed stack vs. dynamic stack selection.**

The parent orchestrator never asks the LLM "which framework should we use?" The stack is locked: React + FastAPI + PostgreSQL + EKS. An engineer might reasonably argue this makes Nexus less "general" — after all, a truly intelligent builder should choose the right tool for the job.

The counter: stack selection errors cascade. If the LLM picks a template the scaffolding system doesn't support, every subsequent tool call produces broken output. The 5-day constraint forces a choice between breadth (more stacks) and depth (better subagent isolation, better eval harness, better observability). Depth wins — because a system that reliably builds one stack with production-grade scaffolding is more useful than one that unreliably attempts five.

The fixed stack is a constraint on the *agent*, not on the *user*. Users still get a general builder: they describe any features, models, and pages they want. The agent figures out how to build those features within the opinionated stack. That's the right scope for a 5-day build.
