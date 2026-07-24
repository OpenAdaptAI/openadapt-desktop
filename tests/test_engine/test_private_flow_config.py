"""Tests for private target/deployment staging."""

from __future__ import annotations

import stat
from pathlib import Path

import yaml

from engine.private_flow_config import (
    PrivateFlowConfigError,
    flow_log_redactions,
    private_flow_config,
    redact_flow_log,
)
from engine.targets import ExecutionTarget


def test_merges_target_over_base_config_with_mode_0600_and_removes_file(
    tmp_path: Path,
) -> None:
    source = tmp_path / "deployment.yaml"
    source.write_text(
        """
backend:
  kind: rdp
  rdp_host: old.example
  rdp_username: operator
  rdp_password: from-private-source
effects:
  kind: onscreen
""".strip()
    )
    target = ExecutionTarget(
        backend="rdp",
        rdp_host="new.example",
        rdp_window_title="Patient Jane Doe",
        rdp_readiness_text="MRN 12345",
    )

    staged_path: Path | None = None
    with private_flow_config(
        tmp_path / "run",
        source=source,
        target=target,
    ) as staged:
        assert staged is not None
        staged_path = staged
        assert stat.S_IMODE(staged.stat().st_mode) & 0o077 == 0
        merged = yaml.safe_load(staged.read_text())
        assert merged["backend"] == {
            "kind": "rdp",
            "rdp_host": "new.example",
            "rdp_username": "operator",
            "rdp_password": "from-private-source",
            "rdp_window_title": "Patient Jane Doe",
            "rdp_readiness_text": "MRN 12345",
        }
        assert merged["effects"] == {"kind": "onscreen"}

    assert staged_path is not None
    assert not staged_path.exists()
    assert source.exists()


def test_target_only_config_uses_flow_backend_keys(tmp_path: Path) -> None:
    target = ExecutionTarget(
        backend="macos",
        macos_app="TextEdit",
        macos_window_title="Patient Jane Doe",
    )

    with private_flow_config(tmp_path, target=target) as staged:
        assert staged is not None
        assert yaml.safe_load(staged.read_text()) == {
            "backend": {
                "kind": "macos",
                "macos_app": "TextEdit",
                "macos_window_title": "Patient Jane Doe",
            }
        }


def test_config_only_preserves_native_backend_without_web_override(
    tmp_path: Path,
) -> None:
    source = tmp_path / "deployment.yaml"
    source.write_text("backend:\n  kind: linux\n  linux_app: gedit\n")

    with private_flow_config(tmp_path / "run", source=source) as staged:
        assert staged is not None
        merged = yaml.safe_load(staged.read_text())
        assert merged["backend"]["kind"] == "linux"
        assert merged["backend"]["linux_app"] == "gedit"


def test_log_redactions_cover_config_and_direct_selector_values(tmp_path: Path) -> None:
    source = tmp_path / "deployment.yaml"
    source.write_text(
        "backend:\n"
        "  kind: rdp\n"
        "  rdp_window_title: Patient Jane Doe\n"
        "  rdp_password: super-secret\n"
    )
    target = ExecutionTarget(
        backend="rdp",
        rdp_host="10.0.0.5",
        rdp_readiness_text="MRN 12345",
    )

    redactions = flow_log_redactions(source, target)
    safe = redact_flow_log(
        "Patient Jane Doe MRN 12345 at 10.0.0.5 with super-secret",
        redactions,
    )

    assert safe == "[REDACTED] [REDACTED] at [REDACTED] with [REDACTED]"


def test_rejects_non_mapping_config_without_echoing_values(tmp_path: Path) -> None:
    source = tmp_path / "deployment.yaml"
    source.write_text("- Patient Jane Doe\n- MRN 12345\n")

    try:
        with private_flow_config(tmp_path / "run", source=source):
            raise AssertionError("invalid config must not be staged")
    except PrivateFlowConfigError as exc:
        message = str(exc)

    assert "Jane Doe" not in message
    assert "12345" not in message
