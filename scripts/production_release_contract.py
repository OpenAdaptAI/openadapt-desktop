#!/usr/bin/env python3
"""Build and validate the closed Desktop Production release contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
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
TAG_RULESETS_DOMAIN = b"OpenAdapt production release tag rulesets v1\0"
STAGING_DOMAIN = b"OpenAdapt production release staging evidence v1\0"
ARTIFACT_INVENTORY_DOMAIN = b"OpenAdapt production release artifact inventory v1\0"
TAG_ADMISSION_REFERENCE_DOMAIN = b"OpenAdapt production release tag admission reference v1\0"
EVIDENCE_REGISTRY_ENTRY_DOMAIN = b"OpenAdapt production evidence registry entry v1\0"
PLATFORM_VERIFICATION_MEDIA_TYPE = (
    "application/vnd.openadapt.desktop-platform-verification+json;version=1"
)
VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
COMMIT = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
DECIMAL_ID = re.compile(r"^[1-9][0-9]*$")
TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
APPLE_TEAM_ID = re.compile(r"^[A-Z0-9]{10}$")
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


def validate_artifact_inventory(value: Any, *, version: str) -> dict[str, Any]:
    inventory = _closed(
        value,
        {"schema_version", "target", "claim_scope", "artifacts"},
        "Desktop artifact inventory",
    )
    if (
        inventory["schema_version"] != ARTIFACT_INVENTORY_SCHEMA
        or inventory["target"] != TARGET
        or inventory["claim_scope"] != CLAIM_SCOPE
    ):
        raise ValueError("Desktop artifact inventory identity differs")
    artifacts = inventory["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != 14:
        raise ValueError("Desktop artifact inventory must contain exactly fourteen assets")
    specs = {item.name: item for item in artifact_specs(version)}
    names: set[str] = set()
    for index, artifact_value in enumerate(artifacts):
        artifact = _closed(
            artifact_value,
            {
                "name",
                "kind",
                "sha256",
                "size_bytes",
                "media_type",
                "publish_destinations",
            },
            f"Desktop artifact inventory item {index}",
        )
        spec = specs.get(str(artifact["name"]))
        if (
            spec is None
            or artifact["name"] in names
            or artifact["kind"] != spec.kind
            or artifact["media_type"] != spec.media_type
            or artifact["publish_destinations"] != list(spec.publish_destinations)
            or DIGEST.fullmatch(str(artifact["sha256"])) is None
            or not isinstance(artifact["size_bytes"], int)
            or isinstance(artifact["size_bytes"], bool)
            or artifact["size_bytes"] <= 0
        ):
            raise ValueError("Desktop artifact inventory item differs from the profile")
        names.add(artifact["name"])
    if names != set(specs):
        raise ValueError("Desktop artifact inventory is incomplete")
    if artifacts != sorted(
        artifacts, key=lambda item: (item["kind"], item["name"], item["sha256"])
    ):
        raise ValueError("Desktop artifact inventory is not canonically sorted")
    return inventory


def artifact_inventory_digest(value: Any, *, version: str) -> str:
    inventory = validate_artifact_inventory(value, version=version)
    projection = {
        "target": inventory["target"],
        "claim_scope": inventory["claim_scope"],
        "artifacts": inventory["artifacts"],
    }
    return (
        "sha256:" + hashlib.sha256(ARTIFACT_INVENTORY_DOMAIN + _canonical(projection)).hexdigest()
    )


def _validate_release_checksums(directory: Path, *, version: str) -> None:
    expected_names = expected_asset_names(version) - {"SHA256SUMS"}
    expected = b"".join(
        f"{_digest(directory / name).removeprefix('sha256:')}  {name}\n".encode("utf-8")
        for name in sorted(expected_names)
    )
    actual = _regular_file(directory / "SHA256SUMS", "release checksums").read_bytes()
    if actual != expected:
        raise ValueError("Desktop SHA256SUMS does not bind the exact release bytes")


def _validate_release_sbom(directory: Path, *, version: str) -> None:
    path = _regular_file(
        directory / f"OpenAdapt-Desktop-v{version}.cyclonedx.json",
        "Desktop CycloneDX SBOM",
    )
    try:
        value = json.loads(path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Desktop CycloneDX SBOM is not UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("Desktop CycloneDX SBOM must be an object")
    metadata = value.get("metadata")
    tools = metadata.get("tools") if isinstance(metadata, dict) else None
    components = value.get("components")
    if (
        value.get("bomFormat") != "CycloneDX"
        or not isinstance(value.get("specVersion"), str)
        or re.fullmatch(r"1\.[4-9]", value["specVersion"]) is None
        or value.get("version") != 1
        or not isinstance(tools, (dict, list))
        or not tools
        or not isinstance(components, list)
        or not components
    ):
        raise ValueError("Desktop CycloneDX SBOM identity or inventory is invalid")
    if any(
        not isinstance(component, dict)
        or not isinstance(component.get("name"), str)
        or not component["name"].strip()
        for component in components
    ):
        raise ValueError("Desktop CycloneDX SBOM contains an unnamed component")


def validate_release_asset_contents(
    directory: Path,
    *,
    version: str,
    source_commit: str,
    embedded_flow_version: str,
) -> None:
    """Validate the public metadata files against the exact release bytes."""

    if COMMIT.fullmatch(source_commit) is None:
        raise ValueError("Desktop release source commit is invalid")
    if VERSION.fullmatch(embedded_flow_version) is None:
        raise ValueError("Desktop embedded Flow version is invalid")
    specs = {item.kind: item for item in artifact_specs(version)}
    profiles = (
        ("linux", "x86_64", "verification-metadata-linux-x86-64"),
        ("macos", "arm64", "verification-metadata-macos-arm64"),
        ("macos", "x86_64", "verification-metadata-macos-x86-64"),
        ("windows", "x86_64", "verification-metadata-windows-x86-64"),
    )
    for platform, architecture, kind in profiles:
        path = _regular_file(directory / specs[kind].name, f"{platform} verification metadata")
        raw = path.read_bytes()
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"{platform} verification metadata is not UTF-8 JSON") from exc
        document = validate_platform_verification(value, version=version)
        if raw != _canonical(document) + b"\n":
            raise ValueError(f"{platform} verification metadata is not canonical JSON plus LF")
        if (
            document["platform"] != platform
            or document["architecture"] != architecture
            or document["release"]["source_commit"] != source_commit
            or document["build"]["embedded_flow_version"] != embedded_flow_version
        ):
            raise ValueError(f"{platform} verification metadata release identity differs")
        for artifact in document["artifacts"]:
            artifact_path = _regular_file(
                directory / artifact["name"],
                f"verified {platform} artifact {artifact['name']}",
            )
            if (
                artifact_path.stat().st_size != artifact["size_bytes"]
                or _digest(artifact_path) != artifact["sha256"]
            ):
                raise ValueError(f"{platform} verification metadata artifact bytes differ")
    _validate_release_sbom(directory, version=version)
    _validate_release_checksums(directory, version=version)


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


def _ruleset(value: Any, *, role: str) -> dict[str, Any]:
    ruleset = _closed(
        value,
        {
            "schema_version",
            "role",
            "repository",
            "repository_id",
            "ruleset_id",
            "name",
            "target",
            "enforcement",
            "bypass_actors",
            "conditions",
            "rules",
        },
        f"{role} tag ruleset",
    )
    if (
        ruleset["schema_version"] != "openadapt.production-release-tag-ruleset/v1"
        or ruleset["role"] != role
        or ruleset["repository"] != REPOSITORY
        or ruleset["repository_id"] != REPOSITORY_ID
        or DECIMAL_ID.fullmatch(str(ruleset["ruleset_id"])) is None
        or ruleset["target"] != "tag"
        or ruleset["enforcement"] != "active"
    ):
        raise ValueError(f"{role} tag ruleset identity differs")
    expected_name = {
        "creation_authority": "OpenAdapt policy: release tag creation",
        "immutability": "OpenAdapt policy: immutable release tags",
    }[role]
    expected_actors = (
        [
            {
                "actor_id": "4730708",
                "actor_type": "Integration",
                "bypass_mode": "always",
            }
        ]
        if role == "creation_authority"
        else []
    )
    expected_rules = (
        [{"type": "creation"}]
        if role == "creation_authority"
        else [
            {"type": "deletion"},
            {"type": "non_fast_forward"},
            {
                "type": "update",
                "parameters": {"update_allows_fetch_and_merge": False},
            },
        ]
    )
    conditions = _closed(ruleset["conditions"], {"ref_name"}, "tag ruleset conditions")
    ref_name = _closed(conditions["ref_name"], {"include", "exclude"}, "tag ref conditions")
    if (
        ruleset["name"] != expected_name
        or ruleset["bypass_actors"] != expected_actors
        or ref_name != {"include": ["refs/tags/v*"], "exclude": []}
        or ruleset["rules"] != expected_rules
    ):
        raise ValueError(f"{role} tag ruleset policy differs")
    return ruleset


def validate_tag_rulesets(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("tag rulesets must contain creation and immutability rulesets")
    _ruleset(value[0], role="creation_authority")
    _ruleset(value[1], role="immutability")
    return value


def tag_rulesets_digest(value: Any) -> str:
    rulesets = validate_tag_rulesets(value)
    return "sha256:" + hashlib.sha256(TAG_RULESETS_DOMAIN + _canonical(rulesets)).hexdigest()


def normalize_tag_rulesets(creation: Any, immutability: Any) -> list[dict[str, Any]]:
    """Normalize the two full GitHub ruleset API responses for central staging."""

    values = []
    for role, raw_value in (
        ("creation_authority", creation),
        ("immutability", immutability),
    ):
        if not isinstance(raw_value, dict):
            raise ValueError(f"{role} GitHub ruleset response must be an object")
        try:
            actors = [
                {
                    "actor_id": str(item["actor_id"]),
                    "actor_type": item["actor_type"],
                    "bypass_mode": item["bypass_mode"],
                }
                for item in raw_value["bypass_actors"]
            ]
            values.append(
                {
                    "schema_version": "openadapt.production-release-tag-ruleset/v1",
                    "role": role,
                    "repository": REPOSITORY,
                    "repository_id": REPOSITORY_ID,
                    "ruleset_id": str(raw_value["id"]),
                    "name": raw_value["name"],
                    "target": raw_value["target"],
                    "enforcement": raw_value["enforcement"],
                    "bypass_actors": actors,
                    "conditions": raw_value["conditions"],
                    "rules": raw_value["rules"],
                }
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"{role} GitHub ruleset response is incomplete") from exc
    return validate_tag_rulesets(values)


def _timestamp(value: str) -> str:
    if TIMESTAMP.fullmatch(value) is None:
        raise ValueError("staging observed_at must be an exact UTC timestamp")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError("staging observed_at is not a calendar timestamp") from exc
    return value


def _release_api_asset_map(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("draft release assets must be a list")
    result: dict[str, dict[str, Any]] = {}
    ids: set[str] = set()
    for index, asset in enumerate(value):
        if not isinstance(asset, dict):
            raise ValueError(f"draft release asset {index} must be an object")
        try:
            name = asset["name"]
            asset_id = str(asset["id"])
            uploader = asset["uploader"]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"draft release asset {index} is incomplete") from exc
        if (
            not isinstance(name, str)
            or not name
            or name in result
            or DECIMAL_ID.fullmatch(asset_id) is None
            or asset_id in ids
        ):
            raise ValueError("draft release asset names and ids must be unique and valid")
        if (
            asset.get("state") != "uploaded"
            or not isinstance(uploader, dict)
            or str(uploader.get("id")) != "321543906"
            or uploader.get("login") != "openadapt-release[bot]"
        ):
            raise ValueError("draft release asset is not uploaded by the release App")
        result[name] = asset
        ids.add(asset_id)
    return result


def build_publication_staging(
    release_api: Any,
    *,
    directory: Path,
    version: str,
    source_commit: str,
    embedded_flow_version: str,
    immutable_releases: Any,
    tag_rulesets: Any,
    tag_ref_state: Any,
    observed_at: str,
) -> dict[str, Any]:
    """Build exact central staging evidence from one complete App draft."""

    if COMMIT.fullmatch(source_commit) is None:
        raise ValueError("staging source commit is invalid")
    if not isinstance(release_api, dict):
        raise ValueError("draft release API response must be an object")
    tag = f"v{version}"
    try:
        release_id = str(release_api["id"])
        author = release_api["author"]
    except (KeyError, TypeError) as exc:
        raise ValueError("draft release API response is incomplete") from exc
    if (
        DECIMAL_ID.fullmatch(release_id) is None
        or release_api.get("tag_name") != tag
        or release_api.get("target_commitish") != source_commit
        or release_api.get("draft") is not True
        or release_api.get("prerelease") is not False
        or not isinstance(author, dict)
        or str(author.get("id")) != "321543906"
        or author.get("login") != "openadapt-release[bot]"
    ):
        raise ValueError("draft release identity, state, or App author differs")

    inventory = build_artifact_inventory(directory, version=version)
    release_assets = _release_api_asset_map(release_api.get("assets"))
    inventory_names = {item["name"] for item in inventory["artifacts"]}
    if set(release_assets) != inventory_names:
        raise ValueError("draft release assets differ from the exact local inventory")
    staged_assets = []
    for artifact in inventory["artifacts"]:
        remote = release_assets[artifact["name"]]
        if (
            remote.get("digest") != artifact["sha256"]
            or remote.get("size") != artifact["size_bytes"]
        ):
            raise ValueError("draft release asset bytes differ from the local inventory")
        staged_assets.append(
            {
                "asset_id": str(remote["id"]),
                **artifact,
                "uploader_id": "321543906",
                "uploader_login": "openadapt-release[bot]",
            }
        )
    validate_release_asset_contents(
        directory,
        version=version,
        source_commit=source_commit,
        embedded_flow_version=embedded_flow_version,
    )
    staged_assets.sort(key=lambda item: (item["name"], item["asset_id"]))
    immutable = validate_immutable_releases_response(immutable_releases)
    rulesets = validate_tag_rulesets(tag_rulesets)
    state = validate_tag_ref_state(tag_ref_state, tag=tag)
    staging = {
        "schema_version": "openadapt.production-release-staging-evidence/v1",
        "repository": REPOSITORY,
        "repository_id": REPOSITORY_ID,
        "draft_release_id": release_id,
        "tag": tag,
        "target_commitish": source_commit,
        "draft": True,
        "prerelease": False,
        "release_app_id": "4730708",
        "release_app_installation_id": "156835568",
        "release_app_bot_user_id": "321543906",
        "release_author_login": "openadapt-release[bot]",
        "assets": staged_assets,
        "immutable_releases": immutable,
        "immutable_releases_sha256": immutable_releases_digest(immutable),
        "tag_rulesets": rulesets,
        "tag_rulesets_sha256": tag_rulesets_digest(rulesets),
        "tag_ref_state": state,
        "tag_ref_state_sha256": tag_ref_state_digest(state, tag=tag),
        "observed_at": _timestamp(observed_at),
    }
    return validate_publication_staging(staging, version=version)


def staging_digest(value: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(STAGING_DOMAIN + _canonical(value)).hexdigest()


def validate_publication_staging(
    value: Any,
    *,
    version: str,
    expected_source_commit: str | None = None,
    expected_draft_release_id: str | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    """Validate the closed central staging output before a release effect."""

    staging = _closed(
        value,
        {
            "schema_version",
            "repository",
            "repository_id",
            "draft_release_id",
            "tag",
            "target_commitish",
            "draft",
            "prerelease",
            "release_app_id",
            "release_app_installation_id",
            "release_app_bot_user_id",
            "release_author_login",
            "assets",
            "immutable_releases",
            "immutable_releases_sha256",
            "tag_rulesets",
            "tag_rulesets_sha256",
            "tag_ref_state",
            "tag_ref_state_sha256",
            "observed_at",
        },
        "publication staging",
    )
    draft_release_id = staging["draft_release_id"]
    source_commit = staging["target_commitish"]
    if (
        staging["schema_version"] != "openadapt.production-release-staging-evidence/v1"
        or staging["repository"] != REPOSITORY
        or staging["repository_id"] != REPOSITORY_ID
        or not isinstance(draft_release_id, str)
        or DECIMAL_ID.fullmatch(draft_release_id) is None
        or staging["tag"] != f"v{version}"
        or not isinstance(source_commit, str)
        or COMMIT.fullmatch(source_commit) is None
        or staging["draft"] is not True
        or staging["prerelease"] is not False
        or staging["release_app_id"] != "4730708"
        or staging["release_app_installation_id"] != "156835568"
        or staging["release_app_bot_user_id"] != "321543906"
        or staging["release_author_login"] != "openadapt-release[bot]"
    ):
        raise ValueError("publication staging identity differs")
    if expected_source_commit is not None and source_commit != expected_source_commit:
        raise ValueError("publication staging source commit differs")
    if expected_draft_release_id is not None and draft_release_id != expected_draft_release_id:
        raise ValueError("publication staging draft release id differs")

    staged_assets = staging["assets"]
    if not isinstance(staged_assets, list) or len(staged_assets) != 14:
        raise ValueError("publication staging must contain exactly fourteen assets")
    inventory_artifacts = []
    asset_ids: set[str] = set()
    for index, staged_value in enumerate(staged_assets):
        staged = _closed(
            staged_value,
            {
                "asset_id",
                "name",
                "kind",
                "sha256",
                "size_bytes",
                "media_type",
                "publish_destinations",
                "uploader_id",
                "uploader_login",
            },
            f"publication staging asset {index}",
        )
        asset_id = staged["asset_id"]
        if (
            not isinstance(asset_id, str)
            or DECIMAL_ID.fullmatch(asset_id) is None
            or asset_id in asset_ids
            or staged["uploader_id"] != "321543906"
            or staged["uploader_login"] != "openadapt-release[bot]"
        ):
            raise ValueError("publication staging asset identity differs")
        asset_ids.add(asset_id)
        inventory_artifacts.append(
            {
                key: staged[key]
                for key in (
                    "name",
                    "kind",
                    "sha256",
                    "size_bytes",
                    "media_type",
                    "publish_destinations",
                )
            }
        )
    if staged_assets != sorted(
        staged_assets,
        key=lambda item: (item["name"], item["asset_id"]),
    ):
        raise ValueError("publication staging assets are not canonically sorted")
    inventory_artifacts.sort(key=lambda item: (item["kind"], item["name"], item["sha256"]))
    validate_artifact_inventory(
        {
            "schema_version": ARTIFACT_INVENTORY_SCHEMA,
            "target": TARGET,
            "claim_scope": CLAIM_SCOPE,
            "artifacts": inventory_artifacts,
        },
        version=version,
    )

    validate_immutable_releases_response(staging["immutable_releases"])
    if staging["immutable_releases_sha256"] != immutable_releases_digest(
        staging["immutable_releases"]
    ):
        raise ValueError("publication staging immutable-releases digest differs")
    validate_tag_rulesets(staging["tag_rulesets"])
    if staging["tag_rulesets_sha256"] != tag_rulesets_digest(staging["tag_rulesets"]):
        raise ValueError("publication staging tag-rulesets digest differs")
    validate_tag_ref_state(staging["tag_ref_state"], tag=staging["tag"])
    if staging["tag_ref_state_sha256"] != tag_ref_state_digest(
        staging["tag_ref_state"], tag=staging["tag"]
    ):
        raise ValueError("publication staging tag-ref digest differs")
    _timestamp(staging["observed_at"])
    digest = staging_digest(staging)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("publication staging digest differs")
    return staging


def validate_publication_staging_bytes(
    raw: bytes,
    *,
    version: str,
    expected_source_commit: str,
    expected_draft_release_id: str,
    expected_sha256: str,
) -> dict[str, Any]:
    """Require the reusable verifier's exact compact canonical staging JSON."""

    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("publication staging output is not UTF-8 JSON") from exc
    if raw != _canonical(value):
        raise ValueError("publication staging output is not exact compact canonical JSON")
    return validate_publication_staging(
        value,
        version=version,
        expected_source_commit=expected_source_commit,
        expected_draft_release_id=expected_draft_release_id,
        expected_sha256=expected_sha256,
    )


