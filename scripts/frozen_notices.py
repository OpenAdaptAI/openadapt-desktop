"""Build a license/NOTICE bundle for the frozen Desktop dependency closure.

The native sidecar is a redistribution boundary: PyInstaller embeds Python
packages that are otherwise installed as separate wheels.  Derive the closure
from installed distribution metadata, preserve every available license/NOTICE
file, and emit a hash-bound inventory that the artifact verifier can inspect.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections import deque
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Callable, Iterable

ROOT_DISTRIBUTION = "openadapt-desktop"
FROZEN_RUNTIME_ROOTS = ("openadapt-desktop", "openadapt-flow")
BUILD_EXTRA = frozenset({"build"})
NOTICE_BUNDLE_MEMBER = "third_party/python"
NOTICE_INVENTORY_NAME = "NOTICE-INVENTORY.json"

# PyInstaller itself is a build tool and must not be classified as an ordinary
# frozen runtime package. Its compiled bootloader and loader files are,
# however, embedded in every executable under the upstream Bootloader
# Exception. Pin the reviewed upstream notice bytes so an upgrade requires an
# explicit release-boundary review instead of silently inheriting new terms.
PYINSTALLER_DISTRIBUTION = "pyinstaller"
PYINSTALLER_VERSION = "6.21.0"
PYINSTALLER_NOTICE_SHA256 = "dcf75fdb959db1e3b41c0f8505069d2ece781b5ec6b3d0a4d30975cfc6580245"
PYINSTALLER_NOTICE_MEMBER = f"{NOTICE_BUNDLE_MEMBER}/build-components/pyinstaller/COPYING.txt"
PYINSTALLER_EXCEPTION_MARKERS = (
    "The PyInstaller licensing terms",
    "Bootloader Exception",
    "unlimited permission to link or embed compiled bootloader",
    "./PyInstaller/loader",
)

# These packages may be used as separately distributed optional components, but
# they must not be copied into OpenAdapt's permissively licensed one-file
# runtime.  The metadata scan below also catches any other GPL/AGPL/LGPL
# distribution before PyInstaller runs.
FORBIDDEN_FROZEN_DISTRIBUTIONS = frozenset({"oa-atomacos", "pynput"})
COPYLEFT_LICENSE_RE = re.compile(
    r"(?:\bA?GPL(?:v?\d|[-+. ]|$)|\bLGPL(?:v?\d|[-+. ]|$)|"
    r"GNU (?:AFFERO |LESSER )?GENERAL PUBLIC LICENSE)",
    re.IGNORECASE,
)
NOTICE_FILE_RE = re.compile(
    r"(?:^|[/\\])(?:licen[cs]e|copying|notice|authors)(?:[._-]|$)",
    re.IGNORECASE,
)

# These packages are central to the Desktop/Capture/Flow runtime and therefore
# must contribute concrete notice text, not only a short metadata classifier.
# Other closure members with notice files are bundled as well.
REQUIRED_NOTICE_TOKENS: dict[str, tuple[str, ...]] = {
    "openadapt-desktop": ("license",),
    "openadapt-capture": ("license",),
    "openadapt-privacy": ("license",),
    "openadapt-flow": ("license",),
    "alembic": ("license",),
    "mako": ("license",),
    "pympler": ("license", "notice"),
    "sqlalchemy": ("license",),
}
FIRST_PARTY_MIT_FALLBACK = frozenset(
    {"openadapt-desktop", "openadapt-flow", "openadapt-capture", "openadapt-privacy"}
)


def _canonicalize_name(name: str) -> str:
    """Normalize a distribution name without importing build-only tooling."""

    return re.sub(r"[-_.]+", "-", name).lower()


def _metadata_values(metadata, key: str) -> list[str]:
    values = metadata.get_all(key) if hasattr(metadata, "get_all") else None
    if values:
        return [str(value) for value in values if value]
    value = metadata.get(key)
    return [str(value)] if value else []


def license_evidence(dist) -> list[str]:
    """Return the distribution's machine-readable license declarations."""

    evidence: list[str] = []
    for key in ("License-Expression", "License"):
        evidence.extend(_metadata_values(dist.metadata, key))
    evidence.extend(
        classifier
        for classifier in _metadata_values(dist.metadata, "Classifier")
        if classifier.startswith("License ::")
    )
    return evidence


