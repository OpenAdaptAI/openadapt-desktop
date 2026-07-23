from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts import build_frozen_engine as build
from scripts import verify_build_artifact as verify


def _build_command(identity: str, tmp_path: Path) -> list[str]:
    onnxruntime_dir = tmp_path / "onnxruntime"
    onnxruntime_dir.mkdir()
    (onnxruntime_dir / "LICENSE").write_text("MIT\n")
    (onnxruntime_dir / "ThirdPartyNotices.txt").write_text("notices\n")
    return build.build_command(
        signing_identity=identity,
        platform="darwin",
        onnxruntime_dir=onnxruntime_dir,
        notice_bundle=tmp_path / "frozen-notices",
    )


def test_developer_id_signs_embedded_binaries_with_tauri_identity(tmp_path: Path) -> None:
    command = _build_command("Developer ID Application: OpenAdapt AI (TEAM123)", tmp_path)

    index = command.index("--codesign-identity")
    assert command[index + 1] == "Developer ID Application: OpenAdapt AI (TEAM123)"


def test_adhoc_build_does_not_enable_hardened_runtime_inside_onefile(tmp_path: Path) -> None:
    command = _build_command("-", tmp_path)

    assert "--codesign-identity" not in command
    for module in build.EXCLUDED_MODULES:
        assert ["--exclude-module", module] == command[
            command.index(module) - 1 : command.index(module) + 1
        ]


def test_frozen_runtime_bundles_required_third_party_notices(tmp_path: Path) -> None:
    command = _build_command("", tmp_path)

    values = [command[index + 1] for index, value in enumerate(command) if value == "--add-data"]
    assert values == [
        f"{tmp_path / 'onnxruntime' / 'LICENSE'}:third_party/onnxruntime",
        f"{tmp_path / 'onnxruntime' / 'ThirdPartyNotices.txt'}:third_party/onnxruntime",
        f"{build.RAPIDOCR_NOTICE_DIR / 'LICENSE'}:third_party/rapidocr",
        f"{build.RAPIDOCR_NOTICE_DIR / 'NOTICE'}:third_party/rapidocr",
        f"{tmp_path / 'frozen-notices'}:third_party/python",
    ]


def test_missing_onnxruntime_notice_fails_closed(tmp_path: Path) -> None:
    (tmp_path / "LICENSE").write_text("MIT\n")

    with pytest.raises(RuntimeError, match="ThirdPartyNotices.txt"):
        build.notice_data(tmp_path)


def test_windows_frozen_inventory_member_paths_are_normalized() -> None:
    windows_inventory = repr("third_party\\rapidocr\\LICENSE") + "\n"

    normalized = verify.normalized_inventory(windows_inventory)
    assert "third_party/rapidocr/LICENSE" in normalized
    assert "//" not in normalized
    member_keys = verify.frozen_member_keys([r"third_party\python\NOTICE-INVENTORY.json"])
    assert member_keys["third_party/python/NOTICE-INVENTORY.json"] == (
        r"third_party\python\NOTICE-INVENTORY.json"
    )


def test_frozen_inventory_rejects_copyleft_module_names() -> None:
    assert verify.FORBIDDEN_FROZEN_MEMBERS.search("'oa_atomacos._a11y'")
    assert verify.FORBIDDEN_FROZEN_MEMBERS.search("'pynput.keyboard._darwin'")


def test_frozen_notice_inventory_binds_concrete_archive_bytes() -> None:
    payloads = {
        "third_party/python/openadapt-desktop/001-LICENSE": b"desktop MIT\n",
        "third_party/python/openadapt-capture/001-LICENSE": b"capture MIT\n",
        "third_party/python/openadapt-privacy/001-LICENSE": b"privacy MIT\n",
        "third_party/python/openadapt-flow/001-LICENSE": b"flow MIT\n",
        "third_party/python/alembic/001-LICENSE": b"alembic MIT\n",
        "third_party/python/mako/001-LICENSE": b"mako MIT\n",
        "third_party/python/pympler/001-LICENSE": b"Apache\n",
        "third_party/python/pympler/002-NOTICE": b"Pympler notice\n",
        "third_party/python/sqlalchemy/001-LICENSE": b"sqlalchemy MIT\n",
    }
    packages = []
    for name in verify.REQUIRED_NOTICE_TOKENS:
        notices = []
        for member, payload in payloads.items():
            if f"/{name}/" not in member:
                continue
            import hashlib

            notices.append(
                {
                    "bundled_member": member,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                }
            )
        packages.append(
            {
                "name": name,
                "version": "1.0.0",
                "license_evidence": ["MIT"],
                "notices": notices,
            }
        )
    inventory = json.dumps({"schema_version": 1, "packages": packages}).encode()

    verify.validate_frozen_notice_inventory(
        inventory,
        members=set(payloads),
        extract_member=payloads.__getitem__,
    )


def test_frozen_notice_inventory_rejects_copyleft_metadata() -> None:
    inventory = json.dumps(
        {
            "schema_version": 1,
            "packages": [
                {
                    "name": "oa-atomacos",
                    "version": "3.2.0",
                    "license_evidence": ["GPLv2"],
                    "notices": [],
                }
            ],
        }
    ).encode()

    with pytest.raises(ValueError, match="copyleft package"):
        verify.validate_frozen_notice_inventory(
            inventory,
            members=set(),
            extract_member=lambda member: b"",
        )


def test_frozen_notice_inventory_rejects_metadata_only_package() -> None:
    inventory = json.dumps(
        {
            "schema_version": 1,
            "packages": [
                {
                    "name": "metadata-only",
                    "version": "1.0.0",
                    "license_evidence": ["MIT"],
                    "notices": [],
                }
            ],
        }
    ).encode()

    with pytest.raises(ValueError, match="missing concrete notice"):
        verify.validate_frozen_notice_inventory(
            inventory,
            members=set(),
            extract_member=lambda member: b"",
        )
