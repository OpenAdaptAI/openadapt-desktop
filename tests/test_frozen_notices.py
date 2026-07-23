from __future__ import annotations

import hashlib
import json
from email.message import Message
from pathlib import Path, PurePosixPath

import pytest

from scripts import frozen_notices as notices


class FakeDistribution:
    def __init__(
        self,
        root: Path,
        name: str,
        *,
        version: str = "1.0.0",
        requires: list[str] | None = None,
        license_expression: str = "MIT",
        notice_files: dict[str, str] | None = None,
    ) -> None:
        self.root = root
        self.version = version
        self.requires = requires or []
        self.metadata = Message()
        self.metadata["Name"] = name
        self.metadata["License-Expression"] = license_expression
        self.files = []
        for member, text in (notice_files or {}).items():
            path = root / member
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            self.files.append(PurePosixPath(member))

    def locate_file(self, member: PurePosixPath) -> Path:
        return self.root / member


def test_dependency_closure_resolves_only_selected_extra(tmp_path: Path) -> None:
    distributions = {
        "desktop": FakeDistribution(
            tmp_path / "desktop",
            "desktop",
            requires=[
                "runtime",
                "builder; extra == 'build'",
                "developer; extra == 'dev'",
            ],
        ),
        "runtime": FakeDistribution(tmp_path / "runtime", "runtime"),
        "builder": FakeDistribution(tmp_path / "builder", "builder", requires=["runtime"]),
        "developer": FakeDistribution(tmp_path / "developer", "developer"),
    }

    closure = notices.dependency_closure(
        root_name="desktop",
        root_extras={"build"},
        distribution_getter=distributions.__getitem__,
    )

    assert set(closure) == {"desktop", "runtime", "builder"}


def test_frozen_runtime_closure_excludes_build_tools(tmp_path: Path) -> None:
    distributions = {
        "openadapt-desktop": FakeDistribution(
            tmp_path / "desktop",
            "openadapt-desktop",
            requires=[
                "desktop-runtime",
                "pyinstaller; extra == 'build'",
                "openadapt-flow; extra == 'build'",
            ],
        ),
        "desktop-runtime": FakeDistribution(
            tmp_path / "desktop-runtime",
            "desktop-runtime",
        ),
        "openadapt-flow": FakeDistribution(
            tmp_path / "flow",
            "openadapt-flow",
            requires=["flow-runtime"],
        ),
        "flow-runtime": FakeDistribution(tmp_path / "flow-runtime", "flow-runtime"),
        "pyinstaller": FakeDistribution(tmp_path / "pyinstaller", "pyinstaller"),
    }

    closure = notices.frozen_runtime_closure(
        distribution_getter=distributions.__getitem__,
    )

    assert set(closure) == {
        "openadapt-desktop",
        "desktop-runtime",
        "openadapt-flow",
        "flow-runtime",
    }
    assert "pyinstaller" not in closure


def test_dependency_closure_terminates_on_cycles(tmp_path: Path) -> None:
    distributions = {
        "desktop": FakeDistribution(
            tmp_path / "desktop",
            "desktop",
            requires=["runtime"],
        ),
        "runtime": FakeDistribution(
            tmp_path / "runtime",
            "runtime",
            requires=["desktop"],
        ),
    }

    closure = notices.dependency_closure(
        root_name="desktop",
        root_extras=set(),
        distribution_getter=distributions.__getitem__,
    )

    assert set(closure) == {"desktop", "runtime"}


def _bootloader_kwargs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    runtime_closure: dict[str, FakeDistribution],
) -> dict[str, object]:
    notice_text = "\n".join(notices.PYINSTALLER_EXCEPTION_MARKERS) + "\n"
    pyinstaller = FakeDistribution(
        tmp_path / "pyinstaller",
        "pyinstaller",
        version=notices.PYINSTALLER_VERSION,
        license_expression="GPL-2.0-or-later WITH Bootloader-exception",
        notice_files={
            (
                f"pyinstaller-{notices.PYINSTALLER_VERSION}.dist-info/licenses/COPYING.txt"
            ): notice_text,
            "PyInstaller/__init__.py": "",
        },
    )
    monkeypatch.setattr(
        notices,
        "PYINSTALLER_NOTICE_SHA256",
        hashlib.sha256(notice_text.encode()).hexdigest(),
    )
    return {
        "build_closure": {**runtime_closure, "pyinstaller": pyinstaller},
        "pyinstaller_dist": pyinstaller,
    }