def dependency_closure(
    *,
    root_name: str = ROOT_DISTRIBUTION,
    root_extras: Iterable[str] = (),
    distribution_getter: Callable[[str], object] = distribution,
) -> dict[str, object]:
    """Resolve the installed dependency closure for the selected root extras."""

    try:
        from packaging.markers import default_environment
        from packaging.requirements import InvalidRequirement, Requirement
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "the build extra is required to resolve the frozen dependency closure"
        ) from exc

    environment = default_environment()
    pending: deque[tuple[str, frozenset[str]]] = deque([(root_name, frozenset(root_extras))])
    resolved: dict[str, object] = {}
    processed_contexts: dict[str, set[str]] = {}

    while pending:
        requested_name, requested_extras = pending.popleft()
        canonical_name = _canonicalize_name(requested_name)
        try:
            dist = resolved.get(canonical_name) or distribution_getter(requested_name)
        except PackageNotFoundError as exc:
            raise RuntimeError(f"frozen dependency {requested_name!r} is not installed") from exc

        resolved[canonical_name] = dist
        prior_contexts = processed_contexts.setdefault(canonical_name, set())
        requested_contexts = {""} | set(requested_extras)
        new_contexts = requested_contexts - prior_contexts
        if not new_contexts:
            continue
        prior_contexts.update(new_contexts)

        for raw_requirement in dist.requires or ():
            try:
                requirement = Requirement(raw_requirement)
            except InvalidRequirement as exc:
                raise RuntimeError(
                    f"{canonical_name} has an invalid Requires-Dist entry: {raw_requirement!r}"
                ) from exc
            active = False
            for extra in new_contexts:
                marker_environment = dict(environment)
                marker_environment["extra"] = extra
                if requirement.marker is None or requirement.marker.evaluate(marker_environment):
                    active = True
                    break
            if active:
                pending.append((requirement.name, frozenset(requirement.extras)))

    return dict(sorted(resolved.items()))


def frozen_runtime_closure(
    *,
    distribution_getter: Callable[[str], object] = distribution,
) -> dict[str, object]:
    """Resolve packages that execute in the sidecar, excluding build tooling.

    Flow is a deliberately frozen payload selected from Desktop's ``build``
    extra, but PyInstaller and its packaging dependencies are not. Resolve the
    two actual runtime roots without extras instead of treating every member of
    ``Desktop[build]`` as redistributed Python runtime code.
    """

    resolved: dict[str, object] = {}
    for root_name in FROZEN_RUNTIME_ROOTS:
        closure = dependency_closure(
            root_name=root_name,
            root_extras=(),
            distribution_getter=distribution_getter,
        )
        for name, dist in closure.items():
            prior = resolved.get(name)
            if prior is not None and str(prior.version) != str(dist.version):
                raise RuntimeError(
                    f"frozen dependency version conflict for {name}: "
                    f"{prior.version} != {dist.version}"
                )
            resolved[name] = dist
    return dict(sorted(resolved.items()))


def _notice_sources(dist) -> list[tuple[str, Path]]:
    sources: list[tuple[str, Path]] = []
    for member in dist.files or ():
        member_name = str(member).replace("\\", "/")
        if not NOTICE_FILE_RE.search(member_name):
            continue
        source = Path(dist.locate_file(member))
        if source.is_file():
            sources.append((member_name, source))
    return sorted(sources)


def _declared_name(dist) -> str:
    name = dist.metadata.get("Name")
    if not name:
        raise RuntimeError("installed distribution metadata is missing Name")
    return _canonicalize_name(str(name))