def validate_bound_release(
    release_api: Any,
    *,
    directory: Path,
    version: str,
    embedded_flow_version: str,
    publication_staging: Any,
    phase: str,
) -> dict[str, Any]:
    """Verify that a live draft or immutable release is the admitted release ID."""

    if phase not in {"draft", "published"}:
        raise ValueError("bound release phase must be draft or published")
    staging = validate_publication_staging(publication_staging, version=version)

    if not isinstance(release_api, dict):
        raise ValueError("live release API response must be an object")
    try:
        author = release_api["author"]
        release_id = str(release_api["id"])
    except (KeyError, TypeError) as exc:
        raise ValueError("live release API response is incomplete") from exc
    if (
        release_id != staging["draft_release_id"]
        or release_api.get("tag_name") != staging["tag"]
        or release_api.get("target_commitish") != staging["target_commitish"]
        or release_api.get("prerelease") is not False
        or not isinstance(author, dict)
        or str(author.get("id")) != "321543906"
        or author.get("login") != "openadapt-release[bot]"
    ):
        raise ValueError("live release identity differs from admitted staging")
    if phase == "draft":
        if release_api.get("draft") is not True or release_api.get("immutable") is not False:
            raise ValueError("admitted release is not the same mutable draft")
    elif release_api.get("draft") is not False or release_api.get("immutable") is not True:
        raise ValueError("published release is not immutable")

    inventory = build_artifact_inventory(directory, version=version)
    inventory_by_name = {item["name"]: item for item in inventory["artifacts"]}
    staged_assets = staging["assets"]
    if not isinstance(staged_assets, list):
        raise ValueError("publication staging assets must be a list")
    staged_by_name = {item.get("name"): item for item in staged_assets if isinstance(item, dict)}
    if len(staged_by_name) != len(staged_assets) or set(staged_by_name) != set(inventory_by_name):
        raise ValueError("publication staging asset inventory differs")
    remote_by_name = _release_api_asset_map(release_api.get("assets"))
    if set(remote_by_name) != set(inventory_by_name):
        raise ValueError("live release asset inventory differs")
    for name, artifact in inventory_by_name.items():
        staged_asset = staged_by_name[name]
        remote_asset = remote_by_name[name]
        expected_staged = {
            "asset_id": str(remote_asset["id"]),
            **artifact,
            "uploader_id": "321543906",
            "uploader_login": "openadapt-release[bot]",
        }
        if staged_asset != expected_staged:
            raise ValueError("live release asset differs from admitted staging")
        if (
            remote_asset.get("digest") != artifact["sha256"]
            or remote_asset.get("size") != artifact["size_bytes"]
        ):
            raise ValueError("live release bytes differ from admitted staging")
    validate_release_asset_contents(
        directory,
        version=version,
        source_commit=staging["target_commitish"],
        embedded_flow_version=embedded_flow_version,
    )
    return release_api


