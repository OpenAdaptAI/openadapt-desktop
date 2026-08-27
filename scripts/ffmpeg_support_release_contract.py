#!/usr/bin/env python3
"""Build and validate the separate managed-FFmpeg Support release contract."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from scripts.production_release_contract import (
    immutable_releases_digest,
    validate_immutable_releases_response,
)

REPOSITORY = "OpenAdaptAI/openadapt-desktop"
REPOSITORY_ID = "1171291730"
SUPPORT_ARTIFACT = "managed-ffmpeg"
LIFECYCLE = "Support"
FFMPEG_VERSION = "8.1.2"
RUNTIME_REVISION = "r2"
TAG = f"ffmpeg-runtime-v{FFMPEG_VERSION}-{RUNTIME_REVISION}"
SOURCE_URL = f"https://ffmpeg.org/releases/ffmpeg-{FFMPEG_VERSION}.tar.xz"
SOURCE_SIGNATURE_URL = f"{SOURCE_URL}.asc"
SOURCE_SHA256 = "464beb5e7bf0c311e68b45ae2f04e9cc2af88851abb4082231742a74d97b524c"
SOURCE_SIGNATURE_SHA256 = "0a0963fccd70597838073f3e31b20f4a4d8cc2b5e577472c9a5a1f22624246f8"
SIGNING_KEY_SHA256 = "397b3becedcd5a98769967ff1ff8501ddc89f8368b8f766e4701377d7dbaabe5"
SIGNING_KEY_FINGERPRINT = "FCF986EA15E6E293A5644F10B4322F04D67658D8"
RELEASE_APP_ID = "4730708"
RELEASE_APP_INSTALLATION_ID = "156835568"
RELEASE_APP_BOT_USER_ID = "321543906"
RELEASE_APP_LOGIN = "openadapt-release[bot]"
TARGETS = (
    "aarch64-apple-darwin",
    "x86_64-apple-darwin",
    "x86_64-pc-windows-msvc",
    "x86_64-unknown-linux-gnu",
)
INVENTORY_SCHEMA = "openadapt.support-release-artifact-inventory/v1"
STAGING_SCHEMA = "openadapt.support-release-staging/v1"
TAG_BINDING_SCHEMA = "openadapt.support-release-tag-binding/v1"
INVENTORY_DOMAIN = b"OpenAdapt support release artifact inventory v1\0"
STAGING_DOMAIN = b"OpenAdapt support release staging v1\0"
TAG_BINDING_DOMAIN = b"OpenAdapt support release tag binding v1\0"
TAG_REF_STATE_DOMAIN = b"OpenAdapt support release tag ref state v1\0"
TAG_RULESETS_DOMAIN = b"OpenAdapt support release tag rulesets v1\0"
COMMIT = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
DECIMAL_ID = re.compile(r"^[1-9][0-9]*$")
TIMESTAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
FIXED_ZIP_TIME = (2026, 6, 17, 2, 47, 34)


@dataclass(frozen=True)
class ArtifactSpec:
    name: str
    kind: str
    media_type: str


def artifact_specs() -> tuple[ArtifactSpec, ...]:
    values = [
        ArtifactSpec(
            f"ffmpeg-{FFMPEG_VERSION}.tar.xz",
            "upstream-source",
            "application/x-xz",
        ),
        ArtifactSpec(
            f"ffmpeg-{FFMPEG_VERSION}.tar.xz.asc",
            "upstream-source-signature",
            "application/pgp-signature",
        ),
        ArtifactSpec("ffmpeg-devel.asc", "upstream-signing-key", "application/pgp-keys"),
        ArtifactSpec("SHA256SUMS", "release-checksums", "text/plain"),
    ]
    for target in TARGETS:
        build_id = f"ffmpeg-{FFMPEG_VERSION}-{RUNTIME_REVISION}-{target}"
        values.extend(
            (
                ArtifactSpec(
                    f"openadapt-{build_id}.zip",
                    f"runtime-{target}",
                    "application/zip",
                ),
                ArtifactSpec(
                    f"{build_id}.manifest-entry.json",
                    f"runtime-manifest-{target}",
                    "application/vnd.openadapt.ffmpeg-runtime-manifest-entry+json;version=1",
                ),
            )
        )
    return tuple(sorted(values, key=lambda item: (item.kind, item.name)))


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _file_digest(path: Path) -> str:
    return _sha256(path.read_bytes())


def _closed(value: Any, fields: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        actual = sorted(value) if isinstance(value, dict) else type(value).__name__
        raise ValueError(f"{label} must contain exactly {sorted(fields)}; got {actual}")
    return value


def _regular(path: Path, label: str) -> Path:
    if not path.is_file() or path.is_symlink() or path.stat().st_size <= 0:
        raise ValueError(f"{label} must be a non-empty regular file")
    return path


def _validate_asset_directory(directory: Path, *, inventory: Any) -> None:
    inventory_digest(inventory)
    if not directory.is_dir() or directory.is_symlink():
        raise ValueError("managed FFmpeg downloaded assets must be a directory")
    expected = {item["name"]: item for item in inventory["artifacts"]}
    actual = {path.name for path in directory.iterdir()}
    if actual != set(expected):
        raise ValueError(
            "managed FFmpeg downloaded assets differ: "
            f"missing={sorted(set(expected) - actual)}, "
            f"unexpected={sorted(actual - set(expected))}"
        )
    for name, artifact in expected.items():
        path = _regular(directory / name, f"downloaded Support asset {name}")
        if (
            path.stat().st_size != artifact["size_bytes"]
            or _file_digest(path) != artifact["sha256"]
        ):
            raise ValueError(f"downloaded Support asset bytes differ: {name}")


def _timestamp(value: Any) -> str:
    if not isinstance(value, str) or TIMESTAMP.fullmatch(value) is None:
        raise ValueError("Support release observed_at must be an exact UTC timestamp")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise ValueError("Support release observed_at is not a calendar timestamp") from exc
    return value


def _expected_archive_members(target: str) -> set[str]:
    executable = ".exe" if target == "x86_64-pc-windows-msvc" else ""
    members = {
        f"bin/ffmpeg{executable}",
        f"bin/ffprobe{executable}",
        "LICENSES/FFmpeg-LGPL-2.1-or-later.txt",
        "LICENSES/FFmpeg-LICENSE.md",
        "PROVENANCE/BUILD.json",
        "PROVENANCE/SOURCE.json",
        "PROVENANCE/configure-args.txt",
        "PROVENANCE/ffmpeg-buildconf.txt",
        "PROVENANCE/ffmpeg-encoders.txt",
        "PROVENANCE/ffmpeg-muxers.txt",
        "PROVENANCE/ffmpeg-version.txt",
        "PROVENANCE/ffprobe-buildconf.txt",
        "PROVENANCE/ffprobe-version.txt",
        "PROVENANCE/native-dependencies.txt",
        "SHA256SUMS",
    }
    if target.endswith("apple-darwin"):
        members.add("PROVENANCE/hardware-probe.txt")
    if target == "x86_64-pc-windows-msvc":
        members.add("LICENSES/zlib.txt")
    return members


def _read_zip(archive_path: Path, *, target: str) -> tuple[list[str], dict[str, bytes]]:
    _regular(archive_path, f"runtime archive {target}")
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        names = [item.filename for item in infos]
        expected_members = _expected_archive_members(target)
        expected_order = sorted(expected_members - {"SHA256SUMS"}) + ["SHA256SUMS"]
        if names != expected_order or archive.comment != b"":
            raise ValueError(f"runtime archive {target} has an unexpected member set")
        for info in infos:
            path = PurePosixPath(info.filename)
            mode = info.external_attr >> 16
            permissions = (
                0o755
                if info.filename
                in {
                    "bin/ffmpeg",
                    "bin/ffprobe",
                    "bin/ffmpeg.exe",
                    "bin/ffprobe.exe",
                }
                else 0o644
            )
            expected_mode = stat.S_IFREG | permissions
            if (
                info.is_dir()
                or path.is_absolute()
                or ".." in path.parts
                or info.date_time != FIXED_ZIP_TIME
                or mode != expected_mode
                or info.compress_type != zipfile.ZIP_DEFLATED
                or info.extra != b""
                or info.comment != b""
            ):
                raise ValueError(
                    f"runtime archive member is unsafe or noncanonical: {info.filename}"
                )
        return names, {name: archive.read(name) for name in names}


def _validate_provenance(members: dict[str, bytes], *, target: str, source_commit: str) -> None:
    try:
        source = json.loads(members["PROVENANCE/SOURCE.json"])
        build = json.loads(members["PROVENANCE/BUILD.json"])
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("runtime provenance is not UTF-8 JSON") from exc
    if source != {
        "source_url": SOURCE_URL,
        "source_sha256": SOURCE_SHA256,
        "signature_url": SOURCE_SIGNATURE_URL,
        "signing_key_fingerprint": SIGNING_KEY_FINGERPRINT,
    }:
        raise ValueError("runtime source provenance differs")
    build = _closed(
        build,
        {
            "target",
            "runtime_revision",
            "repository",
            "commit",
            "run_id",
            "workflow_ref",
            "compiler",
            "optional_hardware_encoder",
            "software_fallback_encoder",
            "zlib_provenance",
            "license",
        },
        "runtime build provenance",
    )
    expected_hardware = "h264_videotoolbox" if target.endswith("apple-darwin") else ""
    if (
        build["target"] != target
        or build["runtime_revision"] != RUNTIME_REVISION
        or build["repository"] != REPOSITORY
        or build["commit"] != source_commit
        or not isinstance(build["run_id"], str)
        or DECIMAL_ID.fullmatch(build["run_id"]) is None
        or build["workflow_ref"]
        != f"{REPOSITORY}/.github/workflows/ffmpeg-runtime.yml@refs/heads/main"
        or not isinstance(build["compiler"], str)
        or not build["compiler"]
        or build["optional_hardware_encoder"] != expected_hardware
        or build["software_fallback_encoder"] != "mpeg4"
        or not isinstance(build["zlib_provenance"], str)
        or not build["zlib_provenance"]
        or build["license"] != "LGPL-2.1-or-later"
    ):
        raise ValueError("runtime build provenance differs")


def validate_manifest_entry(
    manifest_path: Path,
    archive_path: Path,
    *,
    target: str,
    source_commit: str,
) -> dict[str, Any]:
    if target not in TARGETS or COMMIT.fullmatch(source_commit) is None:
        raise ValueError("runtime manifest target or source commit is invalid")
    try:
        manifest = json.loads(_regular(manifest_path, "runtime manifest").read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("runtime manifest is not UTF-8 JSON") from exc
    manifest = _closed(
        manifest,
        {
            "target",
            "build_id",
            "url",
            "archive_sha256",
            "archive_max_bytes",
            "files",
            "probe",
            "source",
            "license",
        },
        "runtime manifest",
    )
    build_id = f"ffmpeg-{FFMPEG_VERSION}-{RUNTIME_REVISION}-{target}"
    expected_archive = f"openadapt-{build_id}.zip"
    if (
        archive_path.name != expected_archive
        or manifest["target"] != target
        or manifest["build_id"] != build_id
        or manifest["url"]
        != f"https://github.com/{REPOSITORY}/releases/download/{TAG}/{expected_archive}"
        or manifest["archive_sha256"] != _file_digest(archive_path).removeprefix("sha256:")
        or manifest["archive_max_bytes"]
        != max(archive_path.stat().st_size + 5 * 1024 * 1024, archive_path.stat().st_size * 2)
    ):
        raise ValueError("runtime manifest archive binding differs")
    names, members = _read_zip(archive_path, target=target)
    expected_files = []
    for name in names:
        data = members[name]
        item: dict[str, Any] = {
            "member": name,
            "destination": name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "max_bytes": max(len(data) + 1024 * 1024, len(data) * 2),
        }
        if name in {"bin/ffmpeg", "bin/ffmpeg.exe"}:
            item["role"] = "ffmpeg"
        elif name in {"bin/ffprobe", "bin/ffprobe.exe"}:
            item["role"] = "ffprobe"
        expected_files.append(item)
    if manifest["files"] != expected_files:
        raise ValueError("runtime manifest file inventory differs from the archive")
    checksum_names = [name for name in names if name != "SHA256SUMS"]
    expected_checksums = b"".join(
        f"{hashlib.sha256(members[name]).hexdigest()}  {name}\n".encode() for name in checksum_names
    )
    if members["SHA256SUMS"] != expected_checksums:
        raise ValueError("runtime archive checksums differ")
    if manifest["probe"] != {
        "version_contains": f"ffmpeg version {FFMPEG_VERSION}",
        "ffprobe_version_contains": f"ffprobe version {FFMPEG_VERSION}",
        "required_build_flags": [
            "--disable-gpl",
            "--disable-nonfree",
            "--disable-version3",
            "--disable-network",
        ],
        "forbidden_build_flags": ["--enable-gpl", "--enable-nonfree"],
        "required_encoders": ["mpeg4", "png", "rawvideo"],
        "required_muxers": ["mp4", "image2pipe", "rawvideo"],
    }:
        raise ValueError("runtime probe contract differs")
    if manifest["source"] != {
        "url": SOURCE_URL,
        "sha256": SOURCE_SHA256,
        "signature_url": SOURCE_SIGNATURE_URL,
        "signing_key_fingerprint": SIGNING_KEY_FINGERPRINT,
        "build_workflow": ".github/workflows/ffmpeg-runtime.yml",
    }:
        raise ValueError("runtime manifest source contract differs")
    if manifest["license"] != {
        "expression": "LGPL-2.1-or-later",
        "license_destination": "LICENSES/FFmpeg-LGPL-2.1-or-later.txt",
    }:
        raise ValueError("runtime manifest license contract differs")
    _validate_provenance(members, target=target, source_commit=source_commit)
    return manifest


def build_artifact_inventory(directory: Path, *, source_commit: str) -> dict[str, Any]:
    if COMMIT.fullmatch(source_commit) is None:
        raise ValueError("Support release source commit is invalid")
    specs = artifact_specs()
    actual = {path.name for path in directory.iterdir()}
    expected = {item.name for item in specs}
    if actual != expected:
        raise ValueError(
            "managed FFmpeg Support assets differ: "
            f"missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}"
        )
    fixed_hashes = {
        f"ffmpeg-{FFMPEG_VERSION}.tar.xz": SOURCE_SHA256,
        f"ffmpeg-{FFMPEG_VERSION}.tar.xz.asc": SOURCE_SIGNATURE_SHA256,
        "ffmpeg-devel.asc": SIGNING_KEY_SHA256,
    }
    for name, expected_hash in fixed_hashes.items():
        if _file_digest(_regular(directory / name, name)) != f"sha256:{expected_hash}":
            raise ValueError(f"managed FFmpeg Support source input hash differs: {name}")
    for target in TARGETS:
        build_id = f"ffmpeg-{FFMPEG_VERSION}-{RUNTIME_REVISION}-{target}"
        validate_manifest_entry(
            directory / f"{build_id}.manifest-entry.json",
            directory / f"openadapt-{build_id}.zip",
            target=target,
            source_commit=source_commit,
        )
    checksummed = sorted(name for name in expected if name != "SHA256SUMS")
    expected_checksums = "".join(
        f"{_file_digest(directory / name).removeprefix('sha256:')}  {name}\n"
        for name in checksummed
    ).encode()
    if _regular(directory / "SHA256SUMS", "release checksums").read_bytes() != expected_checksums:
        raise ValueError("managed FFmpeg Support release checksums differ")
    artifacts = []
    for spec in specs:
        path = _regular(directory / spec.name, spec.name)
        artifacts.append(
            {
                "name": spec.name,
                "kind": spec.kind,
                "sha256": _file_digest(path),
                "size_bytes": path.stat().st_size,
                "media_type": spec.media_type,
                "publish_destinations": ["github-release"],
            }
        )
    return {
        "schema_version": INVENTORY_SCHEMA,
        "lifecycle": LIFECYCLE,
        "support_artifact": SUPPORT_ARTIFACT,
        "repository": REPOSITORY,
        "repository_id": REPOSITORY_ID,
        "source_commit": source_commit,
        "version": FFMPEG_VERSION,
        "revision": RUNTIME_REVISION,
        "tag": TAG,
        "artifacts": artifacts,
    }


def inventory_digest(value: Any) -> str:
    inventory = _closed(
        value,
        {
            "schema_version",
            "lifecycle",
            "support_artifact",
            "repository",
            "repository_id",
            "source_commit",
            "version",
            "revision",
            "tag",
            "artifacts",
        },
        "managed FFmpeg Support inventory",
    )
    if (
        inventory["schema_version"] != INVENTORY_SCHEMA
        or inventory["lifecycle"] != LIFECYCLE
        or inventory["support_artifact"] != SUPPORT_ARTIFACT
        or inventory["repository"] != REPOSITORY
        or inventory["repository_id"] != REPOSITORY_ID
        or COMMIT.fullmatch(str(inventory["source_commit"])) is None
        or inventory["version"] != FFMPEG_VERSION
        or inventory["revision"] != RUNTIME_REVISION
        or inventory["tag"] != TAG
    ):
        raise ValueError("managed FFmpeg Support inventory identity differs")
    artifacts = inventory["artifacts"]
    specs = artifact_specs()
    if not isinstance(artifacts, list) or len(artifacts) != len(specs):
        raise ValueError("managed FFmpeg Support inventory is incomplete")
    for artifact, spec in zip(artifacts, specs, strict=True):
        item = _closed(
            artifact,
            {"name", "kind", "sha256", "size_bytes", "media_type", "publish_destinations"},
            "managed FFmpeg Support artifact",
        )
        if (
            item["name"] != spec.name
            or item["kind"] != spec.kind
            or item["media_type"] != spec.media_type
            or item["publish_destinations"] != ["github-release"]
            or not isinstance(item["sha256"], str)
            or DIGEST.fullmatch(item["sha256"]) is None
            or not isinstance(item["size_bytes"], int)
            or isinstance(item["size_bytes"], bool)
            or item["size_bytes"] <= 0
        ):
            raise ValueError("managed FFmpeg Support artifact differs")
    return _sha256(INVENTORY_DOMAIN + _canonical(inventory))


def normalize_tag_rulesets(creation: Any, immutability: Any) -> list[dict[str, Any]]:
    values = []
    for role, name, raw in (
        ("creation_authority", "OpenAdapt policy: FFmpeg runtime tag creation", creation),
        ("immutability", "OpenAdapt policy: immutable FFmpeg runtime tags", immutability),
    ):
        if not isinstance(raw, dict):
            raise ValueError("managed FFmpeg Support ruleset response must be an object")
        raw_actors = raw.get("bypass_actors")
        if not isinstance(raw_actors, list) or not all(
            isinstance(item, dict) for item in raw_actors
        ):
            raise ValueError("managed FFmpeg Support ruleset bypass actors are invalid")
        try:
            actors = [
                {
                    "actor_id": str(item["actor_id"]),
                    "actor_type": item["actor_type"],
                    "bypass_mode": item["bypass_mode"],
                }
                for item in raw_actors
            ]
        except KeyError as exc:
            raise ValueError("managed FFmpeg Support ruleset bypass actor is incomplete") from exc
        value = {
            "role": role,
            "repository": REPOSITORY,
            "repository_id": REPOSITORY_ID,
            "ruleset_id": str(raw.get("id")),
            "name": raw.get("name"),
            "target": raw.get("target"),
            "enforcement": raw.get("enforcement"),
            "bypass_actors": actors,
            "conditions": raw.get("conditions"),
            "rules": raw.get("rules"),
        }
        expected_actors = (
            [{"actor_id": RELEASE_APP_ID, "actor_type": "Integration", "bypass_mode": "always"}]
            if role == "creation_authority"
            else []
        )
        expected_rules = (
            [{"type": "creation"}]
            if role == "creation_authority"
            else [
                {"type": "deletion"},
                {"type": "non_fast_forward"},
                {"type": "update", "parameters": {"update_allows_fetch_and_merge": False}},
            ]
        )
        if (
            DECIMAL_ID.fullmatch(value["ruleset_id"]) is None
            or value["name"] != name
            or value["target"] != "tag"
            or value["enforcement"] != "active"
            or value["bypass_actors"] != expected_actors
            or value["conditions"]
            != {"ref_name": {"include": ["refs/tags/ffmpeg-runtime-v*"], "exclude": []}}
            or value["rules"] != expected_rules
        ):
            raise ValueError(f"managed FFmpeg {role} ruleset differs")
        values.append(value)
    return values


def tag_rulesets_digest(value: Any) -> str:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("managed FFmpeg Support rulesets are incomplete")
    fields = {
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
    }
    expected_roles = ("creation_authority", "immutability")
    for index, (item, role) in enumerate(zip(value, expected_roles, strict=True)):
        normalized = _closed(item, fields, f"managed FFmpeg Support ruleset {index}")
        if (
            normalized["role"] != role
            or normalized["repository"] != REPOSITORY
            or normalized["repository_id"] != REPOSITORY_ID
        ):
            raise ValueError("managed FFmpeg Support ruleset identity differs")
    normalized = normalize_tag_rulesets(
        {**value[0], "id": value[0].get("ruleset_id")},
        {**value[1], "id": value[1].get("ruleset_id")},
    )
    return _sha256(TAG_RULESETS_DOMAIN + _canonical(normalized))


def tag_ref_state_digest(value: Any) -> str:
    state = _closed(value, {"ref", "exists"}, "managed FFmpeg tag ref state")
    if state != {"ref": f"refs/tags/{TAG}", "exists": False}:
        raise ValueError("managed FFmpeg prospective tag already exists or differs")
    return _sha256(TAG_REF_STATE_DOMAIN + _canonical(state))


def build_staging(
    release_api: Any,
    *,
    inventory: Any,
    asset_directory: Path,
    immutable_releases: Any,
    tag_rulesets: Any,
    tag_ref_state: Any,
    observed_at: str,
) -> dict[str, Any]:
    inventory_sha256 = inventory_digest(inventory)
    _validate_asset_directory(asset_directory, inventory=inventory)
    if not isinstance(release_api, dict):
        raise ValueError("managed FFmpeg draft release response must be an object")
    release_id = str(release_api.get("id"))
    author = release_api.get("author")
    if (
        DECIMAL_ID.fullmatch(release_id) is None
        or release_api.get("tag_name") != TAG
        or release_api.get("target_commitish") != inventory["source_commit"]
        or release_api.get("draft") is not True
        or release_api.get("prerelease") is not False
        or release_api.get("immutable") is not False
        or not isinstance(author, dict)
        or str(author.get("id")) != RELEASE_APP_BOT_USER_ID
        or author.get("login") != RELEASE_APP_LOGIN
    ):
        raise ValueError("managed FFmpeg draft release identity differs")
    expected_artifacts = {item["name"]: item for item in inventory["artifacts"]}
    staged_assets = []
    asset_ids: set[str] = set()
    asset_names: set[str] = set()
    for asset in release_api.get("assets", []):
        if not isinstance(asset, dict):
            raise ValueError("managed FFmpeg draft release asset is invalid")
        name = asset.get("name")
        artifact = expected_artifacts.get(name)
        asset_id = str(asset.get("id"))
        uploader = asset.get("uploader")
        if (
            artifact is None
            or name in asset_names
            or DECIMAL_ID.fullmatch(asset_id) is None
            or asset_id in asset_ids
            or asset.get("state") != "uploaded"
            or asset.get("digest") != artifact["sha256"]
            or asset.get("size") != artifact["size_bytes"]
            or not isinstance(uploader, dict)
            or str(uploader.get("id")) != RELEASE_APP_BOT_USER_ID
            or uploader.get("login") != RELEASE_APP_LOGIN
        ):
            raise ValueError("managed FFmpeg draft release asset differs")
        asset_ids.add(asset_id)
        asset_names.add(name)
        staged_assets.append(
            {
                "asset_id": asset_id,
                **artifact,
                "uploader_id": RELEASE_APP_BOT_USER_ID,
                "uploader_login": RELEASE_APP_LOGIN,
            }
        )
    if asset_names != set(expected_artifacts):
        raise ValueError("managed FFmpeg draft release assets are incomplete")
    staged_assets.sort(key=lambda item: (item["name"], item["asset_id"]))
    immutable = validate_immutable_releases_response(immutable_releases)
    rulesets_sha256 = tag_rulesets_digest(tag_rulesets)
    tag_state_sha256 = tag_ref_state_digest(tag_ref_state)
    staging = {
        "schema_version": STAGING_SCHEMA,
        "repository": REPOSITORY,
        "repository_id": REPOSITORY_ID,
        "source_commit": inventory["source_commit"],
        "tag": TAG,
        "draft_release_id": release_id,
        "release_app_id": RELEASE_APP_ID,
        "release_app_installation_id": RELEASE_APP_INSTALLATION_ID,
        "release_app_bot_user_id": RELEASE_APP_BOT_USER_ID,
        "release_author_login": RELEASE_APP_LOGIN,
        "inventory_sha256": inventory_sha256,
        "assets": staged_assets,
        "immutable_releases": immutable,
        "immutable_releases_sha256": immutable_releases_digest(immutable),
        "tag_rulesets": tag_rulesets,
        "tag_rulesets_sha256": rulesets_sha256,
        "tag_ref_state": tag_ref_state,
        "tag_ref_state_sha256": tag_state_sha256,
        "observed_at": _timestamp(observed_at),
    }
    staging_digest(staging, inventory=inventory)
    return staging


def staging_digest(value: Any, *, inventory: Any) -> str:
    staging = _closed(
        value,
        {
            "schema_version",
            "repository",
            "repository_id",
            "source_commit",
            "tag",
            "draft_release_id",
            "release_app_id",
            "release_app_installation_id",
            "release_app_bot_user_id",
            "release_author_login",
            "inventory_sha256",
            "assets",
            "immutable_releases",
            "immutable_releases_sha256",
            "tag_rulesets",
            "tag_rulesets_sha256",
            "tag_ref_state",
            "tag_ref_state_sha256",
            "observed_at",
        },
        "managed FFmpeg Support staging",
    )
    expected_inventory_sha256 = inventory_digest(inventory)
    if (
        staging["schema_version"] != STAGING_SCHEMA
        or staging["repository"] != REPOSITORY
        or staging["repository_id"] != REPOSITORY_ID
        or COMMIT.fullmatch(str(staging["source_commit"])) is None
        or staging["tag"] != TAG
        or DECIMAL_ID.fullmatch(str(staging["draft_release_id"])) is None
        or staging["release_app_id"] != RELEASE_APP_ID
        or staging["release_app_installation_id"] != RELEASE_APP_INSTALLATION_ID
        or staging["release_app_bot_user_id"] != RELEASE_APP_BOT_USER_ID
        or staging["release_author_login"] != RELEASE_APP_LOGIN
        or staging["source_commit"] != inventory["source_commit"]
        or staging["inventory_sha256"] != expected_inventory_sha256
        or staging["immutable_releases_sha256"]
        != immutable_releases_digest(staging["immutable_releases"])
        or staging["tag_rulesets_sha256"] != tag_rulesets_digest(staging["tag_rulesets"])
        or staging["tag_ref_state_sha256"] != tag_ref_state_digest(staging["tag_ref_state"])
    ):
        raise ValueError("managed FFmpeg Support staging differs")
    expected_artifacts = {item["name"]: item for item in inventory["artifacts"]}
    staged_assets = staging["assets"]
    if not isinstance(staged_assets, list) or len(staged_assets) != len(expected_artifacts):
        raise ValueError("managed FFmpeg Support staging assets are incomplete")
    asset_ids: set[str] = set()
    asset_names: set[str] = set()
    for staged_value in staged_assets:
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
            "managed FFmpeg Support staging asset",
        )
        name = staged["name"]
        asset_id = staged["asset_id"]
        expected = expected_artifacts.get(name)
        if (
            expected is None
            or name in asset_names
            or not isinstance(asset_id, str)
            or DECIMAL_ID.fullmatch(asset_id) is None
            or asset_id in asset_ids
            or {key: staged[key] for key in expected} != expected
            or staged["uploader_id"] != RELEASE_APP_BOT_USER_ID
            or staged["uploader_login"] != RELEASE_APP_LOGIN
        ):
            raise ValueError("managed FFmpeg Support staging asset differs")
        asset_names.add(name)
        asset_ids.add(asset_id)
    if asset_names != set(expected_artifacts) or staged_assets != sorted(
        staged_assets, key=lambda item: (item["name"], item["asset_id"])
    ):
        raise ValueError("managed FFmpeg Support staging asset order differs")
    _timestamp(staging["observed_at"])
    return _sha256(STAGING_DOMAIN + _canonical(staging))


def build_tag_binding(inventory: Any, staging: Any) -> dict[str, Any]:
    inventory_sha256 = inventory_digest(inventory)
    staging_sha256 = staging_digest(staging, inventory=inventory)
    if inventory["source_commit"] != staging["source_commit"]:
        raise ValueError("managed FFmpeg inventory and staging source differ")
    return {
        "schema_version": TAG_BINDING_SCHEMA,
        "repository": REPOSITORY,
        "repository_id": REPOSITORY_ID,
        "lifecycle": LIFECYCLE,
        "support_artifact": SUPPORT_ARTIFACT,
        "source_commit": inventory["source_commit"],
        "tag": TAG,
        "artifact_inventory": inventory,
        "artifact_inventory_sha256": inventory_sha256,
        "release_staging": staging,
        "release_staging_sha256": staging_sha256,
        "source": {
            "url": SOURCE_URL,
            "sha256": f"sha256:{SOURCE_SHA256}",
            "signature_url": SOURCE_SIGNATURE_URL,
            "signature_sha256": f"sha256:{SOURCE_SIGNATURE_SHA256}",
            "signing_key_fingerprint": SIGNING_KEY_FINGERPRINT,
            "signing_key_sha256": f"sha256:{SIGNING_KEY_SHA256}",
        },
    }


def tag_binding_bytes(value: Any) -> bytes:
    binding = _closed(
        value,
        {
            "schema_version",
            "repository",
            "repository_id",
            "lifecycle",
            "support_artifact",
            "source_commit",
            "tag",
            "artifact_inventory",
            "artifact_inventory_sha256",
            "release_staging",
            "release_staging_sha256",
            "source",
        },
        "managed FFmpeg Support tag binding",
    )
    inventory = binding["artifact_inventory"]
    staging = binding["release_staging"]
    if (
        binding["schema_version"] != TAG_BINDING_SCHEMA
        or binding["repository"] != REPOSITORY
        or binding["repository_id"] != REPOSITORY_ID
        or binding["lifecycle"] != LIFECYCLE
        or binding["support_artifact"] != SUPPORT_ARTIFACT
        or COMMIT.fullmatch(str(binding["source_commit"])) is None
        or binding["tag"] != TAG
        or binding["artifact_inventory_sha256"] != inventory_digest(inventory)
        or binding["release_staging_sha256"] != staging_digest(staging, inventory=inventory)
        or binding["source_commit"] != inventory["source_commit"]
        or binding["source_commit"] != staging["source_commit"]
        or binding["source"]
        != {
            "url": SOURCE_URL,
            "sha256": f"sha256:{SOURCE_SHA256}",
            "signature_url": SOURCE_SIGNATURE_URL,
            "signature_sha256": f"sha256:{SOURCE_SIGNATURE_SHA256}",
            "signing_key_fingerprint": SIGNING_KEY_FINGERPRINT,
            "signing_key_sha256": f"sha256:{SIGNING_KEY_SHA256}",
        }
    ):
        raise ValueError("managed FFmpeg Support tag binding differs")
    return _canonical(binding) + b"\n"


def tag_binding_digest(value: Any) -> str:
    return _sha256(TAG_BINDING_DOMAIN + tag_binding_bytes(value).removesuffix(b"\n"))


def validate_tag_binding_bytes(raw: bytes, *, inventory: Any, staging: Any) -> dict[str, Any]:
    if not raw or not raw.endswith(b"\n") or raw.endswith(b"\n\n"):
        raise ValueError("managed FFmpeg Support tag binding must end with one LF")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("managed FFmpeg Support tag binding is not UTF-8 JSON") from exc
    if raw != tag_binding_bytes(value) or value != build_tag_binding(inventory, staging):
        raise ValueError("managed FFmpeg Support tag binding is not exact or current")
    return value


def validate_tag_object(
    value: Any,
    *,
    source_commit: str,
    binding: Any,
) -> str:
    if not isinstance(value, dict) or COMMIT.fullmatch(source_commit) is None:
        raise ValueError("managed FFmpeg annotated tag response is invalid")
    tag_object_sha = value.get("sha")
    target = value.get("object")
    message = value.get("message")
    if (
        not isinstance(tag_object_sha, str)
        or COMMIT.fullmatch(tag_object_sha) is None
        or value.get("tag") != TAG
        or not isinstance(target, dict)
        or target.get("type") != "commit"
        or target.get("sha") != source_commit
        or not isinstance(message, str)
        or message.encode() != tag_binding_bytes(binding)
    ):
        raise ValueError("managed FFmpeg annotated tag differs")
    return tag_object_sha


def validate_tag_ref(value: Any, *, tag_object_sha: str) -> None:
    target = value.get("object") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or COMMIT.fullmatch(tag_object_sha) is None
        or value.get("ref") != f"refs/tags/{TAG}"
        or not isinstance(target, dict)
        or target.get("type") != "tag"
        or target.get("sha") != tag_object_sha
    ):
        raise ValueError("managed FFmpeg tag ref differs")


def validate_bound_release(
    release_api: Any,
    *,
    inventory: Any,
    staging: Any,
    asset_directory: Path,
    phase: str,
) -> dict[str, Any]:
    if phase not in {"draft", "published"} or not isinstance(release_api, dict):
        raise ValueError("managed FFmpeg release phase or response is invalid")
    staging_digest(staging, inventory=inventory)
    _validate_asset_directory(asset_directory, inventory=inventory)
    author = release_api.get("author")
    release_id = str(release_api.get("id"))
    expected_draft = phase == "draft"
    if (
        release_id != staging["draft_release_id"]
        or release_api.get("tag_name") != TAG
        or release_api.get("target_commitish") != inventory["source_commit"]
        or release_api.get("draft") is not expected_draft
        or release_api.get("prerelease") is not False
        or release_api.get("immutable") is not (not expected_draft)
        or not isinstance(author, dict)
        or str(author.get("id")) != RELEASE_APP_BOT_USER_ID
        or author.get("login") != RELEASE_APP_LOGIN
    ):
        raise ValueError("managed FFmpeg live release identity differs")
    expected_assets = {item["name"]: item for item in staging["assets"]}
    remote_assets = release_api.get("assets")
    if not isinstance(remote_assets, list) or len(remote_assets) != len(expected_assets):
        raise ValueError("managed FFmpeg live release assets are incomplete")
    observed_names: set[str] = set()
    for remote in remote_assets:
        if not isinstance(remote, dict):
            raise ValueError("managed FFmpeg live release asset is invalid")
        name = remote.get("name")
        expected = expected_assets.get(name)
        uploader = remote.get("uploader")
        if (
            expected is None
            or name in observed_names
            or str(remote.get("id")) != expected["asset_id"]
            or remote.get("state") != "uploaded"
            or remote.get("digest") != expected["sha256"]
            or remote.get("size") != expected["size_bytes"]
            or not isinstance(uploader, dict)
            or str(uploader.get("id")) != RELEASE_APP_BOT_USER_ID
            or uploader.get("login") != RELEASE_APP_LOGIN
        ):
            raise ValueError("managed FFmpeg live release asset differs")
        observed_names.add(name)
    if observed_names != set(expected_assets):
        raise ValueError("managed FFmpeg live release asset set differs")
    return release_api


def _write_json(path: Path, value: Any) -> None:
    if path.exists():
        raise ValueError(f"output already exists: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    inventory = commands.add_parser("inventory")
    inventory.add_argument("--directory", type=Path, required=True)
    inventory.add_argument("--source-commit", required=True)
    inventory.add_argument("--output", type=Path, required=True)
    rulesets = commands.add_parser("rulesets")
    rulesets.add_argument("--creation", type=Path, required=True)
    rulesets.add_argument("--immutability", type=Path, required=True)
    rulesets.add_argument("--output", type=Path, required=True)
    staging = commands.add_parser("staging")
    staging.add_argument("--release", type=Path, required=True)
    staging.add_argument("--inventory", type=Path, required=True)
    staging.add_argument("--asset-directory", type=Path, required=True)
    staging.add_argument("--immutable-releases", type=Path, required=True)
    staging.add_argument("--rulesets", type=Path, required=True)
    staging.add_argument("--tag-ref-state", type=Path, required=True)
    staging.add_argument("--observed-at", required=True)
    staging.add_argument("--output", type=Path, required=True)
    binding = commands.add_parser("tag-binding")
    binding.add_argument("--inventory", type=Path, required=True)
    binding.add_argument("--staging", type=Path, required=True)
    binding.add_argument("--output", type=Path, required=True)
    validate_binding = commands.add_parser("validate-tag-binding")
    validate_binding.add_argument("--file", type=Path, required=True)
    validate_binding.add_argument("--inventory", type=Path, required=True)
    validate_binding.add_argument("--staging", type=Path, required=True)
    validate_tag = commands.add_parser("validate-tag-object")
    validate_tag.add_argument("--file", type=Path, required=True)
    validate_tag.add_argument("--binding", type=Path, required=True)
    validate_tag.add_argument("--source-commit", required=True)
    validate_ref = commands.add_parser("validate-tag-ref")
    validate_ref.add_argument("--file", type=Path, required=True)
    validate_ref.add_argument("--tag-object-sha", required=True)
    validate_release = commands.add_parser("validate-bound-release")
    validate_release.add_argument("--file", type=Path, required=True)
    validate_release.add_argument("--inventory", type=Path, required=True)
    validate_release.add_argument("--staging", type=Path, required=True)
    validate_release.add_argument("--asset-directory", type=Path, required=True)
    validate_release.add_argument("--phase", choices=("draft", "published"), required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "inventory":
            value = build_artifact_inventory(args.directory, source_commit=args.source_commit)
            _write_json(args.output, value)
            print(inventory_digest(value))
        elif args.command == "rulesets":
            value = normalize_tag_rulesets(
                json.loads(_regular(args.creation, "creation ruleset").read_bytes()),
                json.loads(_regular(args.immutability, "immutability ruleset").read_bytes()),
            )
            _write_json(args.output, value)
            print(tag_rulesets_digest(value))
        elif args.command == "staging":
            inventory = json.loads(_regular(args.inventory, "Support inventory").read_bytes())
            value = build_staging(
                json.loads(_regular(args.release, "draft release").read_bytes()),
                inventory=inventory,
                asset_directory=args.asset_directory,
                immutable_releases=json.loads(
                    _regular(args.immutable_releases, "immutable releases response").read_bytes()
                ),
                tag_rulesets=json.loads(_regular(args.rulesets, "tag rulesets").read_bytes()),
                tag_ref_state=json.loads(_regular(args.tag_ref_state, "tag state").read_bytes()),
                observed_at=args.observed_at,
            )
            _write_json(args.output, value)
            print(staging_digest(value, inventory=inventory))
        elif args.command == "tag-binding":
            inventory = json.loads(_regular(args.inventory, "Support inventory").read_bytes())
            staging = json.loads(_regular(args.staging, "Support staging").read_bytes())
            value = build_tag_binding(inventory, staging)
            if args.output.exists():
                raise ValueError(f"output already exists: {args.output}")
            args.output.write_bytes(tag_binding_bytes(value))
            print(tag_binding_digest(value))
        elif args.command == "validate-tag-binding":
            inventory = json.loads(_regular(args.inventory, "Support inventory").read_bytes())
            staging = json.loads(_regular(args.staging, "Support staging").read_bytes())
            validate_tag_binding_bytes(
                _regular(args.file, "Support tag binding").read_bytes(),
                inventory=inventory,
                staging=staging,
            )
            print(f"Validated {args.file}.")
        elif args.command == "validate-tag-object":
            binding_raw = _regular(args.binding, "Support tag binding").read_bytes()
            try:
                binding = json.loads(binding_raw)
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("Support tag binding is not UTF-8 JSON") from exc
            if binding_raw != tag_binding_bytes(binding):
                raise ValueError("Support tag binding is not exact canonical JSON plus LF")
            print(
                validate_tag_object(
                    json.loads(_regular(args.file, "annotated tag response").read_bytes()),
                    source_commit=args.source_commit,
                    binding=binding,
                )
            )
        elif args.command == "validate-tag-ref":
            validate_tag_ref(
                json.loads(_regular(args.file, "tag ref response").read_bytes()),
                tag_object_sha=args.tag_object_sha,
            )
            print(f"Validated {args.file}.")
        else:
            inventory = json.loads(_regular(args.inventory, "Support inventory").read_bytes())
            staging = json.loads(_regular(args.staging, "Support staging").read_bytes())
            release = validate_bound_release(
                json.loads(_regular(args.file, "Support release response").read_bytes()),
                inventory=inventory,
                staging=staging,
                asset_directory=args.asset_directory,
                phase=args.phase,
            )
            print(f"Validated {args.phase} Support release {release['id']}.")
    except (
        OSError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