def _reject_copyleft(closure: dict[str, object]) -> None:
    for canonical_name, dist in closure.items():
        evidence = license_evidence(dist)
        if canonical_name in FORBIDDEN_FROZEN_DISTRIBUTIONS or COPYLEFT_LICENSE_RE.search(
            "\n".join(evidence)
        ):
            detail = "; ".join(evidence) or "known forbidden distribution"
            raise RuntimeError(
                f"refusing copyleft distribution in frozen runtime: {canonical_name} ({detail})"
            )


def _top_level_imports(dist) -> tuple[str, ...]:
    """Return import roots supplied by a wheel, for artifact exclusion checks."""

    roots: set[str] = set()
    for member in dist.files or ():
        top_level = str(member).replace("\\", "/").split("/", 1)[0]
        if top_level.endswith(".py"):
            top_level = top_level[:-3]
        if top_level.isidentifier():
            roots.add(top_level)
    if not roots:
        raise RuntimeError(
            f"build-only distribution {_declared_name(dist)} has no auditable import roots"
        )
    return tuple(sorted(roots))


def _build_only_packages(
    runtime_closure: dict[str, object],
    *,
    build_closure: dict[str, object] | None = None,
) -> list[dict[str, object]]:
    """Describe build-only imports that the final archive must not contain."""

    if build_closure is None:
        build_closure = dependency_closure(
            root_name=ROOT_DISTRIBUTION,
            root_extras=BUILD_EXTRA,
        )
    packages: list[dict[str, object]] = []
    for name in sorted(set(build_closure) - set(runtime_closure)):
        dist = build_closure[name]
        packages.append(
            {
                "name": _declared_name(dist),
                "version": str(dist.version),
                "archive_import_roots": list(_top_level_imports(dist)),
            }
        )
    if PYINSTALLER_DISTRIBUTION not in {package["name"] for package in packages}:
        raise RuntimeError("PyInstaller was not isolated as a build-only distribution")
    return packages


def _stage_pyinstaller_bootloader_notice(
    output: Path,
    *,
    pyinstaller_dist=None,
) -> dict[str, object]:
    """Stage the exact reviewed license exception for the embedded bootloader."""

    if pyinstaller_dist is None:
        pyinstaller_dist = distribution(PYINSTALLER_DISTRIBUTION)
    declared_name = _declared_name(pyinstaller_dist)
    if declared_name != PYINSTALLER_DISTRIBUTION:
        raise RuntimeError(
            "PyInstaller distribution name drift: "
            f"expected {PYINSTALLER_DISTRIBUTION}, got {declared_name}"
        )
    if str(pyinstaller_dist.version) != PYINSTALLER_VERSION:
        raise RuntimeError(
            "PyInstaller version requires a new bootloader license review: "
            f"expected {PYINSTALLER_VERSION}, got {pyinstaller_dist.version}"
        )

    candidates = [
        (member, source)
        for member, source in _notice_sources(pyinstaller_dist)
        if member.replace("\\", "/").endswith("/licenses/COPYING.txt")
    ]
    if len(candidates) != 1:
        raise RuntimeError(
            "expected one PyInstaller licenses/COPYING.txt, "
            f"found {[member for member, _ in candidates]}"
        )
    source_member, source = candidates[0]
    payload = source.read_bytes()
    digest = hashlib.sha256(payload).hexdigest()
    if digest != PYINSTALLER_NOTICE_SHA256:
        raise RuntimeError(
            "PyInstaller bootloader notice bytes require review: "
            f"expected {PYINSTALLER_NOTICE_SHA256}, got {digest}"
        )
    try:
        notice_text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise RuntimeError("PyInstaller bootloader notice is not UTF-8") from exc
    missing_markers = [
        marker for marker in PYINSTALLER_EXCEPTION_MARKERS if marker not in notice_text
    ]
    if missing_markers:
        raise RuntimeError(
            "PyInstaller notice omitted Bootloader Exception terms: " + "; ".join(missing_markers)
        )

    destination = output / "build-components" / "pyinstaller" / "COPYING.txt"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    return {
        "name": "pyinstaller-bootloader",
        "source_distribution": PYINSTALLER_DISTRIBUTION,
        "source_version": PYINSTALLER_VERSION,
        "license_scope": "GPL-2.0-or-later WITH PyInstaller-Bootloader-exception",
        "source_member": source_member,
        "bundled_member": PYINSTALLER_NOTICE_MEMBER,
        "sha256": digest,
        "bytes": len(payload),
        "required_markers": list(PYINSTALLER_EXCEPTION_MARKERS),
    }


