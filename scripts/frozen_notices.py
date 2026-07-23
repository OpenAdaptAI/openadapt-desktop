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

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

ROOT_DISTRIBUTION = "openadapt-desktop"
ROOT_EXTRAS = frozenset({"build"})
NOTICE_BUNDLE_MEMBER = "third_party/python"
NOTICE_INVENTORY_NAME = "NOTICE-INVENTORY.json"

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
    root_extras: Iterable[str] = ROOT_EXTRAS,
    distribution_getter: Callable[[str], object] = distribution,
) -> dict[str, object]:
    """Resolve the installed dependency closure for the selected root extras."""

    environment = default_environment()
    pending: deque[tuple[str, frozenset[str]]] = deque([(root_name, frozenset(root_extras))])
    resolved: dict[str, object] = {}
    processed_contexts: dict[str, set[str]] = {}

    while pending:
        requested_name, requested_extras = pending.popleft()
        canonical_name = canonicalize_name(requested_name)
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
    return canonicalize_name(str(name))


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


def prepare_notice_bundle(
    output: Path,
    *,
    root_license: Path,
    closure: dict[str, object] | None = None,
    required_notice_tokens: dict[str, tuple[str, ...]] = REQUIRED_NOTICE_TOKENS,
) -> Path:
    """Stage closure notices and a deterministic, hash-bound JSON inventory."""

    closure = closure or dependency_closure()
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

    inventory = {
        "schema_version": 1,
        "root_distribution": ROOT_DISTRIBUTION,
        "root_extras": sorted(ROOT_EXTRAS),
        "packages": packages,
    }
    (output / NOTICE_INVENTORY_NAME).write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return output
