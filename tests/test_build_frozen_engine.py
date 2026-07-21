from __future__ import annotations

from scripts import build_frozen_engine as build


def _build_command(identity: str) -> list[str]:
    return build.build_command(signing_identity=identity, platform="darwin")


def test_developer_id_signs_embedded_binaries_with_tauri_identity() -> None:
    command = _build_command("Developer ID Application: OpenAdapt AI (TEAM123)")

    index = command.index("--codesign-identity")
    assert command[index + 1] == "Developer ID Application: OpenAdapt AI (TEAM123)"


def test_adhoc_build_does_not_enable_hardened_runtime_inside_onefile() -> None:
    command = _build_command("-")

    assert "--codesign-identity" not in command
    for module in build.EXCLUDED_MODULES:
        assert ["--exclude-module", module] == command[
            command.index(module) - 1 : command.index(module) + 1
        ]
