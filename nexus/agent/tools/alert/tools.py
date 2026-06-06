"""Alerting tools for Telegram notifications and log monitoring."""

from __future__ import annotations

import re
import subprocess
import time

import httpx

from agent.core.errors import AlertingError, NetworkError
from agent.core.observability import instrument
from agent.core.retry import rate_limit, retry
from agent.tools.registry import registry

# In-process store for alert rules and silence state.
_rules: dict[str, dict] = {}
_silenced: dict[str, float] = {}  # rule_id -> silence_until timestamp
_telegram_config: dict = {}


@registry.register(
    name="alert.setup_telegram_bot",
    description=(
        "Configure Telegram bot token and target chat ID for alerting"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "bot_token": {"type": "string"},
            "chat_id": {"type": "string"},
        },
        "required": ["bot_token", "chat_id"],
    },
)
@instrument(namespace="alert", tool="setup_telegram_bot")
def setup_telegram_bot(bot_token: str, chat_id: str) -> dict:
    """Configures the Telegram bot token and target chat ID.

    Args:
        bot_token: The Telegram bot API token.
        chat_id: The target chat or channel ID.

    Returns:
        A dict with configured flag and chat_id.
    """
    _telegram_config["bot_token"] = bot_token
    _telegram_config["chat_id"] = chat_id
    return {"configured": True, "chat_id": chat_id}


@registry.register(
    name="alert.send_telegram_message",
    description=(
        "Send a formatted alert message to the configured Telegram channel"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "message": {"type": "string"},
            "severity": {"type": "string"},
        },
        "required": ["message"],
    },
)
@instrument(namespace="alert", tool="send_telegram_message")
@retry(
    max_attempts=3,
    base_delay_seconds=2.0,
    retryable_on=[NetworkError, AlertingError],
)
def send_telegram_message(
    message: str, severity: str = "warning"
) -> dict:
    """Sends a formatted alert message to the Telegram channel.

    Args:
        message: The message body.
        severity: Alert severity ("info", "warning", or "critical").

    Returns:
        A dict with sent flag and message_id.

    Raises:
        AlertingError: If the bot is not configured.
        NetworkError: If the Telegram API returns a non-200 response.
    """
    rate_limit("alert")
    if not _telegram_config.get("bot_token"):
        raise AlertingError(
            "Telegram not configured — call alert.setup_telegram_bot first"
        )
    emoji = {
        "critical": "🔴",
        "warning": "🟡",
        "info": "🟢",
    }.get(severity, "⚪")
    text = f"{emoji} *NEXUS ALERT* [{severity.upper()}]\n\n{message}"
    resp = httpx.post(
        f"https://api.telegram.org/bot{_telegram_config['bot_token']}"
        "/sendMessage",
        json={
            "chat_id": _telegram_config["chat_id"],
            "text": text,
            "parse_mode": "Markdown",
        },
        timeout=10.0,
    )
    if resp.status_code != 200:
        raise NetworkError(
            f"Telegram API error: {resp.status_code} {resp.text[:200]}"
        )
    return {
        "sent": True,
        "message_id": resp.json().get("result", {}).get("message_id"),
    }


@registry.register(
    name="alert.create_alert_rule",
    description=(
        "Define an alert rule: metric + threshold + time window + severity"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "rule_id": {"type": "string"},
            "metric": {"type": "string"},
            "threshold": {"type": "number"},
            "window_seconds": {"type": "integer"},
            "severity": {
                "type": "string",
                "enum": ["info", "warning", "critical"],
            },
        },
        "required": [
            "rule_id", "metric", "threshold", "window_seconds", "severity"
        ],
    },
)
@instrument(namespace="alert", tool="create_alert_rule")
def create_alert_rule(
    rule_id: str,
    metric: str,
    threshold: float,
    window_seconds: int,
    severity: str,
) -> dict:
    """Defines an alert rule with a metric threshold and time window.

    Args:
        rule_id: A unique identifier for the rule.
        metric: The metric name to evaluate.
        threshold: The value above which the alert fires.
        window_seconds: The evaluation window in seconds.
        severity: Alert severity ("info", "warning", or "critical").

    Returns:
        A dict with rule_id and created flag.
    """
    _rules[rule_id] = {
        "rule_id": rule_id,
        "metric": metric,
        "threshold": threshold,
        "window_seconds": window_seconds,
        "severity": severity,
        "created_at": time.time(),
    }
    return {"rule_id": rule_id, "created": True}


