"""The persistent alerting subagent that polls logs and sends alerts."""

from __future__ import annotations

import logging
import time

from agent.subagents.base import BaseSubagent
from agent.tools.alert.tools import (
    create_alert_rule,
    is_silenced,
    parse_log_for_errors,
    query_recent_logs,
    send_telegram_message,
    setup_telegram_bot,
    silence_alert,
)

logger = logging.getLogger("nexus.alerting")

DEFAULT_RULES = [
    {
        "rule_id": "high_error_rate",
        "metric": "error_count",
        "threshold": 5,
        "window_seconds": 300,
        "severity": "critical",
    },
    {
        "rule_id": "error_spike",
        "metric": "error_count",
        "threshold": 10,
        "window_seconds": 60,
        "severity": "critical",
    },
]


class AlertingSubagent(BaseSubagent):
    """Polls deployment logs on an interval and dispatches Telegram alerts."""

    def __init__(self, poll_interval_seconds: int = 60):
        """Initializes the alerting subagent.

        Args:
            poll_interval_seconds: Seconds to wait between log polls.
        """
        super().__init__(
            name="AlertingSubagent",
            system_prompt="",
            allowed_namespaces=["alert", "aws"],
            model="claude-haiku-4-5-20251001",
        )
        self.poll_interval = poll_interval_seconds
        self._running = True

    def run(self, input_data: dict) -> dict:  # type: ignore[override]
        """Runs the polling loop until stopped.

        Configures the Telegram bot, installs the default rules, then
        polls logs every poll_interval seconds, logging and swallowing any
        per-poll errors so the loop keeps running.

        Args:
            input_data: Must contain cluster_name, namespace,
                telegram_bot_token, and telegram_chat_id.

        Returns:
            A {"stopped": True} dict once the loop exits.
        """
        cluster_name = input_data["cluster_name"]
        namespace = input_data["namespace"]
        setup_telegram_bot(
            bot_token=input_data["telegram_bot_token"],
            chat_id=input_data["telegram_chat_id"],
        )
        for rule in DEFAULT_RULES:
            create_alert_rule(**rule)

        logger.info(
            "AlertingSubagent started for %s/%s", cluster_name, namespace
        )

        while self._running:
            try:
                self._poll(cluster_name, namespace)
            except Exception as exc:
                logger.warning("Alert poll error: %s", exc)
            time.sleep(self.poll_interval)

        return {"stopped": True}

    def _poll(self, cluster_name: str, namespace: str) -> None:
        """Evaluates every active rule against the latest logs once.

        Fires and then silences any rule whose error-count threshold is
        exceeded.

        Args:
            cluster_name: The cluster to pull logs from.
            namespace: The namespace to pull logs from.
        """
        from agent.tools.alert.tools import _rules
        logs = query_recent_logs(
            cluster_name=cluster_name, namespace=namespace, tail_lines=200
        )
        parsed = parse_log_for_errors(log_text=logs["logs"])

        for rule in _rules.values():
            if is_silenced(rule["rule_id"]):
                continue
            if (
                rule["metric"] == "error_count"
                and parsed["error_count"] >= rule["threshold"]
            ):
                msg = (
                    f"Cluster: `{cluster_name}` | "
                    f"Namespace: `{namespace}`\n"
                    f"Rule: `{rule['rule_id']}`\n"
                    f"Errors in last {rule['window_seconds']}s: "
                    f"*{parsed['error_count']}* "
                    f"(threshold: {rule['threshold']})\n\n"
                    + "\n".join(e["text"] for e in parsed["errors"][:3])
                )
                send_telegram_message(message=msg, severity=rule["severity"])
                silence_alert(rule_id=rule["rule_id"], duration_seconds=300)

    def stop(self) -> None:
        """Signals the polling loop to exit after its current iteration."""
        self._running = False
