from unittest.mock import patch, AsyncMock, MagicMock
from agent.tools.alert.tools import create_alert_rule, list_alert_rules, parse_log_for_errors, silence_alert


def test_create_and_list_alert_rules():
    create_alert_rule(rule_id="high_errors", metric="error_count", threshold=10, window_seconds=300, severity="critical")
    rules = list_alert_rules()
    assert any(r["rule_id"] == "high_errors" for r in rules["rules"])


def test_parse_log_finds_errors():
    logs = "INFO: request ok\nERROR: connection refused\nINFO: request ok\n500 Internal Server Error\n"
    result = parse_log_for_errors(log_text=logs)
    assert result["error_count"] >= 1


def test_silence_alert():
    create_alert_rule(rule_id="test_rule", metric="cpu", threshold=90, window_seconds=60, severity="warning")
    result = silence_alert(rule_id="test_rule", duration_seconds=300)
    assert result["silenced"] is True
