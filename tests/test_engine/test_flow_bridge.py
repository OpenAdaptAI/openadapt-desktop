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

    def test_citrix_replay_forwards_only_workspace_target_flags(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "engine.flow_bridge.shutil.which", lambda _: "/usr/bin/openadapt-flow"
        )
        calls: list = []
        bridge = FlowBridge(runner=_runner(calls))
        target = ExecutionTarget(
            backend="citrix",
            rdp_window="Citrix Viewer",
            rdp_window_title="Production EMR",
            rdp_readiness_text="Patient Search",
        )
        bridge.replay(
            tmp_path / "bundle",
            out_dir=tmp_path / "run",
            config=tmp_path / "deployment.yaml",
            target=target,
        )

        command, _ = calls[0]
        assert command == [
            "/usr/bin/openadapt-flow",
            "replay",
            str(tmp_path / "bundle"),
            "--run-dir",
            str(tmp_path / "run"),
            "--config",
            str(tmp_path / "deployment.yaml"),
            "--backend",
            "citrix",
            "--rdp-window",
            "Citrix Viewer",
            "--rdp-window-title",
            "Production EMR",
            "--rdp-readiness-text",
            "Patient Search",
        ]
        assert "--rdp-host" not in command

    def test_rdp_run_forwards_network_target_and_keeps_credentials_out_of_argv(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "engine.flow_bridge.shutil.which", lambda _: "/usr/bin/openadapt-flow"
        )
        calls: list = []
        bridge = FlowBridge(runner=_runner(calls))
        target = ExecutionTarget(
            backend="rdp",
            rdp_host="10.0.0.5",
            rdp_readiness_text="Patient Search",
        )
        bridge.run(
            tmp_path / "bundle",
            tmp_path / "deployment.yaml",
            out_dir=tmp_path / "run",
            target=target,
        )

        command, _ = calls[0]
        assert command == [
            "/usr/bin/openadapt-flow",
            "run",
            str(tmp_path / "bundle"),
            "--config",
            str(tmp_path / "deployment.yaml"),
            "--run-dir",
            str(tmp_path / "run"),
            "--backend",
            "rdp",
            "--rdp-host",
            "10.0.0.5",
            "--rdp-readiness-text",
            "Patient Search",
        ]
        assert not any("password" in value.lower() for value in command)

    @pytest.mark.parametrize(
        ("target", "expected"),
        [
            (
                ExecutionTarget(
                    backend="windows", agent_url="http://localhost:5001"
                ),
                ["--backend", "windows", "--agent-url", "http://localhost:5001"],
            ),
            (
                ExecutionTarget(
                    backend="macos",
                    macos_app="TextEdit",
                    macos_window_title="Notes",
                ),
                [
                    "--backend",
                    "macos",
                    "--macos-app",
                    "TextEdit",
                    "--macos-window-title",
                    "Notes",
                ],
            ),
            (
                ExecutionTarget(
                    backend="linux",
                    linux_app="gedit",
                    linux_window_title="Notes",
                    linux_allow_physical_input=True,
                ),
                [
                    "--backend",
                    "linux",
                    "--linux-app",
                    "gedit",
                    "--linux-window-title",
                    "Notes",
                    "--linux-allow-physical-input",
                ],
            ),
        ],
    )
    def test_native_targets_use_flow_public_flags(
        self, target: ExecutionTarget, expected: list[str]
    ) -> None:
        assert target.flow_args() == expected

    def test_secret_flag_values_are_redacted_from_debug_command(self) -> None:
        rendered = _safe_command_for_log(
            ["openadapt-flow", "push", "--token", "oar_secret", "--kind", "bundle"]
        )
        assert "oar_secret" not in rendered
        assert rendered == "openadapt-flow push --token [REDACTED] --kind bundle"

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
