#!/usr/bin/env python3
"""Build and validate the closed Desktop Production release contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

REPOSITORY = "OpenAdaptAI/openadapt-desktop"
REPOSITORY_ID = "1171291730"
TARGET = "desktop"
CLAIM_SCOPE = "production_desktop"
ARTIFACT_INVENTORY_SCHEMA = "openadapt.production-release-artifact-inventory/v1"
PLATFORM_VERIFICATION_SCHEMA = "openadapt.desktop-platform-verification/v1"
IMMUTABLE_RELEASES_DOMAIN = b"OpenAdapt production immutable releases response v1\0"
TAG_REF_STATE_DOMAIN = b"OpenAdapt production release tag ref state v1\0"
PLATFORM_VERIFICATION_MEDIA_TYPE = (
    "application/vnd.openadapt.desktop-platform-verification+json;version=1"
)
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
DECIMAL_ID = re.compile(r"^[1-9][0-9]*$")
ARCHITECTURES = {
    "linux": {"x86_64"},
    "macos": {"arm64", "x86_64"},
    "windows": {"x86_64"},
}


@dataclass(frozen=True)
class ArtifactSpec:
    """One exact public asset in the Desktop package release."""

    name: str
    kind: str
    media_type: str
    publish_destinations: tuple[str, ...]


def _native_prefix(version: str, platform: str, architecture: str) -> str:
    return f"OpenAdapt-Desktop-v{version}-{platform}-{architecture}"


def artifact_specs(version: str) -> tuple[ArtifactSpec, ...]:
    """Return the exact fourteen-asset Production profile for one version."""

    if VERSION.fullmatch(version) is None:
        raise ValueError("Desktop release version must be X.Y.Z")
    github = ("github-release",)
    package = ("github-release", "pypi")
    values = (
        ArtifactSpec(
            f"{_native_prefix(version, 'linux', 'x86_64')}.AppImage",
            "linux-appimage",
            "application/vnd.appimage",
            github,
        ),
        ArtifactSpec(
            f"{_native_prefix(version, 'linux', 'x86_64')}.deb",
            "linux-deb",
            "application/vnd.debian.binary-package",
            github,
        ),
        ArtifactSpec(
            f"{_native_prefix(version, 'macos', 'arm64')}.dmg",
            "macos-dmg-arm64",
            "application/x-apple-diskimage",
            github,
        ),
        ArtifactSpec(
            f"{_native_prefix(version, 'macos', 'x86_64')}.dmg",
            "macos-dmg-x86-64",
            "application/x-apple-diskimage",
            github,
        ),
        ArtifactSpec(
            f"openadapt_desktop-{version}.tar.gz",
            "python-sdist",
            "application/gzip",
            package,
        ),
        ArtifactSpec(
            f"openadapt_desktop-{version}-py3-none-any.whl",
            "python-wheel",
            "application/zip",
            package,
        ),
        ArtifactSpec(
            "SHA256SUMS",
            "release-checksums",
            "text/plain",
            github,
        ),
        ArtifactSpec(
            f"{_native_prefix(version, 'linux', 'x86_64')}-verification.json",
            "verification-metadata-linux-x86-64",
            PLATFORM_VERIFICATION_MEDIA_TYPE,
            github,
        ),
        ArtifactSpec(
            f"{_native_prefix(version, 'macos', 'arm64')}-verification.json",
            "verification-metadata-macos-arm64",
            PLATFORM_VERIFICATION_MEDIA_TYPE,
            github,
        ),
        ArtifactSpec(
            f"{_native_prefix(version, 'macos', 'x86_64')}-verification.json",
            "verification-metadata-macos-x86-64",
            PLATFORM_VERIFICATION_MEDIA_TYPE,
            github,
        ),
        ArtifactSpec(
            f"{_native_prefix(version, 'windows', 'x86_64')}-verification.json",
            "verification-metadata-windows-x86-64",
            PLATFORM_VERIFICATION_MEDIA_TYPE,
            github,
        ),
        ArtifactSpec(
            f"{_native_prefix(version, 'windows', 'x86_64')}.msi",
            "windows-msi",
            "application/x-msi",
            github,
        ),
        ArtifactSpec(
            f"{_native_prefix(version, 'windows', 'x86_64')}-setup.exe",
            "windows-nsis",
            "application/vnd.microsoft.portable-executable",
            github,
        ),
        ArtifactSpec(
            f"OpenAdapt-Desktop-v{version}.cyclonedx.json",
            "cyclonedx-sbom",
            "application/vnd.cyclonedx+json",
            github,
        ),
    )
    return tuple(sorted(values, key=lambda item: (item.kind, item.name)))


def expected_asset_names(version: str) -> set[str]:
    return {item.name for item in artifact_specs(version)}


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _closed(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise ValueError(f"{label} must contain exactly {sorted(fields)}; got {actual}")
    return value


def _regular_file(path: Path, label: str) -> Path:
    if not path.is_file() or path.is_symlink() or path.stat().st_size <= 0:
        raise ValueError(f"{label} must be a non-empty regular file")
    return path


def build_artifact_inventory(directory: Path, *, version: str) -> dict[str, Any]:
    """Hash the exact fourteen staged assets into the central inventory schema."""

    specs = artifact_specs(version)
    actual = {path.name for path in directory.iterdir()}
    expected = {item.name for item in specs}
    if actual != expected:
        raise ValueError(
            "Desktop Production release assets differ from the exact profile: "
            f"missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}"
        )
    artifacts: list[dict[str, Any]] = []
    for spec in specs:
        path = _regular_file(directory / spec.name, f"release asset {spec.name}")
        artifacts.append(
            {
                "name": spec.name,
                "kind": spec.kind,
                "sha256": _digest(path),
                "size_bytes": path.stat().st_size,
                "media_type": spec.media_type,
                "publish_destinations": list(spec.publish_destinations),
            }
        )
    return {
        "schema_version": ARTIFACT_INVENTORY_SCHEMA,
        "target": TARGET,
        "claim_scope": CLAIM_SCOPE,
        "artifacts": artifacts,
    }


def write_artifact_inventory(path: Path, value: Mapping[str, Any]) -> Path:
    if path.exists():
        raise ValueError("artifact inventory output already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def validate_immutable_releases_response(value: Any) -> dict[str, Any]:
    """Validate the exact GitHub immutable-releases API response."""

    response = _closed(
        value,
        {"enabled", "enforced_by_owner"},
        "immutable releases response",
    )
    if response["enabled"] is not True:
        raise ValueError("immutable releases must be enabled")
    if not isinstance(response["enforced_by_owner"], bool):
        raise ValueError("immutable releases enforced_by_owner must be boolean")
    return response


def immutable_releases_digest(value: Any) -> str:
    response = validate_immutable_releases_response(value)
    return "sha256:" + hashlib.sha256(IMMUTABLE_RELEASES_DOMAIN + _canonical(response)).hexdigest()


def validate_tag_ref_state(value: Any, *, tag: str) -> dict[str, Any]:
    """Require proof that the prospective immutable release tag does not exist."""

    if VERSION.fullmatch(tag.removeprefix("v")) is None or not tag.startswith("v"):
        raise ValueError("release tag must be vX.Y.Z")
    state = _closed(value, {"ref", "exists"}, "tag ref state")
    if state != {"ref": f"refs/tags/{tag}", "exists": False}:
        raise ValueError("prospective release tag already exists or differs")
    return state


def tag_ref_state_digest(value: Any, *, tag: str) -> str:
    state = validate_tag_ref_state(value, tag=tag)
    return "sha256:" + hashlib.sha256(TAG_REF_STATE_DOMAIN + _canonical(state)).hexdigest()


def _validate_release(value: Any, *, version: str) -> dict[str, Any]:
    release = _closed(
        value,
        {"repository", "repository_id", "source_commit", "version", "tag"},
        "platform verification release",
    )
    expected = {
        "repository": REPOSITORY,
        "repository_id": REPOSITORY_ID,
        "version": version,
        "tag": f"v{version}",
    }
    if any(release.get(key) != item for key, item in expected.items()):
        raise ValueError("platform verification release identity differs")
    if COMMIT.fullmatch(str(release.get("source_commit") or "")) is None:
        raise ValueError("platform verification source commit is invalid")
    return release


def _validate_build(value: Any, *, source_commit: str) -> dict[str, Any]:
    build = _closed(
        value,
        {
            "workflow",
            "workflow_ref",
            "workflow_commit",
            "event",
            "run_id",
            "run_attempt",
            "runner_environment",
            "install_verified",
            "launch_verified",
            "uninstall_verified",
            "embedded_flow_version",
        },
        "platform verification build",
    )
    expected = {
        "workflow": ".github/workflows/release.yml",
        "workflow_ref": (
            "OpenAdaptAI/openadapt-desktop/.github/workflows/release.yml@refs/heads/main"
        ),
        "workflow_commit": source_commit,
        "event": "workflow_dispatch",
        "runner_environment": "github-hosted",
        "install_verified": True,
        "launch_verified": True,
        "uninstall_verified": True,
    }
    if any(build.get(key) != item for key, item in expected.items()):
        raise ValueError("platform verification build identity or result differs")
    if not isinstance(build.get("run_id"), int) or build["run_id"] <= 0:
        raise ValueError("platform verification run_id is invalid")
    if not isinstance(build.get("run_attempt"), int) or build["run_attempt"] <= 0:
        raise ValueError("platform verification run_attempt is invalid")
    flow_version = build.get("embedded_flow_version")
    if not isinstance(flow_version, str) or not flow_version:
        raise ValueError("platform verification embedded Flow version is invalid")
    return build


def _validate_artifact_bindings(
    value: Any,
    *,
    version: str,
    platform: str,
    architecture: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("platform verification artifacts must be a non-empty list")
    specs = {
        item.name: item
        for item in artifact_specs(version)
        if item.kind.startswith(f"{platform}-") and "verification-metadata" not in item.kind
    }
    result: list[dict[str, Any]] = []
    for index, artifact_value in enumerate(value):
        artifact = _closed(
            artifact_value,
            {"name", "kind", "sha256", "size_bytes", "media_type"},
            f"platform verification artifact {index}",
        )
        spec = specs.get(str(artifact.get("name")))
        if spec is None:
            raise ValueError("platform verification names an artifact outside its platform")
        if (
            artifact.get("kind") != spec.kind
            or artifact.get("media_type") != spec.media_type
            or DIGEST.fullmatch(str(artifact.get("sha256") or "")) is None
            or not isinstance(artifact.get("size_bytes"), int)
            or artifact["size_bytes"] <= 0
        ):
            raise ValueError("platform verification artifact binding is invalid")
        result.append(artifact)
    expected_names = {name for name in specs if f"-{platform}-{architecture}" in name}
    if {item["name"] for item in result} != expected_names:
        raise ValueError("platform verification artifact set is incomplete")
    if result != sorted(result, key=lambda item: (item["kind"], item["name"])):
        raise ValueError("platform verification artifacts are not sorted")
    return result


def _valid_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or DIGEST.fullmatch(value) is None:
        raise ValueError(f"{label} must be a lowercase sha256 digest")
    return value


def _validate_native_verification(
    value: Any,
    *,
    platform: str,
    artifacts: list[dict[str, Any]],
) -> dict[str, Any]:
    if platform == "macos":
        verification = _closed(
            value,
            {"method", "signature", "notarization"},
            "macOS verification",
        )
        if verification["method"] != "apple-developer-id-notarization":
            raise ValueError("macOS verification method differs")
        signature = _closed(
            verification["signature"],
            {
                "status",
                "team_id",
                "signer_identity_sha256",
                "designated_requirement_sha256",
                "hardened_runtime",
            },
            "macOS signature verification",
        )
        if (
            signature["status"] != "valid"
            or signature["hardened_runtime"] is not True
            or not isinstance(signature["team_id"], str)
            or not signature["team_id"]
        ):
            raise ValueError("macOS signature verification result differs")
        _valid_digest(signature["signer_identity_sha256"], "macOS signer identity")
        _valid_digest(
            signature["designated_requirement_sha256"],
            "macOS designated requirement",
        )
        notarization = _closed(
            verification["notarization"],
            {"status", "ticket_stapled", "ticket_validated", "gatekeeper_assessment"},
            "macOS notarization verification",
        )
        if notarization != {
            "status": "accepted",
            "ticket_stapled": True,
            "ticket_validated": True,
            "gatekeeper_assessment": "accepted",
        }:
            raise ValueError("macOS notarization verification result differs")
        return verification

    if platform == "windows":
        verification = _closed(
            value,
            {"method", "file_digest_algorithm", "signatures"},
            "Windows verification",
        )
        if (
            verification["method"] != "authenticode"
            or verification["file_digest_algorithm"] != "sha256"
        ):
            raise ValueError("Windows verification method differs")
        signatures = verification["signatures"]
        if not isinstance(signatures, list):
            raise ValueError("Windows signatures must be a list")
        expected_names = [item["name"] for item in artifacts]
        observed_names = [
            item.get("artifact_name") for item in signatures if isinstance(item, dict)
        ]
        if observed_names != expected_names:
            raise ValueError("Windows signatures do not bind the exact artifact set")
        for index, signature_value in enumerate(signatures):
            signature = _closed(
                signature_value,
                {
                    "artifact_name",
                    "status",
                    "signer_certificate_sha256",
                    "signer_subject_sha256",
                    "timestamp_certificate_sha256",
                    "timestamp_subject_sha256",
                },
                f"Windows signature {index}",
            )
            if signature["status"] != "valid":
                raise ValueError("Windows Authenticode signature is not valid")
            for field in (
                "signer_certificate_sha256",
                "signer_subject_sha256",
                "timestamp_certificate_sha256",
                "timestamp_subject_sha256",
            ):
                _valid_digest(signature[field], f"Windows {field}")
        return verification

    verification = _closed(
        value,
        {
            "method",
            "oidc_issuer",
            "certificate_identity",
            "predicate_type",
            "build_type",
            "subjects",
        },
        "Linux provenance verification",
    )
    expected = {
        "method": "github-oidc-attestation",
        "oidc_issuer": "https://token.actions.githubusercontent.com",
        "certificate_identity": (
            "https://github.com/OpenAdaptAI/openadapt-desktop/.github/workflows/"
            "release.yml@refs/heads/main"
        ),
        "predicate_type": "https://slsa.dev/provenance/v1",
        "build_type": "https://actions.github.io/buildtypes/workflow/v1",
    }
    if any(verification.get(key) != item for key, item in expected.items()):
        raise ValueError("Linux provenance identity differs")
    subjects = verification["subjects"]
    if not isinstance(subjects, list):
        raise ValueError("Linux provenance subjects must be a list")
    expected_subjects = [{"name": item["name"], "sha256": item["sha256"]} for item in artifacts]
    if subjects != expected_subjects:
        raise ValueError("Linux provenance subjects differ from the artifact bindings")
    return verification


def validate_platform_verification(value: Any, *, version: str) -> dict[str, Any]:
    """Validate one closed, privacy-safe platform verification object."""

    document = _closed(
        value,
        {
            "schema_version",
            "release",
            "platform",
            "architecture",
            "artifacts",
            "build",
            "verification",
        },
        "platform verification",
    )
    if document["schema_version"] != PLATFORM_VERIFICATION_SCHEMA:
        raise ValueError("platform verification schema is not supported")
    platform = document["platform"]
    architecture = document["architecture"]
    if platform not in ARCHITECTURES or architecture not in ARCHITECTURES[platform]:
        raise ValueError("platform verification platform or architecture is invalid")
    release = _validate_release(document["release"], version=version)
    artifacts = _validate_artifact_bindings(
        document["artifacts"],
        version=version,
        platform=platform,
        architecture=architecture,
    )
    _validate_build(document["build"], source_commit=release["source_commit"])
    _validate_native_verification(document["verification"], platform=platform, artifacts=artifacts)
    return document


def _load(path: Path) -> Any:
    _regular_file(path, str(path))
    return json.loads(path.read_text(encoding="utf-8"))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory")
    inventory.add_argument("--directory", type=Path, required=True)
    inventory.add_argument("--version", required=True)
    inventory.add_argument("--output", type=Path, required=True)
    immutable = commands.add_parser("validate-immutable-releases")
    immutable.add_argument("--file", type=Path, required=True)
    immutable.add_argument("--github-output", type=Path)
    tag_state = commands.add_parser("validate-tag-ref-state")
    tag_state.add_argument("--file", type=Path, required=True)
    tag_state.add_argument("--tag", required=True)
    tag_state.add_argument("--github-output", type=Path)
    verification = commands.add_parser("validate-platform-verification")
    verification.add_argument("--file", type=Path, required=True)
    verification.add_argument("--version", required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "inventory":
            value = build_artifact_inventory(args.directory, version=args.version)
            write_artifact_inventory(args.output, value)
            print(args.output)
        elif args.command == "validate-immutable-releases":
            value = _load(args.file)
            response = validate_immutable_releases_response(value)
            digest = immutable_releases_digest(response)
            if args.github_output:
                with args.github_output.open("a", encoding="utf-8") as output:
                    output.write(
                        "immutable_releases=" + _canonical(response).decode("utf-8") + "\n"
                    )
                    output.write(f"immutable_releases_sha256={digest}\n")
            print(digest)
        elif args.command == "validate-tag-ref-state":
            value = _load(args.file)
            state = validate_tag_ref_state(value, tag=args.tag)
            digest = tag_ref_state_digest(state, tag=args.tag)
            if args.github_output:
                with args.github_output.open("a", encoding="utf-8") as output:
                    output.write("tag_ref_state=" + _canonical(state).decode("utf-8") + "\n")
                    output.write(f"tag_ref_state_sha256={digest}\n")
            print(digest)
        else:
            validate_platform_verification(_load(args.file), version=args.version)
            print(f"Validated {args.file}.")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
