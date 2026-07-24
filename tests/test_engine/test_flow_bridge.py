"""Tests for the openadapt-flow CLI wrapper."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.flow_bridge import (
    EMBEDDED_FLOW_MODE,
    BrowserRuntimeError,
    FlowBridge,
    _safe_command_for_log,
    flow_available,
)
from engine.targets import ExecutionTarget


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _runner(recorder, returncode=0, stdout="", stderr=""):
    def run(cmd, capture_output=True, text=True, timeout=None, env=None):
        recorder.append((cmd, env))
        return FakeProc(returncode, stdout, stderr)

    return run


class TestFlowBridgeInvocation:
    def test_compile_builds_args(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.flow_bridge.shutil.which", lambda _: "/usr/bin/openadapt-flow")
        calls: list = []
        bridge = FlowBridge(runner=_runner(calls, stdout="ok"))
        result = bridge.compile(tmp_path / "rec", tmp_path / "bundle")
        assert result.ok
        command, env = calls[0]
        assert command[1] == "compile"
        assert "--out" in command
        assert env is not None
        # openadapt-flow compile requires --name; default it to the bundle name.
        assert "--name" in command
        assert command[command.index("--name") + 1] == "bundle"

    def test_run_builds_args(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.flow_bridge.shutil.which", lambda _: "/usr/bin/openadapt-flow")
        calls: list = []
        bridge = FlowBridge(runner=_runner(calls))
        bridge.run(tmp_path / "bundle", tmp_path / "cfg.yaml", out_dir=tmp_path / "run")
        command, _ = calls[0]
        assert command[1] == "run"
        assert "--config" in command
        # The run directory is passed via --run-dir (not --out).
        assert "--run-dir" in command

    def test_replay_uses_run_dir(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.flow_bridge.shutil.which", lambda _: "/usr/bin/openadapt-flow")
        calls: list = []
        bridge = FlowBridge(runner=_runner(calls))
        bridge.replay(tmp_path / "bundle", out_dir=tmp_path / "run")
        command, _ = calls[0]
        assert command[1] == "replay"
        assert "--run-dir" in command
        assert "--out" not in command

    def test_config_only_replay_never_injects_web_backend(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr("engine.flow_bridge.shutil.which", lambda _: "/usr/bin/openadapt-flow")
        calls: list = []
        bridge = FlowBridge(runner=_runner(calls))
        deployment = tmp_path / "deployment.yaml"
        bridge.replay(
            tmp_path / "bundle",
            out_dir=tmp_path / "run",
            config=deployment,
        )

        command, _ = calls[0]
        assert command == [
            "/usr/bin/openadapt-flow",
            "replay",
            str(tmp_path / "bundle"),
            "--run-dir",
            str(tmp_path / "run"),
            "--config",
            str(deployment),
        ]
        assert "--backend" not in command

    def test_run_uses_only_private_config_path_for_target_details(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr("engine.flow_bridge.shutil.which", lambda _: "/usr/bin/openadapt-flow")
        calls: list = []
        bridge = FlowBridge(runner=_runner(calls))
        deployment = tmp_path / ".deployment-private.yaml"
        bridge.run(
            tmp_path / "bundle",
            deployment,
            out_dir=tmp_path / "run",
        )

        command, _ = calls[0]
        assert command == [
            "/usr/bin/openadapt-flow",
            "run",
            str(tmp_path / "bundle"),
            "--config",
            str(deployment),
            "--run-dir",
            str(tmp_path / "run"),
        ]
        assert "--backend" not in command

    @pytest.mark.parametrize(
        ("target", "expected"),
        [
            (
                ExecutionTarget(backend="windows", agent_url="http://localhost:5001"),
                {"kind": "windows", "agent_url": "http://localhost:5001"},
            ),
            (
                ExecutionTarget(
                    backend="macos",
                    macos_app="TextEdit",
                    macos_window_title="Notes",
                ),
                {
                    "kind": "macos",
                    "macos_app": "TextEdit",
                    "macos_window_title": "Notes",
                },
            ),
            (
                ExecutionTarget(
                    backend="linux",
                    linux_app="gedit",
                    linux_window_title="Notes",
                    linux_allow_physical_input=True,
                ),
                {
                    "kind": "linux",
                    "linux_app": "gedit",
                    "linux_window_title": "Notes",
                    "linux_allow_physical_input": True,
                },
            ),
        ],
    )
    def test_native_targets_render_flow_deployment_overrides(
        self, target: ExecutionTarget, expected: dict[str, object]
    ) -> None:
        assert target.deployment_overrides() == expected

    def test_secret_flag_values_are_redacted_from_debug_command(self) -> None:
        rendered = _safe_command_for_log(
            ["openadapt-flow", "push", "--token", "oar_secret", "--kind", "bundle"]
        )
        assert "oar_secret" not in rendered
        assert rendered == "openadapt-flow push --token [REDACTED] --kind bundle"

    def test_phi_capable_selector_values_are_redacted_from_debug_command(self) -> None:
        rendered = _safe_command_for_log(
            [
                "openadapt-flow",
                "replay",
                "--rdp-window-title",
                "Patient Jane Doe",
                "--rdp-readiness-text",
                "MRN 12345",
            ]
        )
        assert "Jane Doe" not in rendered
        assert "12345" not in rendered

    def test_nonzero_returncode(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr("engine.flow_bridge.shutil.which", lambda _: "/usr/bin/openadapt-flow")
        bridge = FlowBridge(runner=_runner([], returncode=1, stderr="boom"))
        result = bridge.replay(tmp_path / "bundle")
        assert not result.ok
        assert result.stderr == "boom"

    def test_frozen_runtime_uses_own_executable_not_path(self, monkeypatch) -> None:
        monkeypatch.setattr("engine.flow_bridge._is_frozen", lambda: True)
        monkeypatch.setattr("engine.flow_bridge.sys.executable", "/signed/openadapt-engine")
        monkeypatch.setattr("engine.flow_bridge.shutil.which", lambda _: "/tmp/shadowed")
        calls: list = []

        FlowBridge(runner=_runner(calls)).replay(Path("bundle"))

        command, env = calls[0]
        assert command[:3] == [
            "/signed/openadapt-engine",
            EMBEDDED_FLOW_MODE,
            "replay",
        ]
        assert env is not None
        assert flow_available()

    def test_optional_commands_use_same_bundled_runtime(self, monkeypatch) -> None:
        monkeypatch.setattr("engine.flow_bridge._is_frozen", lambda: True)
        monkeypatch.setattr("engine.flow_bridge.sys.executable", "/signed/openadapt-engine")
        calls: list = []
        bridge = FlowBridge(runner=_runner(calls, stdout="wf_123"))

        assert bridge.supports_command("push")
        result = bridge.push(
            Path("bundle"),
            kind="bundle",
            host="https://app.openadapt.ai",
        )

        assert result.ok
        commands = [command for command, _env in calls]
        assert commands[0] == [
            "/signed/openadapt-engine",
            EMBEDDED_FLOW_MODE,
            "push",
            "--help",
        ]
        assert commands[1][:3] == [
            "/signed/openadapt-engine",
            EMBEDDED_FLOW_MODE,
            "push",
        ]


class TestReportParsing:
    def test_read_report_missing(self, tmp_path: Path) -> None:
        assert FlowBridge.read_report(tmp_path) == {}

    def test_read_halt_nested(self, tmp_path: Path) -> None:
        (tmp_path / "report.json").write_text(
            json.dumps({"halt": {"reason": "ambiguous", "step_intent": "click X"}})
        )
        halt = FlowBridge.read_halt(tmp_path)
        assert halt is not None
        assert halt["reason"] == "ambiguous"

    def test_read_halt_top_level_status(self, tmp_path: Path) -> None:
        (tmp_path / "report.json").write_text(json.dumps({"status": "halt", "reason": "drift"}))
        halt = FlowBridge.read_halt(tmp_path)
        assert halt is not None
        assert halt["reason"] == "drift"

    def test_read_halt_none_when_ok(self, tmp_path: Path) -> None:
        (tmp_path / "report.json").write_text(json.dumps({"status": "ok"}))
        assert FlowBridge.read_halt(tmp_path) is None

    @pytest.mark.parametrize(
        ("returncode", "report", "expected"),
        [
            (0, {"success": True}, "success"),
            (0, {"success": True, "terminal_outcome": "success"}, "success"),
            (1, {"success": False, "halt": {"outcome": "halt"}}, "halt"),
            (1, {"terminal_outcome": "halt"}, "halt"),
            (1, {"halt": {"outcome": "escalate"}}, "halt"),
            (1, {"success": True}, "unknown"),
            (0, {"success": False}, "unknown"),
            (0, {}, "unknown"),
            (2, {"results": []}, "unknown"),
            (0, {"success": True, "halt": {"outcome": "halt"}}, "unknown"),
            (0, {"success": "yes"}, "unknown"),
            (1, {"halt": "not-structured"}, "unknown"),
        ],
    )
    def test_classifies_only_explicit_consistent_flow_outcomes(
        self, returncode: int, report: dict, expected: str
    ) -> None:
        assert FlowBridge.classify_outcome(returncode, report) == expected


class TestBrowserRuntime:
    def test_provisions_once_and_reports_progress(self, monkeypatch) -> None:
        calls: list = []
        bridge = FlowBridge(runner=_runner(calls))
        presence = iter((False, True))
        monkeypatch.setattr(bridge, "browser_runtime_present", lambda: next(presence))
        states: list[tuple[str, str]] = []

        bridge.ensure_browser_runtime(lambda state, detail: states.append((state, detail)))

        command, _ = calls[0]
        assert command[1:] == ["-m", "playwright", "install", "chromium"]
        assert [state for state, _ in states] == ["checking", "installing", "ready"]

    def test_existing_offline_prebundle_skips_install(self, monkeypatch) -> None:
        calls: list = []
        bridge = FlowBridge(runner=_runner(calls))
        monkeypatch.setattr(bridge, "browser_runtime_present", lambda: True)
        states: list[str] = []

        bridge.ensure_browser_runtime(lambda state, _detail: states.append(state))

        assert calls == []
        assert states == ["checking", "ready"]

    def test_install_failure_is_explicit_and_retryable(self, monkeypatch) -> None:
        bridge = FlowBridge(runner=_runner([], returncode=1, stderr="network refused"))
        monkeypatch.setattr(bridge, "browser_runtime_present", lambda: False)
        states: list[str] = []

        with pytest.raises(BrowserRuntimeError, match="select Replay to retry"):
            bridge.ensure_browser_runtime(lambda state, _detail: states.append(state))

        assert states == ["checking", "installing", "error"]
