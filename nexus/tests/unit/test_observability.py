import json
import logging
from agent.core.observability import instrument
from agent.core.state import set_session_id

def test_instrument_logs_success(caplog):
    set_session_id("sess-001")
    @instrument(namespace="test", tool="my_tool")
    def my_tool(x: int) -> int:
        return x * 2
    with caplog.at_level(logging.INFO, logger="nexus"):
        result = my_tool(x=5)
    assert result == 10
    assert len(caplog.records) == 1
    record = json.loads(caplog.records[0].message)
    assert record["tool"] == "test.my_tool"
    assert record["status"] == "ok"
    assert record["session_id"] == "sess-001"
    assert record["duration_ms"] >= 0

def test_instrument_logs_error(caplog):
    @instrument(namespace="test", tool="failing_tool")
    def failing_tool():
        raise ValueError("boom")
    with caplog.at_level(logging.INFO, logger="nexus"):
        with __import__("pytest").raises(ValueError):
            failing_tool()
    record = json.loads(caplog.records[0].message)
    assert record["status"] == "error"
    assert "boom" in record["error"]