ADMISSION_REFERENCE_FIELDS = {
    "schema_version",
    "repository",
    "repository_id",
    "repository_owner_id",
    "registry_source_commit",
    "registry_revision",
    "registry_head_sha256",
    "registry_entry_sha256",
    "kind",
    "object_media_type",
    "object_path",
    "object_schema_version",
    "object_sha256",
    "semantic_identity_sha256",
    "size_bytes",
    "subject_sha256",
}


def validate_admission_reference(value: Any) -> dict[str, Any]:
    """Validate the closed lexical identity used by the annotated tag binding."""

    reference = _closed(value, ADMISSION_REFERENCE_FIELDS, "qualification-release reference")
    if (
        reference["schema_version"] != "openadapt.production-evidence-object-reference/v2"
        or reference["repository"] != "OpenAdaptAI/.github"
        or reference["repository_id"] != "858454062"
        or reference["repository_owner_id"] != "132681217"
        or reference["kind"] != "qualification-release"
        or reference["object_schema_version"] != "openadapt.qualification-release/v1"
        or reference["object_media_type"]
        != "application/vnd.openadapt.qualification-release+json;version=1"
        or reference["subject_sha256"] is not None
    ):
        raise ValueError("qualification-release reference identity differs")
    if COMMIT.fullmatch(str(reference["registry_source_commit"])) is None:
        raise ValueError("qualification-release registry commit is invalid")
    for field in (
        "registry_head_sha256",
        "registry_entry_sha256",
        "object_sha256",
        "semantic_identity_sha256",
    ):
        _valid_digest(reference[field], f"qualification-release {field}")
    if (
        not isinstance(reference["registry_revision"], int)
        or isinstance(reference["registry_revision"], bool)
        or reference["registry_revision"] <= 0
        or not isinstance(reference["size_bytes"], int)
        or isinstance(reference["size_bytes"], bool)
        or reference["size_bytes"] <= 0
    ):
        raise ValueError("qualification-release reference size or revision is invalid")
    digest_hex = reference["object_sha256"].removeprefix("sha256:")
    expected_path = (
        f"production-evidence/objects/sha256/{digest_hex[:2]}/"
        f"{digest_hex}.qualification-release.json"
    )
    if reference["object_path"] != expected_path:
        raise ValueError("qualification-release object path is not content addressed")
    entry_fields = {
        "kind",
        "object_media_type",
        "object_path",
        "object_schema_version",
        "object_sha256",
        "semantic_identity_sha256",
        "size_bytes",
        "subject_sha256",
    }
    entry = {field: reference[field] for field in entry_fields}
    expected_entry_digest = (
        "sha256:" + hashlib.sha256(EVIDENCE_REGISTRY_ENTRY_DOMAIN + _canonical(entry)).hexdigest()
    )
    if reference["registry_entry_sha256"] != expected_entry_digest:
        raise ValueError("qualification-release registry entry digest differs")
    return reference


