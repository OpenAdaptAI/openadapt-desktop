#!/usr/bin/env python3
"""Verify the exact file inventory named by an attested SHA256SUMS.

Verify ``SHA256SUMS`` with GitHub first. This helper then refuses missing,
extra, linked, non-regular, duplicate, or digest-mismatched release files.
It uses only the Python standard library so it can run before installation.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import stat
from pathlib import Path


def read_manifest(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, separator, name = line.partition("  ")
        if (
            not separator
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or not name
            or Path(name).name != name
            or name in entries
        ):
            raise ValueError("SHA256SUMS contains an invalid or duplicate entry")
        entries[name] = digest
    if not entries:
        raise ValueError("SHA256SUMS is empty")
    return entries


def verify(directory: Path, manifest: Path) -> int:
    directory = directory.resolve()
    manifest = manifest.resolve()
    if manifest.parent != directory or manifest.name != "SHA256SUMS":
        raise ValueError("SHA256SUMS must be inside the download directory")
    if not manifest.is_file() or manifest.is_symlink():
        raise ValueError("SHA256SUMS must be a regular file")
    entries = read_manifest(manifest)
    members = list(directory.iterdir())
    unsafe = [
        path.name
        for path in members
        if path.is_symlink() or not stat.S_ISREG(path.stat(follow_symlinks=False).st_mode)
    ]
    if unsafe:
        raise ValueError("download directory contains a link or non-regular file")
    actual = {path.name for path in members if path != manifest}
    if actual != set(entries):
        raise ValueError("downloaded files do not equal the signed checksum inventory")
    for name, expected in entries.items():
        observed = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        if observed != expected:
            raise ValueError(f"checksum mismatch for {name}")
    return len(entries)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=Path("."))
    parser.add_argument("--manifest", type=Path, default=Path("SHA256SUMS"))
    args = parser.parse_args()
    try:
        count = verify(args.directory, args.manifest)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"verification failed: {exc}\n")
    print(f"Verified the exact {count}-file native release inventory.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
