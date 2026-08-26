#!/usr/bin/env python3
"""Verify one public PyPI release against the exact local distributions."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

PACKAGE = "openadapt-desktop"
VERSION_PATTERN = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")


def _canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _local_distributions(directory: Path) -> dict[str, tuple[str, int, str]]:
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError(f"distribution directory is invalid: {directory}")
    result: dict[str, tuple[str, int, str]] = {}
    for path in directory.iterdir():
        if not path.is_file() or path.is_symlink():
            continue
        if path.name.endswith(".whl"):
            package_type = "bdist_wheel"
        elif path.name.endswith(".tar.gz"):
            package_type = "sdist"
        else:
            raise ValueError(f"unexpected local distribution: {path.name}")
        if path.name in result:
            raise ValueError(f"duplicate local distribution: {path.name}")
        result[path.name] = (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_size,
            package_type,
        )
    if len(result) != 2 or {value[2] for value in result.values()} != {
        "bdist_wheel",
        "sdist",
    }:
        raise ValueError("local release must contain exactly one wheel and one sdist")
    return result


def verify_pypi_release(metadata_path: Path, directory: Path, version: str) -> None:
    """Require exact public names, sizes, and SHA-256 digests for one version."""

    if VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError(f"version is not an exact stable X.Y.Z value: {version!r}")
    if not metadata_path.is_file() or metadata_path.is_symlink():
        raise ValueError(f"PyPI metadata file is invalid: {metadata_path}")
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("PyPI metadata is not an object")
    info = data.get("info")
    if (
        not isinstance(info, dict)
        or _canonical_name(str(info.get("name") or "")) != PACKAGE
        or info.get("version") != version
    ):
        raise ValueError("PyPI metadata does not identify the requested package version")

    local = _local_distributions(directory)
    urls = data.get("urls")
    if not isinstance(urls, list):
        raise ValueError("PyPI metadata has no release files")
    published: dict[str, tuple[str, int, str]] = {}
    for entry in urls:
        if not isinstance(entry, dict):
            raise ValueError("PyPI release file metadata is invalid")
        filename = entry.get("filename")
        digests = entry.get("digests")
        url = urlparse(str(entry.get("url") or ""))
        if (
            not isinstance(filename, str)
            or Path(filename).name != filename
            or filename in published
            or not isinstance(digests, dict)
            or re.fullmatch(r"[0-9a-f]{64}", str(digests.get("sha256") or "")) is None
            or not isinstance(entry.get("size"), int)
            or entry["size"] <= 0
            or entry.get("packagetype") not in {"bdist_wheel", "sdist"}
            or entry.get("yanked") is not False
            or url.scheme != "https"
            or url.hostname != "files.pythonhosted.org"
        ):
            raise ValueError(f"PyPI release file metadata is invalid: {filename!r}")
        published[filename] = (
            digests["sha256"],
            entry["size"],
            entry["packagetype"],
        )
    if published != local:
        raise ValueError(
            "public PyPI distributions differ from the reviewed build: "
            f"published={published!r}, local={local!r}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    try:
        verify_pypi_release(args.metadata, args.directory, args.version)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.exit(1, f"{exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