def admission_reference_digest(value: Any) -> str:
    reference = validate_admission_reference(value)
    return (
        "sha256:"
        + hashlib.sha256(TAG_ADMISSION_REFERENCE_DOMAIN + _canonical(reference)).hexdigest()
    )


def build_tag_binding(
    admission_reference: Any,
    artifact_inventory: Any,
    *,
    version: str,
    verified_artifact_inventory_sha256: str,
) -> dict[str, Any]:
    reference = validate_admission_reference(admission_reference)
    local_digest = artifact_inventory_digest(artifact_inventory, version=version)
    if verified_artifact_inventory_sha256 != local_digest:
        raise ValueError("verified artifact inventory digest differs from local inventory")
    return {
        "schema_version": "openadapt.production-release-tag-binding/v1",
        "admission_reference": reference,
        "admission_reference_sha256": admission_reference_digest(reference),
        "artifact_inventory_sha256": local_digest,
    }


def tag_binding_bytes(value: Any) -> bytes:
    binding = _closed(
        value,
        {
            "schema_version",
            "admission_reference",
            "admission_reference_sha256",
            "artifact_inventory_sha256",
        },
        "production release tag binding",
    )
    if binding["schema_version"] != "openadapt.production-release-tag-binding/v1":
        raise ValueError("production release tag binding schema differs")
    reference = validate_admission_reference(binding["admission_reference"])
    if binding["admission_reference_sha256"] != admission_reference_digest(reference):
        raise ValueError("production release tag admission-reference digest differs")
    _valid_digest(
        binding["artifact_inventory_sha256"],
        "production release tag artifact inventory",
    )
    return _canonical(binding) + b"\n"