def prepare_notice_bundle(
    output: Path,
    *,
    root_license: Path,
    closure: dict[str, object] | None = None,
    build_closure: dict[str, object] | None = None,
    pyinstaller_dist=None,
    required_notice_tokens: dict[str, tuple[str, ...]] = REQUIRED_NOTICE_TOKENS,
) -> Path:
    """Stage closure notices and a deterministic, hash-bound JSON inventory."""

    if closure is None:
        closure = frozen_runtime_closure()
    _reject_copyleft(closure)
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    packages: list[dict[str, object]] = []
    for canonical_name, dist in sorted(closure.items()):
        declared_name = _declared_name(dist)
        if declared_name != canonical_name:
            raise RuntimeError(
                f"distribution name drift: requested {canonical_name}, "
                f"metadata declares {declared_name}"
            )
        sources = _notice_sources(dist)
        if (
            not sources
            and canonical_name in FIRST_PARTY_MIT_FALLBACK
            and "MIT" in license_evidence(dist)
        ):
            if not root_license.is_file():
                raise RuntimeError(f"first-party MIT license is missing: {root_license}")
            sources = [("workspace:LICENSE", root_license)]
        if not sources:
            raise RuntimeError(
                f"{canonical_name} has no concrete license/NOTICE file in its "
                "installed distribution"
            )

        notices: list[dict[str, object]] = []
        package_dir = output / canonical_name
        for index, (source_member, source) in enumerate(sources, start=1):
            safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", source.name).strip("-")
            destination = package_dir / f"{index:03d}-{safe_name or 'NOTICE'}"
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            payload = destination.read_bytes()
            notices.append(
                {
                    "source_member": source_member,
                    "bundled_member": (
                        f"{NOTICE_BUNDLE_MEMBER}/{canonical_name}/{destination.name}"
                    ),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "bytes": len(payload),
                }
            )

        packages.append(
            {
                "name": canonical_name,
                "version": str(dist.version),
                "license_evidence": license_evidence(dist),
                "notices": notices,
            }
        )

    package_index = {str(package["name"]): package for package in packages}
    for required_name, required_tokens in sorted(required_notice_tokens.items()):
        package = package_index.get(required_name)
        if package is None:
            raise RuntimeError(
                f"required frozen dependency is absent from notice closure: {required_name}"
            )
        notice_members = [
            str(notice["bundled_member"]).lower()
            for notice in package["notices"]  # type: ignore[index]
        ]
        for token in required_tokens:
            if not any(token.lower() in member for member in notice_members):
                raise RuntimeError(f"{required_name} did not provide required {token} notice")

    embedded_build_components = [
        _stage_pyinstaller_bootloader_notice(
            output,
            pyinstaller_dist=pyinstaller_dist,
        )
    ]
    inventory = {
        "schema_version": 2,
        "runtime_roots": list(FROZEN_RUNTIME_ROOTS),
        "packages": packages,
        "build_only_packages": _build_only_packages(
            closure,
            build_closure=build_closure,
        ),
        "embedded_build_components": embedded_build_components,
    }
    (output / NOTICE_INVENTORY_NAME).write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output
