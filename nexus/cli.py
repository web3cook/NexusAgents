from __future__ import annotations
import logging
import sys
from enum import Enum
from pathlib import Path
from typing import Optional
import typer
from rich.console import Console
from rich.panel import Panel
from agent.core.observability import setup_logging

app = typer.Typer(name="nexus", help="Autonomous full-stack app builder and deployer")
console = Console()


class LogLevel(str, Enum):
    verbose = "verbose"   # DEBUG — every tool input/output, all internal detail
    normal  = "normal"    # INFO  — phase transitions + tool names + results (default)
    bugs    = "bugs"      # WARNING — only errors and warnings
    silent  = "silent"    # ERROR — essentially quiet


_LOG_LEVELS: dict[LogLevel, int] = {
    LogLevel.verbose: logging.DEBUG,
    LogLevel.normal:  logging.INFO,
    LogLevel.bugs:    logging.WARNING,
    LogLevel.silent:  logging.ERROR,
}


def _print_cost(console: Console, state) -> None:
    ct = state.cost_tracking
    if ct["calls"] == 0:
        return
    console.print("\n[bold]Cost breakdown:[/bold]")
    console.print(f"  Total LLM cost: [green]${ct['total_usd']:.4f}[/green]")
    console.print(f"  Input tokens:   {ct['input_tokens']:,}")
    console.print(f"  Output tokens:  {ct['output_tokens']:,}")
    console.print(f"  Cache reads:    {ct['cache_read_tokens']:,}")
    console.print(f"  API calls:      {ct['calls']}")
    if ct.get("by_model"):
        console.print("  By model:")
        for model, info in sorted(ct["by_model"].items()):
            console.print(f"    {model}: ${info['usd']:.4f} ({info['calls']} calls)")


@app.command()
def build(
    description: str = typer.Argument(..., help="Natural language description of the app to build"),
    workspace: str = typer.Option("/tmp/nexus-workspace", help="Local workspace directory"),
    region: str = typer.Option("us-east-2", help="AWS region"),
    telegram_token: str = typer.Option("", envvar="TELEGRAM_BOT_TOKEN", help="Telegram bot token for alerts"),
    telegram_chat: str = typer.Option("", envvar="TELEGRAM_CHAT_ID", help="Telegram chat ID"),
    dry_run: bool = typer.Option(False, help="Show cost estimate only, do not build"),
    resume: Optional[str] = typer.Option(None, "--resume", metavar="SESSION_ID",
                                         help="Resume a build. Omit SESSION_ID to resume the last session, "
                                              "or pass a session ID (e.g. --resume f33bd627) to resume a specific one."),
    log_level: LogLevel = typer.Option(LogLevel.normal, "--log-level", help="verbose=all detail, normal=progress, bugs=warnings/errors, silent=quiet"),
):
    """Build and deploy a full-stack application from a description."""
    setup_logging(level=_LOG_LEVELS[log_level])
    console.print(Panel.fit("[bold blue]NEXUS[/bold blue] — Autonomous App Builder", subtitle="Starting build..."))
    console.print(f"[dim]Description:[/dim] {description}")
    console.print(f"[dim]Workspace:[/dim] {workspace}")
    console.print(f"[dim]Region:[/dim] {region}\n")

    Path(workspace).mkdir(parents=True, exist_ok=True)

    if telegram_token and telegram_chat:
        from agent.tools.alert.tools import setup_telegram_bot
        setup_telegram_bot(bot_token=telegram_token, chat_id=telegram_chat)

    if dry_run:
        console.print("[yellow]Dry run — showing cost estimate only[/yellow]")
        from agent.tools.plan.tools import analyze_spec, estimate_steps, estimate_tokens, estimate_aws_cost, render_summary
        spec = analyze_spec(user_description=description)
        steps = estimate_steps(feature_count=len(spec["features"]), model_count=len(spec["db_models"]))
        tokens = estimate_tokens(steps=steps["steps"])
        aws = estimate_aws_cost(region=region)
        summary = render_summary(
            aws_monthly_usd=aws["total_monthly_usd"],
            llm_cost_usd=tokens["cost_usd"],
            steps_estimated=steps["steps"],
            llm_tokens_estimated=tokens["total_tokens"],
        )
        console.print(summary["summary"])
        return

    from agent.core.orchestrator import run
    # resume="" means --resume was passed without a value → resume last session
    want_resume = resume is not None
    explicit_id = resume if resume else None
    try:
        state = run(user_description=description, workspace=workspace,
                    resume=want_resume, session_id=explicit_id)
        if state.deployment_result:
            console.print("\n[bold green]✓ Build complete![/bold green]")
            console.print(f"Frontend: [link]{state.deployment_result.frontend_url}[/link]")
            console.print(f"Backend:  [link]{state.deployment_result.backend_url}[/link]")
            console.print(f"Admin:    [link]{state.deployment_result.frontend_url}/admin[/link]")
            console.print(f"\nTool calls: {state.tool_call_count}")
        else:
            console.print("[red]Build did not complete — check logs[/red]")
        _print_cost(console, state)
        if not state.deployment_result:
            sys.exit(1)
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted. Run with --resume to continue.[/yellow]")
        if "state" in dir():
            _print_cost(console, state)
        sys.exit(130)


@app.command()
def eval_cmd(
    description: str = typer.Argument("Build a SaaS app with login and dashboard"),
    workspace: str = typer.Option("/tmp/nexus-eval-workspace"),
    mock: bool = typer.Option(True, help="Use mock AWS (moto) — no real AWS calls"),
    log_level: LogLevel = typer.Option(LogLevel.normal, "--log-level"),
):
    """Run the evaluation harness against a known spec."""
    setup_logging(level=_LOG_LEVELS[log_level])
    from eval.harness import run_eval
    from eval.cases.basic_saas import EVAL_CASE
    from agent.core.state import BuildState, CostSummary, AppSpec

    console.print("[bold]Running eval harness...[/bold]")
    state = BuildState(session_id="eval", user_description=description)
    state.tool_call_count = 25
    state.cost_summary = CostSummary(aws_monthly_usd=47.0, llm_tokens_estimated=180000, llm_cost_usd=2.0, steps_estimated=28)
    state.app_spec = AppSpec(features=["auth"], db_models=["User"], api_routes=["/auth"], pages=["Login"])

    result = run_eval(EVAL_CASE, state)
    console.print(f"\nPassed: [green]{result['passed']}[/green]/{result['total']}")
    for r in result["results"]:
        icon = "✓" if r["passed"] else "✗"
        color = "green" if r["passed"] else "red"
        console.print(f"  [{color}]{icon}[/{color}] {r['name']}: {r['detail']}")


if __name__ == "__main__":
    app()