def validate_tag_binding_bytes(raw: bytes) -> dict[str, Any]:
    """Reject any annotated-tag message outside exact canonical JSON plus LF."""

    if not raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise ValueError("production release tag binding must end with one LF")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("production release tag binding is not UTF-8 JSON") from exc
    expected = tag_binding_bytes(value)
    if raw != expected:
        raise ValueError("production release tag binding is not exact canonical JSON plus LF")
    return value


def validate_tag_object(
    value: Any,
    *,
    expected_tag: str,
    expected_commit: str,
    expected_binding: Any,
) -> str:
    """Validate one GitHub annotated-tag object against the admitted binding."""

    if not isinstance(value, dict):
        raise ValueError("GitHub annotated tag response must be an object")
    tag_object_sha = value.get("sha")
    target = value.get("object")
    message = value.get("message")
    if (
        not isinstance(tag_object_sha, str)
        or COMMIT.fullmatch(tag_object_sha) is None
        or value.get("tag") != expected_tag
        or not isinstance(target, dict)
        or target.get("type") != "commit"
        or target.get("sha") != expected_commit
        or not isinstance(message, str)
    ):
        raise ValueError("GitHub annotated tag identity or target differs")
    binding = validate_tag_binding_bytes(message.encode("utf-8"))
    if binding != expected_binding:
        raise ValueError("GitHub annotated tag binding differs from the admitted binding")
    return tag_object_sha


