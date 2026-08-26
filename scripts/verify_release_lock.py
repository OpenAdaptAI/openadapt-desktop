#!/usr/bin/env python3
"""Verify that reviewed project metadata matches the editable uv lock entry."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _project_identity(root: Path) -> tuple[str, str]:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(
        r'\[project\]\s+name = "([^"]+)"\s+version = "([^"]+)"',
        text,
        flags=re.MULTILINE,
    )
    if match is None:
        raise ValueError("pyproject.toml has no exact project name and version")
    return match.group(1), match.group(2)


def _editable_lock_versions(lock_text: str, package_name: str) -> list[str]:
    starts = list(re.finditer(r"(?m)^\[\[package\]\]\s*$", lock_text))
    versions: list[str] = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else len(lock_text)
        block = lock_text[start.start() : end]
        if not re.search(
            rf'(?m)^name\s*=\s*"{re.escape(package_name)}"\s*$', block
        ):
            continue
        if not re.search(
            r'(?m)^source\s*=\s*\{\s*editable\s*=\s*"\."\s*\}\s*$', block
        ):
            continue
        match = re.search(r'(?m)^version\s*=\s*"([^"]+)"\s*$', block)
        if match is None:
            raise ValueError(f"editable {package_name!r} lock entry has no version")
        versions.append(match.group(1))
    return versions


def verify_release_lock(root: Path = ROOT) -> None:
    """Fail unless one editable lock entry equals the reviewed project version."""

    package_name, project_version = _project_identity(root)
    lock_versions = _editable_lock_versions(
        (root / "uv.lock").read_text(encoding="utf-8"), package_name
    )
    if lock_versions != [project_version]:
        raise ValueError(
            "release lock differs from reviewed metadata: "
            f"pyproject.toml={project_version!r}, editable uv.lock entries={lock_versions!r}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        verify_release_lock()
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
