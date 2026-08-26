"""Tests for the reviewed release lock and workflow gate."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.verify_release_lock import verify_release_lock

ROOT = Path(__file__).resolve().parents[1]


def _write_candidate(root: Path, *, project: str, lock: str) -> None:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "openadapt-desktop"\nversion = "{project}"\n',
        encoding="utf-8",
    )
    (root / "uv.lock").write_text(
        "[[package]]\n"
        'name = "openadapt-desktop"\n'
        f'version = "{lock}"\n'
        'source = { editable = "." }\n',
        encoding="utf-8",
    )


def test_release_lock_matches_the_reviewed_candidate() -> None:
    verify_release_lock(ROOT)


def test_release_lock_refuses_drift_or_duplicate_editable_entries(tmp_path: Path) -> None:
    _write_candidate(tmp_path, project="1.2.3", lock="1.2.2")
    with pytest.raises(ValueError, match="release lock differs"):
        verify_release_lock(tmp_path)

    _write_candidate(tmp_path, project="1.2.3", lock="1.2.3")
    with (tmp_path / "uv.lock").open("a", encoding="utf-8") as stream:
        stream.write(
            "\n[[package]]\n"
            'name = "openadapt-desktop"\n'
            'version = "1.2.3"\n'
            'source = { editable = "." }\n'
        )
    with pytest.raises(ValueError, match="release lock differs"):
        verify_release_lock(tmp_path)


def test_release_workflow_checks_lock_repository_actor_and_source_boundary() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
        encoding="utf-8"
    )
    assert "OpenAdaptAI/openadapt-desktop" in workflow
    assert "github.actor == 'openadapt-release[bot]'" in workflow
    assert "python scripts/verify_release_lock.py" in workflow
    assert "python scripts/check_source_boundary.py --require-dist" in workflow
    assert "git tag -a" in workflow
    assert "permission-contents: write" in workflow
    assert "permission-metadata: read" in workflow
    assert "GH_TOKEN: ${{ steps.release_app.outputs.token }}" in workflow
    assert "python scripts/verify_pypi_release.py" in workflow
    assert "authorize-release-dispatch" in workflow
    assert "GITHUB_REF_TYPE" in workflow
    assert "needs.authorize-release-dispatch.result == 'success'" in workflow


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("GITHUB_EVENT_NAME", "push"),
        ("GITHUB_REPOSITORY", "OpenAdaptAI/fork"),
        ("GITHUB_REF", "refs/heads/release"),
        ("GITHUB_REF_TYPE", "tag"),
        ("REQUESTED_VERSION", "1.2"),
    ],
)
def test_release_dispatch_guard_refuses_every_invalid_identity(
    field: str, value: str
) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
    script = workflow["jobs"]["authorize-release-dispatch"]["steps"][0]["run"]
    env = os.environ | {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "OpenAdaptAI/openadapt-desktop",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_TYPE": "branch",
        "REQUESTED_VERSION": "1.2.3",
        field: value,
    }

    assert subprocess.run(["bash", "-c", script], env=env, check=False).returncode != 0