def validate_tag_ref(
    value: Any,
    *,
    expected_tag: str,
    expected_tag_object_sha: str,
) -> None:
    """Require the public ref to resolve to the exact annotated-tag object."""

    if not isinstance(value, dict) or COMMIT.fullmatch(expected_tag_object_sha) is None:
        raise ValueError("GitHub annotated tag ref input is invalid")
    target = value.get("object")
    if (
        value.get("ref") != f"refs/tags/{expected_tag}"
        or not isinstance(target, dict)
        or target.get("type") != "tag"
        or target.get("sha") != expected_tag_object_sha
    ):
        raise ValueError("GitHub tag ref is not the exact annotated-tag object")


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
    if (
        not isinstance(build.get("run_id"), int)
        or isinstance(build["run_id"], bool)
        or build["run_id"] <= 0
    ):
        raise ValueError("platform verification run_id is invalid")
    if (
        not isinstance(build.get("run_attempt"), int)
        or isinstance(build["run_attempt"], bool)
        or build["run_attempt"] <= 0
    ):
        raise ValueError("platform verification run_attempt is invalid")
    flow_version = build.get("embedded_flow_version")
    if not isinstance(flow_version, str) or VERSION.fullmatch(flow_version) is None:
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
            or isinstance(artifact["size_bytes"], bool)
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
            or APPLE_TEAM_ID.fullmatch(signature["team_id"]) is None
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


