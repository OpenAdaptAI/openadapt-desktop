from __future__ import annotations

from scripts import build_frozen_engine as build


def _build_command(monkeypatch, identity: str) -> list[str]:
    observed: list[list[str]] = []
    monkeypatch.setattr(build.sys, "platform", "darwin")
    monkeypatch.setenv("APPLE_SIGNING_IDENTITY", identity)
    monkeypatch.setattr(build.sys, "argv", ["build_frozen_engine.py"])
    monkeypatch.setattr(build.PyInstaller.__main__, "run", lambda command: observed.append(command))

    assert build.main() == 0
    return observed[0]


def test_developer_id_signs_embedded_binaries_with_tauri_identity(monkeypatch) -> None:
    command = _build_command(monkeypatch, "Developer ID Application: OpenAdapt AI (TEAM123)")

    index = command.index("--codesign-identity")
    assert command[index + 1] == "Developer ID Application: OpenAdapt AI (TEAM123)"


def test_adhoc_build_does_not_enable_hardened_runtime_inside_onefile(monkeypatch) -> None:
    command = _build_command(monkeypatch, "-")

    assert "--codesign-identity" not in command
    for module in build.EXCLUDED_MODULES:
        assert ["--exclude-module", module] == command[
            command.index(module) - 1 : command.index(module) + 1
        ]