@registry.register(
    name="alert.list_alert_rules",
    description="List all active alert rules for this deployment",
    input_schema={"type": "object", "properties": {}},
)
@instrument(namespace="alert", tool="list_alert_rules")
def list_alert_rules() -> dict:
    """Lists all active alert rules.

    Returns:
        A dict with rules list and total count.
    """
    return {"rules": list(_rules.values()), "total": len(_rules)}


@registry.register(
    name="alert.query_recent_logs",
    description="Pull recent CloudWatch log entries for a deployment",
    input_schema={
        "type": "object",
        "properties": {
            "cluster_name": {"type": "string"},
            "namespace": {"type": "string"},
            "tail_lines": {"type": "integer"},
        },
        "required": ["cluster_name", "namespace"],
    },
)
@instrument(namespace="alert", tool="query_recent_logs")
def query_recent_logs(
    cluster_name: str, namespace: str, tail_lines: int = 200
) -> dict:
    """Pulls recent log lines from all pods in a namespace.

    Args:
        cluster_name: The cluster to pull logs from (informational).
        namespace: The Kubernetes namespace.
        tail_lines: Number of log lines to retrieve per container.

    Returns:
        A dict with logs text, line count, and namespace.
    """
    rate_limit("alert")
    try:
        result = subprocess.run(
            [
                "kubectl", "logs", "-n", namespace,
                "--all-containers",
                f"--tail={tail_lines}",
                "--prefix",
            ],
            capture_output=True, text=True,
            stdin=subprocess.DEVNULL, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {
            "logs": "",
            "lines": 0,
            "namespace": namespace,
            "error": "kubectl logs timed out after 30s",
        }
    return {
        "logs": result.stdout,
        "lines": len(result.stdout.splitlines()),
        "namespace": namespace,
    }


@registry.register(
    name="alert.parse_log_for_errors",
    description=(
        "Extract error patterns, HTTP 5xx codes, and stack traces "
        "from log text"
    ),
    input_schema={
        "type": "object",
        "properties": {"log_text": {"type": "string"}},
        "required": ["log_text"],
    },
)
@instrument(namespace="alert", tool="parse_log_for_errors")
def parse_log_for_errors(log_text: str) -> dict:
    """Extracts error patterns, HTTP 5xx codes, and stack traces.

    Args:
        log_text: The raw log text to search.

    Returns:
        A dict with error_count and a list of up to 20 error entries.
    """
    errors = []
    patterns = [r"ERROR", r"CRITICAL", r"Exception", r"Traceback", r"5\d\d\s"]
    for i, line in enumerate(log_text.splitlines(), 1):
        for pat in patterns:
            if re.search(pat, line, re.IGNORECASE):
                errors.append({"line": i, "text": line.strip()[:200]})
                break
    return {"error_count": len(errors), "errors": errors[:20]}


@registry.register(
    name="alert.silence_alert",
    description="Silence an alert rule for a duration to prevent spam",
    input_schema={
        "type": "object",
        "properties": {
            "rule_id": {"type": "string"},
            "duration_seconds": {"type": "integer"},
        },
        "required": ["rule_id", "duration_seconds"],
    },
)
@instrument(namespace="alert", tool="silence_alert")
def silence_alert(rule_id: str, duration_seconds: int) -> dict:
    """Silences an alert rule for a specified duration.

    Args:
        rule_id: The rule to silence.
        duration_seconds: How long to suppress alerts for this rule.

    Returns:
        A dict with rule_id, silenced flag, and expiry timestamp.
    """
    _silenced[rule_id] = time.time() + duration_seconds
    return {
        "rule_id": rule_id,
        "silenced": True,
        "until": _silenced[rule_id],
    }


def is_silenced(rule_id: str) -> bool:
    """Returns True if the rule is currently silenced.

    Args:
        rule_id: The rule to check.

    Returns:
        True if the rule has an active silence entry.
    """
    return rule_id in _silenced and _silenced[rule_id] > time.time()
