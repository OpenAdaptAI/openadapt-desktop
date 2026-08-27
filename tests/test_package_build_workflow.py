"""Tests for the build-only Desktop Production package workflow."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.production_release_contract import expected_asset_names

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "package-build.yml"


def _workflow() -> dict:
    value = yaml.safe_load(WORKFLOW_PATH.read_text())
    assert isinstance(value, dict)
    return value


def _steps(job: dict) -> dict[str, dict]:
    return {str(step.get("name") or step.get("uses")): step for step in job["steps"]}


def _uses(workflow: dict) -> list[str]:
    return [
        step["uses"] for job in workflow["jobs"].values() for step in job["steps"] if "uses" in step
    ]


def test_package_build_is_an_exact_release_yml_call_only_interface() -> None:
    workflow = _workflow()
    trigger = workflow[True]

    assert set(trigger) == {"workflow_call"}
    call = trigger["workflow_call"]
    assert set(call["inputs"]) == {
        "version",
        "source_commit",
        "embedded_flow_version",
    }
    assert all(value["required"] is True for value in call["inputs"].values())
    assert all(value["type"] == "string" for value in call["inputs"].values())
    assert call["outputs"]["candidate_artifact_name"]["value"] == (
        "${{ jobs.assemble.outputs.candidate_artifact_name }}"
    )
    assert set(workflow["jobs"]) == {
        "validate",
        "python-distributions",
        "macos",
        "windows",
        "linux",
        "assemble",
    }
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["validate"]["permissions"] == {"contents": "read"}

    guard = _steps(workflow["jobs"]["validate"])[
        "Require the exact release.yml caller and protected-main inputs"
    ]
    assert guard["env"] == {
        "CALLER_WORKFLOW_REF": "${{ github.workflow_ref }}",
        "CALLER_WORKFLOW_SHA": "${{ github.workflow_sha }}",
        "RELEASE_VERSION": "${{ inputs.version }}",
        "SOURCE_COMMIT": "${{ inputs.source_commit }}",
        "EMBEDDED_FLOW_VERSION": "${{ inputs.embedded_flow_version }}",
    }
    baseline = {
        **os.environ,
        "GITHUB_REPOSITORY": "OpenAdaptAI/openadapt-desktop",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_TYPE": "branch",
        "CALLER_WORKFLOW_REF": (
            "OpenAdaptAI/openadapt-desktop/.github/workflows/release.yml@refs/heads/main"
        ),
        "CALLER_WORKFLOW_SHA": "a" * 40,
        "GITHUB_SHA": "a" * 40,
        "SOURCE_COMMIT": "a" * 40,
        "RELEASE_VERSION": "1.2.3",
        "EMBEDDED_FLOW_VERSION": "4.5.6",
    }
    assert (
        subprocess.run(
            ["bash", "-c", guard["run"]],
            env=baseline,
            check=False,
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )

    version_check = _steps(workflow["jobs"]["validate"])[
        "Require the exact package and embedded Flow versions"
    ]["run"]
    assert "git fetch --force origin main:refs/remotes/origin/main" in version_check
    assert 'refs/remotes/origin/main)" = "${SOURCE_COMMIT}"' in version_check


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GITHUB_REPOSITORY", "someone/fork"),
        ("GITHUB_EVENT_NAME", "push"),
        ("GITHUB_REF", "refs/heads/feature"),
        ("GITHUB_REF_TYPE", "tag"),
        (
            "CALLER_WORKFLOW_REF",
            "OpenAdaptAI/openadapt-desktop/.github/workflows/other.yml@refs/heads/main",
        ),
        ("CALLER_WORKFLOW_SHA", "b" * 40),
        ("GITHUB_SHA", "b" * 40),
        ("SOURCE_COMMIT", "A" * 40),
        ("RELEASE_VERSION", "v1.2.3"),
        ("EMBEDDED_FLOW_VERSION", "latest"),
    ],
)
def test_package_build_refuses_invalid_caller_or_inputs(name: str, value: str) -> None:
    workflow = _workflow()
    guard = _steps(workflow["jobs"]["validate"])[
        "Require the exact release.yml caller and protected-main inputs"
    ]["run"]
    environment = {
        **os.environ,
        "GITHUB_REPOSITORY": "OpenAdaptAI/openadapt-desktop",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_TYPE": "branch",
        "CALLER_WORKFLOW_REF": (
            "OpenAdaptAI/openadapt-desktop/.github/workflows/release.yml@refs/heads/main"
        ),
        "CALLER_WORKFLOW_SHA": "a" * 40,
        "GITHUB_SHA": "a" * 40,
        "SOURCE_COMMIT": "a" * 40,
        "RELEASE_VERSION": "1.2.3",
        "EMBEDDED_FLOW_VERSION": "4.5.6",
        name: value,
    }

    result = subprocess.run(
        ["bash", "-c", guard],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "requires the exact release.yml main call" in result.stderr


def test_package_build_has_no_publication_tag_or_recovery_authority() -> None:
    workflow = _workflow()
    source = WORKFLOW_PATH.read_text()
    uses = _uses(workflow)

    assert uses
    assert all(re.fullmatch(r"[^@]+@[0-9a-f]{40}", value) for value in uses)
    assert "contents: write" not in source
    for forbidden in (
        "gh release",
        "git tag",
        "git push",
        "gh-action-pypi-publish",
        "refs/tags/desktop-v",
        "--clobber",
        "recover-published",
    ):
        assert forbidden not in source


def test_package_build_builds_each_exact_target_once_with_closed_evidence() -> None:
    workflow = _workflow()
    source = WORKFLOW_PATH.read_text()
    jobs = workflow["jobs"]

    assert source.count("uv build --wheel --sdist") == 1
    assert source.count("npm run tauri build --") == 3
    assert source.count("--bundles dmg") == 1
    assert source.count("--bundles msi,nsis") == 1
    assert source.count("--bundles deb,appimage") == 1
    assert jobs["macos"]["strategy"]["matrix"]["include"] == [
        {
            "os": "macos-15",
            "architecture": "arm64",
            "target": "aarch64-apple-darwin",
        },
        {
            "os": "macos-15-intel",
            "architecture": "x86_64",
            "target": "x86_64-apple-darwin",
        },
    ]
    assert jobs["windows"]["runs-on"] == "windows-2022"
    assert jobs["linux"]["runs-on"] == "ubuntu-22.04"
    assert jobs["macos"]["environment"] == "native-release"
    assert jobs["windows"]["environment"] == "native-release"

    assert source.count("scripts/production_release_contract.py stage-platform") == 3
    assert source.count("scripts/production_release_contract.py platform-verification") == 3
    assert source.count("scripts/smoke_test_native_installer.py") == 5
    assert "--require-mode developer-id-notarized" in source
    assert "--require-mode authenticode" in source
    assert "Build ad-hoc signed" not in source
    assert "Build unsigned" not in source
    assert "actions/attest-build-provenance@" in source
    assert "gh attestation verify" in source
    assert (
        "https://github.com/OpenAdaptAI/openadapt-desktop/.github/workflows/"
        "release.yml@refs/heads/main"
    ) in source


def test_package_build_emits_one_flat_exact_public_candidate() -> None:
    workflow = _workflow()
    assemble = workflow["jobs"]["assemble"]
    steps = _steps(assemble)
    final_upload = steps["Upload one flat 14-asset candidate"]
    final_validation = steps["Generate checksums and validate every public byte"]["run"]

    assert len(expected_asset_names("1.2.3")) == 14
    assert assemble["outputs"]["candidate_artifact_name"] == "${{ steps.candidate.outputs.name }}"
    assert final_upload["with"] == {
        "name": "${{ steps.candidate.outputs.name }}",
        "path": "release-assets/*",
        "if-no-files-found": "error",
        "retention-days": 30,
        "compression-level": 0,
    }
    assert (
        "desktop-production-assets-${RELEASE_VERSION}-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
        in (steps["Name the exact candidate artifact"]["run"])
    )
    assert "OpenAdapt-Desktop-v${{ inputs.version }}.cyclonedx.json" == (
        steps["Generate the CycloneDX SBOM from the exact staged set"]["with"]["output-file"]
    ).removeprefix("release-assets/")
    assert (
        "upload-artifact" in steps["Generate the CycloneDX SBOM from the exact staged set"]["with"]
    )
    assert (
        steps["Generate the CycloneDX SBOM from the exact staged set"]["with"]["upload-artifact"]
        is False
    )
    assert "release-assets/SHA256SUMS" in final_validation
    assert "wc -l | tr -d ' ')\" = 14" in final_validation
    assert "contract/desktop-artifact-inventory.json" in final_validation
    assert "contract/desktop-artifact-inventory.json" not in final_upload["with"]["path"]
    assert "validate_release_asset_contents" in final_validation