def build_platform_verification(
    directory: Path,
    *,
    version: str,
    source_commit: str,
    platform: str,
    architecture: str,
    workflow_commit: str,
    run_id: int,
    run_attempt: int,
    embedded_flow_version: str,
    verification: Any,
) -> dict[str, Any]:
    """Build one public metadata object from verified native artifact bytes."""

    if platform not in ARCHITECTURES or architecture not in ARCHITECTURES[platform]:
        raise ValueError("platform verification platform or architecture is invalid")
    if source_commit != workflow_commit or COMMIT.fullmatch(source_commit) is None:
        raise ValueError("platform verification source and workflow commits must match")
    artifacts = []
    for spec in artifact_specs(version):
        if not spec.kind.startswith(f"{platform}-"):
            continue
        if f"-{platform}-{architecture}" not in spec.name:
            continue
        path = _regular_file(directory / spec.name, f"platform artifact {spec.name}")
        artifacts.append(
            {
                "name": spec.name,
                "kind": spec.kind,
                "sha256": _digest(path),
                "size_bytes": path.stat().st_size,
                "media_type": spec.media_type,
            }
        )
    artifacts.sort(key=lambda item: (item["kind"], item["name"]))
    document = {
        "schema_version": PLATFORM_VERIFICATION_SCHEMA,
        "release": {
            "repository": REPOSITORY,
            "repository_id": REPOSITORY_ID,
            "source_commit": source_commit,
            "version": version,
            "tag": f"v{version}",
        },
        "platform": platform,
        "architecture": architecture,
        "artifacts": artifacts,
        "build": {
            "workflow": ".github/workflows/release.yml",
            "workflow_ref": (
                "OpenAdaptAI/openadapt-desktop/.github/workflows/release.yml@refs/heads/main"
            ),
            "workflow_commit": workflow_commit,
            "event": "workflow_dispatch",
            "run_id": run_id,
            "run_attempt": run_attempt,
            "runner_environment": "github-hosted",
            "install_verified": True,
            "launch_verified": True,
            "uninstall_verified": True,
            "embedded_flow_version": embedded_flow_version,
        },
        "verification": verification,
    }
    return validate_platform_verification(document, version=version)


