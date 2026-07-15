"""Keep public package metadata aligned with the current product boundary."""

import json
import re
import tomllib
from pathlib import Path

from scripts.check_release_consistency import release_versions

ROOT = Path(__file__).resolve().parents[1]


def test_public_metadata_identifies_experimental_supporting_surface() -> None:
    readme = (ROOT / "README.md").read_text()
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    package = json.loads((ROOT / "package.json").read_text())

    assert "Lifecycle: Experimental supporting surface" in readme
    assert "openadapt-flow" in readme
    assert "AI training data collection" not in readme
    assert "AI training data collection" not in pyproject["project"]["description"]
    assert "AI training data collection" not in package["description"]
    assert pyproject["project"]["readme"] == "README.md"
    assert pyproject["project"]["scripts"] == {
        "openadapt-desktop": "engine.cli:main"
    }


def test_semantic_release_preserves_pre_one_versions() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    release = pyproject["tool"]["semantic_release"]

    assert release["major_on_zero"] is False
    assert release["allow_zero_version"] is True


def test_release_versions_are_synchronized() -> None:
    versions = release_versions()
    assert len(set(versions.values())) == 1, versions


def test_semantic_release_refreshes_lock_and_builds_before_tagging() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    build_command = pyproject["tool"]["semantic_release"]["build_command"]

    assert "uv==0.11.29" in build_command
    assert "uv lock --offline" in build_command
    assert "git add uv.lock" in build_command
    assert "uv build --wheel --sdist" in build_command
    assert "$PACKAGE_NAME" not in build_command


def test_release_workflow_uses_matching_pinned_actions() -> None:
    workflow = (ROOT / ".github/workflows/release.yml").read_text()
    uses = re.findall(r"^\s*uses:\s+\S+@([^\s#]+)", workflow, flags=re.MULTILINE)

    assert uses
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in uses)
    assert "# v10.6.1" in workflow
    assert "# v9.15.2" not in workflow
    assert "token: ${{ secrets.ADMIN_TOKEN }}" in workflow
    assert workflow.count("github_token: ${{ secrets.ADMIN_TOKEN }}") == 2
    assert "- name: Build package" not in workflow


def test_readme_local_links_exist() -> None:
    readme = (ROOT / "README.md").read_text()
    links = re.findall(r"\[[^]]*\]\(([^)]+)\)", readme)

    for link in links:
        target = link.split("#", 1)[0]
        if not target or "://" in target or target.startswith("mailto:"):
            continue
        assert (ROOT / target).exists(), f"README link does not exist: {link}"
