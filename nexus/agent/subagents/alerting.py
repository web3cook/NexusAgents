from __future__ import annotations
import logging
import time
from agent.subagents.base import BaseSubagent
from agent.tools.alert.tools import (
    setup_telegram_bot, create_alert_rule, query_recent_logs,
    parse_log_for_errors, send_telegram_message, is_silenced, silence_alert,
)

logger = logging.getLogger("nexus.alerting")

DEFAULT_RULES = [
    {"rule_id": "high_error_rate", "metric": "error_count", "threshold": 5, "window_seconds": 300, "severity": "critical"},
    {"rule_id": "error_spike", "metric": "error_count", "threshold": 10, "window_seconds": 60, "severity": "critical"},
]


class AlertingSubagent(BaseSubagent):
    def __init__(self, poll_interval_seconds: int = 60):
        super().__init__(
            name="AlertingSubagent",
            system_prompt="",
            allowed_namespaces=["alert", "aws"],
            model="claude-haiku-4-5-20251001",
        )
        self.poll_interval = poll_interval_seconds
        self._running = True

    def run(self, input_data: dict) -> dict:  # type: ignore[override]
        cluster_name = input_data["cluster_name"]
        namespace = input_data["namespace"]
        setup_telegram_bot(
            bot_token=input_data["telegram_bot_token"],
            chat_id=input_data["telegram_chat_id"],
        )
        for rule in DEFAULT_RULES:
            create_alert_rule(**rule)

        logger.info(f"AlertingSubagent started for {cluster_name}/{namespace}")

        while self._running:
            try:
                self._poll(cluster_name, namespace)
            except Exception as exc:
                logger.warning(f"Alert poll error: {exc}")
            time.sleep(self.poll_interval)

        return {"stopped": True}

    def _poll(self, cluster_name: str, namespace: str) -> None:
        from agent.tools.alert.tools import _rules
        logs = query_recent_logs(cluster_name=cluster_name, namespace=namespace, tail_lines=200)
        parsed = parse_log_for_errors(log_text=logs["logs"])

        for rule in _rules.values():
            if is_silenced(rule["rule_id"]):
                continue
            if rule["metric"] == "error_count" and parsed["error_count"] >= rule["threshold"]:
                msg = (
                    f"Cluster: `{cluster_name}` | Namespace: `{namespace}`\n"
                    f"Rule: `{rule['rule_id']}`\n"
                    f"Errors in last {rule['window_seconds']}s: *{parsed['error_count']}* (threshold: {rule['threshold']})\n\n"
                    + "\n".join(e["text"] for e in parsed["errors"][:3])
                )
                send_telegram_message(message=msg, severity=rule["severity"])
                silence_alert(rule_id=rule["rule_id"], duration_seconds=300)

    def stop(self) -> None:
        self._running = False