def test_notice_bundle_copies_files_and_hashes_inventory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    desktop = FakeDistribution(
        tmp_path / "desktop",
        "openadapt-desktop",
        notice_files={"dist-info/LICENSE": "desktop MIT\n"},
    )
    capture = FakeDistribution(
        tmp_path / "capture",
        "openadapt-capture",
        notice_files={"dist-info/LICENSE": "capture MIT\n"},
    )
    closure = {
        "openadapt-capture": capture,
        "openadapt-desktop": desktop,
    }
    output = notices.prepare_notice_bundle(
        tmp_path / "bundle",
        root_license=tmp_path / "unused",
        closure=closure,
        required_notice_tokens={
            "openadapt-desktop": ("license",),
            "openadapt-capture": ("license",),
        },
        **_bootloader_kwargs(tmp_path, monkeypatch, closure),
    )

    inventory = json.loads((output / notices.NOTICE_INVENTORY_NAME).read_text())
    assert inventory["schema_version"] == 2
    assert [package["name"] for package in inventory["packages"]] == [
        "openadapt-capture",
        "openadapt-desktop",
    ]
    for package in inventory["packages"]:
        assert package["notices"]
        member = package["notices"][0]["bundled_member"]
        relative = member.removeprefix(f"{notices.NOTICE_BUNDLE_MEMBER}/")
        assert (output / relative).is_file()
    assert inventory["embedded_build_components"][0]["name"] == "pyinstaller-bootloader"
    assert inventory["build_only_packages"] == [
        {
            "archive_import_roots": ["PyInstaller"],
            "name": "pyinstaller",
            "version": notices.PYINSTALLER_VERSION,
        }
    ]
    assert (output / "build-components" / "pyinstaller" / "COPYING.txt").is_file()


def test_bootloader_notice_rejects_unreviewed_bytes(tmp_path: Path) -> None:
    pyinstaller = FakeDistribution(
        tmp_path / "pyinstaller",
        "pyinstaller",
        version=notices.PYINSTALLER_VERSION,
        license_expression="GPL-2.0-or-later WITH Bootloader-exception",
        notice_files={
            (
                f"pyinstaller-{notices.PYINSTALLER_VERSION}.dist-info/licenses/COPYING.txt"
            ): "\n".join(notices.PYINSTALLER_EXCEPTION_MARKERS) + "\nchanged\n",
        },
    )

    with pytest.raises(RuntimeError, match="notice bytes require review"):
        notices._stage_pyinstaller_bootloader_notice(  # noqa: SLF001
            tmp_path / "bundle",
            pyinstaller_dist=pyinstaller,
        )


def test_bootloader_notice_rejects_unreviewed_version(tmp_path: Path) -> None:
    pyinstaller = FakeDistribution(
        tmp_path / "pyinstaller",
        "pyinstaller",
        version="6.22.0",
        license_expression="GPL-2.0-or-later WITH Bootloader-exception",
    )

    with pytest.raises(RuntimeError, match="requires a new bootloader license review"):
        notices._stage_pyinstaller_bootloader_notice(  # noqa: SLF001
            tmp_path / "bundle",
            pyinstaller_dist=pyinstaller,
        )


def test_notice_bundle_rejects_copyleft_before_staging(tmp_path: Path) -> None:
    forbidden = FakeDistribution(
        tmp_path / "forbidden",
        "oa-atomacos",
        license_expression="GPL-2.0",
        notice_files={"dist-info/LICENSE": "GPL\n"},
    )

    with pytest.raises(RuntimeError, match="copyleft distribution"):
        notices.prepare_notice_bundle(
            tmp_path / "bundle",
            root_license=tmp_path / "LICENSE",
            closure={"oa-atomacos": forbidden},
            required_notice_tokens={},
        )


def test_first_party_mit_fallback_is_explicit_and_concrete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    privacy = FakeDistribution(tmp_path / "privacy", "openadapt-privacy")
    root_license = tmp_path / "LICENSE"
    root_license.write_text("OpenAdapt MIT terms\n")

    closure = {"openadapt-privacy": privacy}
    output = notices.prepare_notice_bundle(
        tmp_path / "bundle",
        root_license=root_license,
        closure=closure,
        required_notice_tokens={"openadapt-privacy": ("license",)},
        **_bootloader_kwargs(tmp_path, monkeypatch, closure),
    )

    inventory = json.loads((output / notices.NOTICE_INVENTORY_NAME).read_text())
    notice = inventory["packages"][0]["notices"][0]
    assert notice["source_member"] == "workspace:LICENSE"
    relative = notice["bundled_member"].removeprefix(f"{notices.NOTICE_BUNDLE_MEMBER}/")
    assert (output / relative).read_text() == "OpenAdapt MIT terms\n"


def test_notice_bundle_rejects_third_party_without_concrete_notice(
    tmp_path: Path,
) -> None:
    unknown = FakeDistribution(
        tmp_path / "unknown",
        "unknown-package",
        license_expression="",
    )

    with pytest.raises(RuntimeError, match="no concrete license/NOTICE"):
        notices.prepare_notice_bundle(
            tmp_path / "bundle",
            root_license=tmp_path / "LICENSE",
            closure={"unknown-package": unknown},
            required_notice_tokens={},
        )


def test_metadata_license_does_not_replace_concrete_notice(
    tmp_path: Path,
) -> None:
    dependency = FakeDistribution(
        tmp_path / "dependency",
        "metadata-only",
        license_expression="MIT",
    )

    with pytest.raises(RuntimeError, match="no concrete license/NOTICE"):
        notices.prepare_notice_bundle(
            tmp_path / "bundle",
            root_license=tmp_path / "LICENSE",
            closure={"metadata-only": dependency},
            required_notice_tokens={},
        )
