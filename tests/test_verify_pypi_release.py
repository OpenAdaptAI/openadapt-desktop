"""Tests for exact public PyPI release verification."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.verify_pypi_release import verify_pypi_release


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    dist = tmp_path / "dist"
    dist.mkdir()
    files = {
        "openadapt_desktop-1.2.3-py3-none-any.whl": b"wheel",
        "openadapt_desktop-1.2.3.tar.gz": b"sdist",
    }
    urls = []
    for name, content in files.items():
        (dist / name).write_bytes(content)
        urls.append(
            {
                "filename": name,
                "digests": {"sha256": hashlib.sha256(content).hexdigest()},
                "size": len(content),
                "packagetype": "bdist_wheel" if name.endswith(".whl") else "sdist",
                "yanked": False,
                "url": f"https://files.pythonhosted.org/packages/test/{name}",
            }
        )
    metadata = tmp_path / "pypi.json"
    metadata.write_text(
        json.dumps({"info": {"name": "openadapt-desktop", "version": "1.2.3"}, "urls": urls}),
        encoding="utf-8",
    )
    return metadata, dist


def test_verify_pypi_release_accepts_the_exact_public_artifacts(tmp_path: Path) -> None:
    metadata, dist = _fixture(tmp_path)
    verify_pypi_release(metadata, dist, "1.2.3")


def test_verify_pypi_release_accepts_only_a_matching_existing_subset(
    tmp_path: Path,
) -> None:
    metadata, dist = _fixture(tmp_path)
    data = json.loads(metadata.read_text(encoding="utf-8"))
    data["urls"] = data["urls"][:1]
    metadata.write_text(json.dumps(data), encoding="utf-8")

    verify_pypi_release(metadata, dist, "1.2.3", allow_subset=True)
    with pytest.raises(ValueError, match="public PyPI distributions differ"):
        verify_pypi_release(metadata, dist, "1.2.3")

    data["urls"][0]["digests"]["sha256"] = "0" * 64
    metadata.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError, match="matching reviewed subset"):
        verify_pypi_release(metadata, dist, "1.2.3", allow_subset=True)


@pytest.mark.parametrize("mutation", ["digest", "extra", "yanked", "host"])
def test_verify_pypi_release_refuses_publication_drift(tmp_path: Path, mutation: str) -> None:
    metadata, dist = _fixture(tmp_path)
    data = json.loads(metadata.read_text(encoding="utf-8"))
    if mutation == "digest":
        data["urls"][0]["digests"]["sha256"] = "0" * 64
    elif mutation == "extra":
        data["urls"].append(dict(data["urls"][0], filename="unexpected.whl"))
    elif mutation == "yanked":
        data["urls"][0]["yanked"] = True
    else:
        data["urls"][0]["url"] = "https://example.com/package.whl"
    metadata.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ValueError):
        verify_pypi_release(metadata, dist, "1.2.3")


def test_verify_pypi_release_refuses_local_extras(tmp_path: Path) -> None:
    metadata, dist = _fixture(tmp_path)
    (dist / "notes.txt").write_text("not a distribution", encoding="utf-8")
    with pytest.raises(ValueError, match="unexpected local distribution"):
        verify_pypi_release(metadata, dist, "1.2.3")
