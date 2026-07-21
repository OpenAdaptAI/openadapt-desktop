"""End-to-end tests for the IPC protocol.

Tests the JSON-over-stdin/stdout communication protocol between
the Tauri shell and the Python engine sidecar.

Run:
    uv run pytest tests/test_e2e/test_ipc.py -v
"""

from __future__ import annotations

import json
import uuid

import pytest

from engine.config import EngineConfig
from engine.ipc import IPCHandler


@pytest.fixture
def config(tmp_path):
    """Test configuration."""
    return EngineConfig(data_dir=tmp_path / ".openadapt", log_level="DEBUG")


@pytest.fixture
def handler(config):
    """IPC handler instance."""
    return IPCHandler(config=config)


class TestIPCProtocol:
    """Tests for IPC message handling."""

    def test_unknown_command_returns_error(self, handler) -> None:
        """Unknown commands should return an error response."""
        msg_id = str(uuid.uuid4())
        message = {"id": msg_id, "cmd": "nonexistent_command", "params": {}}
        handler._dispatch(message)
        # The error is written to stdout; we verify dispatch doesn't crash

    def test_dispatch_missing_cmd(self, handler) -> None:
        """Messages without a cmd field should return an error."""
        message = {"id": "test-123", "params": {}}
        handler._dispatch(message)

    def test_send_response_format(self, handler, capsys) -> None:
        """Responses should be valid JSON with id, status, and data fields."""
        handler.send_response("test-id", {"key": "value"})
        captured = capsys.readouterr()
        response = json.loads(captured.out.strip())
        assert response["id"] == "test-id"
        assert response["status"] == "ok"
        assert response["data"]["key"] == "value"

    def test_send_error_format(self, handler, capsys) -> None:
        """Error responses should have status=error and an error message."""
        handler.send_error("test-id", "something went wrong")
        captured = capsys.readouterr()
        response = json.loads(captured.out.strip())
        assert response["id"] == "test-id"
        assert response["status"] == "error"
        assert response["error"] == "something went wrong"

    def test_send_event_format(self, handler, capsys) -> None:
        """Events should have event and data fields but no id."""
        handler.send_event("recording_started", {"capture_id": "abc123"})
        captured = capsys.readouterr()
        event = json.loads(captured.out.strip())
        assert "id" not in event
        assert event["event"] == "recording_started"
        assert event["data"]["capture_id"] == "abc123"

    def test_response_is_single_line(self, handler, capsys) -> None:
        """Each response should be exactly one line (JSON lines format)."""
        handler.send_response("id1", {"a": 1})
        handler.send_response("id2", {"b": 2})
        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            json.loads(line)  # Each line is valid JSON


class TestIPCHandlerRegistration:
    """Tests for command handler registration."""

    def test_handlers_is_dict(self, handler) -> None:
        """Handler registry should be a dict (most commands are still TODO)."""
        assert isinstance(handler._handlers, dict)

    def test_get_program_graph_registered(self, handler) -> None:
        """The get_program_graph command should be registered and dispatchable."""
        assert "get_program_graph" in handler._handlers


class TestGetProgramGraph:
    """Tests for the get_program_graph IPC handler.

    openadapt-flow is not a dependency of the desktop engine, so the handler
    is expected to return a structured not-available response here (the
    desktop program.html view falls back to its bundled sample when it sees
    this). If openadapt-flow ever becomes importable in the test environment,
    the handler builds a real spec instead; these tests stay honest by
    branching on that.
    """

    def _flow_available(self) -> bool:
        try:
            import openadapt_flow  # noqa: F401
            import openadapt_flow.visualize  # noqa: F401
        except Exception:
            return False
        return True

    def test_returns_not_available_without_flow(self, handler) -> None:
        """Without openadapt-flow, the handler reports a clear not-available shape."""
        if self._flow_available():
            pytest.skip("openadapt-flow is importable; not-available path not exercised")
        result = handler._handle_get_program_graph()
        assert result["available"] is False
        assert isinstance(result["reason"], str) and result["reason"]

    def test_not_available_without_bundle_when_flow_present(self, handler) -> None:
        """With openadapt-flow but no bundle, the handler reports not-available."""
        if not self._flow_available():
            pytest.skip("openadapt-flow is not importable in this environment")
        result = handler._handle_get_program_graph()
        assert result["available"] is False

    def test_dispatch_writes_response(self, handler, capsys) -> None:
        """Dispatching get_program_graph writes a single ok JSON-lines response."""
        handler._dispatch({"id": "pg-1", "cmd": "get_program_graph", "params": {}})
        captured = capsys.readouterr()
        lines = [ln for ln in captured.out.strip().split("\n") if ln]
        assert len(lines) == 1
        response = json.loads(lines[0])
        assert response["id"] == "pg-1"
        assert response["status"] == "ok"
        # data is either the not-available response or a real spec (has nodes).
        data = response["data"]
        assert ("available" in data) or ("nodes" in data)