def write_platform_verification(path: Path, value: Any, *, version: str) -> Path:
    document = validate_platform_verification(value, version=version)
    if path.exists():
        raise ValueError("platform verification output already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(document) + b"\n")
    return path


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
    rulesets = commands.add_parser("rulesets")
    rulesets.add_argument("--creation", type=Path, required=True)
    rulesets.add_argument("--immutability", type=Path, required=True)
    rulesets.add_argument("--output", type=Path, required=True)
    staging = commands.add_parser("staging")
    staging.add_argument("--release-api", type=Path, required=True)
    staging.add_argument("--directory", type=Path, required=True)
    staging.add_argument("--version", required=True)
    staging.add_argument("--source-commit", required=True)
    staging.add_argument("--embedded-flow-version", required=True)
    staging.add_argument("--immutable-releases", type=Path, required=True)
    staging.add_argument("--tag-rulesets", type=Path, required=True)
    staging.add_argument("--tag-ref-state", type=Path, required=True)
    staging.add_argument("--observed-at", required=True)
    staging.add_argument("--output", type=Path, required=True)
    staging.add_argument("--github-output", type=Path)
    validate_staging = commands.add_parser("validate-staging")
    validate_staging.add_argument("--file", type=Path, required=True)
    validate_staging.add_argument("--version", required=True)
    validate_staging.add_argument("--source-commit", required=True)
    validate_staging.add_argument("--draft-release-id", required=True)
    validate_staging.add_argument("--expected-sha256", required=True)
    validate_release = commands.add_parser("validate-bound-release")
    validate_release.add_argument("--file", type=Path, required=True)
    validate_release.add_argument("--directory", type=Path, required=True)
    validate_release.add_argument("--version", required=True)
    validate_release.add_argument("--source-commit", required=True)
    validate_release.add_argument("--embedded-flow-version", required=True)
    validate_release.add_argument("--staging", type=Path, required=True)
    validate_release.add_argument("--staging-sha256", required=True)
    validate_release.add_argument("--draft-release-id", required=True)
    validate_release.add_argument("--phase", choices=("draft", "published"), required=True)
    tag_binding = commands.add_parser("tag-binding")
    tag_binding.add_argument("--admission-reference", type=Path, required=True)
    tag_binding.add_argument("--artifact-inventory", type=Path, required=True)
    tag_binding.add_argument("--version", required=True)
    tag_binding.add_argument("--verified-artifact-inventory-sha256", required=True)
    tag_binding.add_argument("--output", type=Path, required=True)
    validate_binding = commands.add_parser("validate-tag-binding")
    validate_binding.add_argument("--file", type=Path, required=True)
    validate_tag = commands.add_parser("validate-tag-object")
    validate_tag.add_argument("--file", type=Path, required=True)
    validate_tag.add_argument("--tag", required=True)
    validate_tag.add_argument("--source-commit", required=True)
    validate_tag.add_argument("--binding", type=Path, required=True)
    validate_ref = commands.add_parser("validate-tag-ref")
    validate_ref.add_argument("--file", type=Path, required=True)
    validate_ref.add_argument("--tag", required=True)
    validate_ref.add_argument("--tag-object-sha", required=True)
    verification = commands.add_parser("validate-platform-verification")
    verification.add_argument("--file", type=Path, required=True)
    verification.add_argument("--version", required=True)
    build_verification = commands.add_parser("platform-verification")
    build_verification.add_argument("--directory", type=Path, required=True)
    build_verification.add_argument("--version", required=True)
    build_verification.add_argument("--source-commit", required=True)
    build_verification.add_argument("--platform", choices=tuple(ARCHITECTURES), required=True)
    build_verification.add_argument("--architecture", choices=("arm64", "x86_64"), required=True)
    build_verification.add_argument("--workflow-commit", required=True)
    build_verification.add_argument("--run-id", type=int, required=True)
    build_verification.add_argument("--run-attempt", type=int, required=True)
    build_verification.add_argument("--embedded-flow-version", required=True)
    build_verification.add_argument("--verification", type=Path, required=True)
    build_verification.add_argument("--output", type=Path, required=True)
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
        elif args.command == "rulesets":
            value = normalize_tag_rulesets(_load(args.creation), _load(args.immutability))
            write_artifact_inventory(args.output, value)
            print(args.output)
        elif args.command == "staging":
            value = build_publication_staging(
                _load(args.release_api),
                directory=args.directory,
                version=args.version,
                source_commit=args.source_commit,
                embedded_flow_version=args.embedded_flow_version,
                immutable_releases=_load(args.immutable_releases),
                tag_rulesets=_load(args.tag_rulesets),
                tag_ref_state=_load(args.tag_ref_state),
                observed_at=args.observed_at,
            )
            write_artifact_inventory(args.output, value)
            digest = staging_digest(value)
            if args.github_output:
                with args.github_output.open("a", encoding="utf-8") as output:
                    output.write("publication_staging_json=" + _canonical(value).decode() + "\n")
                    output.write(f"publication_staging_sha256={digest}\n")
                    output.write(f"draft_release_id={value['draft_release_id']}\n")
            print(digest)
        elif args.command == "validate-staging":
            validate_publication_staging_bytes(
                args.file.read_bytes(),
                version=args.version,
                expected_source_commit=args.source_commit,
                expected_draft_release_id=args.draft_release_id,
                expected_sha256=args.expected_sha256,
            )
            print(f"Validated {args.file}.")
        elif args.command == "validate-bound-release":
            staging = validate_publication_staging_bytes(
                args.staging.read_bytes(),
                version=args.version,
                expected_source_commit=args.source_commit,
                expected_draft_release_id=args.draft_release_id,
                expected_sha256=args.staging_sha256,
            )
            release = validate_bound_release(
                _load(args.file),
                directory=args.directory,
                version=args.version,
                embedded_flow_version=args.embedded_flow_version,
                publication_staging=staging,
                phase=args.phase,
            )
            print(f"Validated {args.phase} release {release['id']}.")
        elif args.command == "tag-binding":
            value = build_tag_binding(
                _load(args.admission_reference),
                _load(args.artifact_inventory),
                version=args.version,
                verified_artifact_inventory_sha256=(args.verified_artifact_inventory_sha256),
            )
            if args.output.exists():
                raise ValueError("tag binding output already exists")
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_bytes(tag_binding_bytes(value))
            print(args.output)
        elif args.command == "validate-tag-binding":
            _regular_file(args.file, "tag binding")
            validate_tag_binding_bytes(args.file.read_bytes())
            print(f"Validated {args.file}.")
        elif args.command == "validate-tag-object":
            binding = validate_tag_binding_bytes(args.binding.read_bytes())
            tag_object_sha = validate_tag_object(
                _load(args.file),
                expected_tag=args.tag,
                expected_commit=args.source_commit,
                expected_binding=binding,
            )
            print(tag_object_sha)
        elif args.command == "validate-tag-ref":
            validate_tag_ref(
                _load(args.file),
                expected_tag=args.tag,
                expected_tag_object_sha=args.tag_object_sha,
            )
            print(f"Validated {args.file}.")
        elif args.command == "validate-platform-verification":
            validate_platform_verification(_load(args.file), version=args.version)
            print(f"Validated {args.file}.")
        else:
            value = build_platform_verification(
                args.directory,
                version=args.version,
                source_commit=args.source_commit,
                platform=args.platform,
                architecture=args.architecture,
                workflow_commit=args.workflow_commit,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
                embedded_flow_version=args.embedded_flow_version,
                verification=_load(args.verification),
            )
            write_platform_verification(args.output, value, version=args.version)
            print(args.output)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
