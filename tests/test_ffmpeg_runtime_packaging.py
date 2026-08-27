"""Tests for the separately provisioned FFmpeg runtime package."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import zipfile
from pathlib import Path

import pytest
import yaml

import scripts.ffmpeg_support_release_contract as support_contract
import scripts.package_ffmpeg_runtime as runtime_package
from scripts.ffmpeg_support_release_contract import (
    RELEASE_APP_BOT_USER_ID,
    RELEASE_APP_LOGIN,
    TAG,
    TARGETS,
    build_artifact_inventory,
    build_staging,
    build_tag_binding,
    inventory_digest,
    normalize_tag_rulesets,
    staging_digest,
    tag_binding_bytes,
    tag_rulesets_digest,
    validate_bound_release,
    validate_manifest_entry,
    validate_tag_binding_bytes,
    validate_tag_object,
    validate_tag_ref,
)
from scripts.package_ffmpeg_runtime import (
    FFMPEG_VERSION,
    SIGNING_KEY_FINGERPRINT,
    SOURCE_SHA256,
    manifest_entry,
    write_checksums,
    write_deterministic_zip,
)

SOURCE_COMMIT = "a" * 40


def _bundle(root: Path) -> Path:
    bundle = root / "bundle"
    for name, value in {
        "bin/ffmpeg": b"ffmpeg",
        "bin/ffprobe": b"ffprobe",
        "LICENSES/FFmpeg-LGPL-2.1-or-later.txt": b"license",
        "PROVENANCE/SOURCE.json": b"{}",
        "PROVENANCE/BUILD.json": b"{}",
    }.items():
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    return bundle


def _support_bundle(root: Path, target: str) -> Path:
    bundle = root / target / "bundle"
    executable = ".exe" if target == "x86_64-pc-windows-msvc" else ""
    names = {
        f"bin/ffmpeg{executable}",
        f"bin/ffprobe{executable}",
        "LICENSES/FFmpeg-LGPL-2.1-or-later.txt",
        "LICENSES/FFmpeg-LICENSE.md",
        "PROVENANCE/configure-args.txt",
        "PROVENANCE/ffmpeg-buildconf.txt",
        "PROVENANCE/ffmpeg-encoders.txt",
        "PROVENANCE/ffmpeg-muxers.txt",
        "PROVENANCE/ffmpeg-version.txt",
        "PROVENANCE/ffprobe-buildconf.txt",
        "PROVENANCE/ffprobe-version.txt",
        "PROVENANCE/native-dependencies.txt",
    }
    if target.endswith("apple-darwin"):
        names.add("PROVENANCE/hardware-probe.txt")
    if target == "x86_64-pc-windows-msvc":
        names.add("LICENSES/zlib.txt")
    for name in names:
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"fixture:{name}".encode())
    source = {
        "source_url": support_contract.SOURCE_URL,
        "source_sha256": support_contract.SOURCE_SHA256,
        "signature_url": support_contract.SOURCE_SIGNATURE_URL,
        "signing_key_fingerprint": support_contract.SIGNING_KEY_FINGERPRINT,
    }
    build = {
        "target": target,
        "runtime_revision": support_contract.RUNTIME_REVISION,
        "repository": support_contract.REPOSITORY,
        "commit": SOURCE_COMMIT,
        "run_id": "123",
        "workflow_ref": (
            "OpenAdaptAI/openadapt-desktop/.github/workflows/ffmpeg-runtime.yml@refs/heads/main"
        ),
        "compiler": "fixture compiler",
        "optional_hardware_encoder": (
            "h264_videotoolbox" if target.endswith("apple-darwin") else ""
        ),
        "software_fallback_encoder": "mpeg4",
        "zlib_provenance": "fixture operating-system library",
        "license": "LGPL-2.1-or-later",
    }
    for name, value in (
        ("PROVENANCE/SOURCE.json", source),
        ("PROVENANCE/BUILD.json", build),
    ):
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    return bundle


def _rulesets() -> tuple[dict, dict]:
    common = {
        "target": "tag",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["refs/tags/ffmpeg-runtime-v*"], "exclude": []}},
    }
    return (
        {
            **common,
            "id": 10,
            "name": "OpenAdapt policy: FFmpeg runtime tag creation",
            "bypass_actors": [
                {"actor_id": 4730708, "actor_type": "Integration", "bypass_mode": "always"}
            ],
            "rules": [{"type": "creation"}],
        },
        {
            **common,
            "id": 11,
            "name": "OpenAdapt policy: immutable FFmpeg runtime tags",
            "bypass_actors": [],
            "rules": [
                {"type": "deletion"},
                {"type": "non_fast_forward"},
                {
                    "type": "update",
                    "parameters": {"update_allows_fetch_and_merge": False},
                },
            ],
        },
    )


def test_runtime_archive_is_deterministic_and_manifest_is_exact(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    archive_a = tmp_path / "runtime-a.zip"
    archive_b = tmp_path / "runtime-b.zip"
    write_checksums(bundle)
    write_deterministic_zip(bundle, archive_a)
    write_deterministic_zip(bundle, archive_b)

    assert archive_a.read_bytes() == archive_b.read_bytes()
    with zipfile.ZipFile(archive_a) as archive:
        assert set(archive.namelist()) == {
            "bin/ffmpeg",
            "bin/ffprobe",
            "LICENSES/FFmpeg-LGPL-2.1-or-later.txt",
            "PROVENANCE/SOURCE.json",
            "PROVENANCE/BUILD.json",
            "SHA256SUMS",
        }

    entry = manifest_entry(
        bundle,
        archive_a,
        "aarch64-apple-darwin",
        f"ffmpeg-{FFMPEG_VERSION}-r2-aarch64-apple-darwin",
    )
    assert entry["source"]["sha256"] == SOURCE_SHA256
    assert entry["source"]["signing_key_fingerprint"] == SIGNING_KEY_FINGERPRINT
    assert entry["license"]["expression"] == "LGPL-2.1-or-later"
    assert {file.get("role") for file in entry["files"]} >= {
        "ffmpeg",
        "ffprobe",
    }
    assert entry["probe"]["forbidden_build_flags"] == [
        "--enable-gpl",
        "--enable-nonfree",
    ]
    assert "rawvideo" in entry["probe"]["required_encoders"]
    assert "rawvideo" in entry["probe"]["required_muxers"]


def test_runtime_manifest_refuses_missing_probe_or_license(tmp_path: Path) -> None:
    bundle = _bundle(tmp_path)
    archive = tmp_path / "runtime.zip"
    write_checksums(bundle)
    write_deterministic_zip(bundle, archive)

    (bundle / "bin/ffprobe").unlink()
    with pytest.raises(ValueError, match="ffmpeg and ffprobe"):
        manifest_entry(bundle, archive, "target", "build")

    _bundle(tmp_path)
    (bundle / "LICENSES/FFmpeg-LGPL-2.1-or-later.txt").unlink()
    with pytest.raises(ValueError, match="missing"):
        manifest_entry(bundle, archive, "target", "build")


def test_support_release_binds_exact_archives_source_authority_and_draft(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = tmp_path / "release"
    release.mkdir()
    source_files = {
        f"ffmpeg-{FFMPEG_VERSION}.tar.xz": b"source fixture",
        f"ffmpeg-{FFMPEG_VERSION}.tar.xz.asc": b"signature fixture",
        "ffmpeg-devel.asc": b"signing key fixture",
    }
    source_sha = {name: hashlib.sha256(value).hexdigest() for name, value in source_files.items()}
    monkeypatch.setattr(
        support_contract,
        "SOURCE_SHA256",
        source_sha[f"ffmpeg-{FFMPEG_VERSION}.tar.xz"],
    )
    monkeypatch.setattr(runtime_package, "SOURCE_SHA256", support_contract.SOURCE_SHA256)
    monkeypatch.setattr(
        support_contract,
        "SOURCE_SIGNATURE_SHA256",
        source_sha[f"ffmpeg-{FFMPEG_VERSION}.tar.xz.asc"],
    )
    monkeypatch.setattr(
        support_contract,
        "SIGNING_KEY_SHA256",
        source_sha["ffmpeg-devel.asc"],
    )
    for name, value in source_files.items():
        (release / name).write_bytes(value)

    for target in TARGETS:
        bundle = _support_bundle(tmp_path, target)
        write_checksums(bundle)
        build_id = f"ffmpeg-{FFMPEG_VERSION}-r2-{target}"
        archive = release / f"openadapt-{build_id}.zip"
        manifest = release / f"{build_id}.manifest-entry.json"
        write_deterministic_zip(bundle, archive)
        value = manifest_entry(bundle, archive, target, build_id)
        manifest.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
        assert validate_manifest_entry(
            manifest,
            archive,
            target=target,
            source_commit=SOURCE_COMMIT,
        ) == json.loads(manifest.read_text())

    members = sorted(path.name for path in release.iterdir())
    (release / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((release / name).read_bytes()).hexdigest()}  {name}\n"
            for name in members
        )
    )
    inventory = build_artifact_inventory(release, source_commit=SOURCE_COMMIT)
    assert len(inventory["artifacts"]) == 12
    assert inventory["lifecycle"] == "Support"
    assert inventory["support_artifact"] == "managed-ffmpeg"
    assert inventory_digest(inventory).startswith("sha256:")

    creation, immutability = _rulesets()
    rulesets = normalize_tag_rulesets(creation, immutability)
    assert tag_rulesets_digest(rulesets).startswith("sha256:")
    release_api = {
        "id": 200,
        "tag_name": TAG,
        "target_commitish": SOURCE_COMMIT,
        "draft": True,
        "prerelease": False,
        "immutable": False,
        "author": {"id": int(RELEASE_APP_BOT_USER_ID), "login": RELEASE_APP_LOGIN},
        "assets": [
            {
                "id": 300 + index,
                "name": artifact["name"],
                "state": "uploaded",
                "digest": artifact["sha256"],
                "size": artifact["size_bytes"],
                "uploader": {
                    "id": int(RELEASE_APP_BOT_USER_ID),
                    "login": RELEASE_APP_LOGIN,
                },
            }
            for index, artifact in enumerate(inventory["artifacts"])
        ],
    }
    staging = build_staging(
        release_api,
        inventory=inventory,
        immutable_releases={"enabled": True, "enforced_by_owner": False},
        tag_rulesets=rulesets,
        tag_ref_state={"ref": f"refs/tags/{TAG}", "exists": False},
        observed_at="2026-08-27T12:00:00Z",
    )
    assert staging_digest(staging, inventory=inventory).startswith("sha256:")
    binding = build_tag_binding(inventory, staging)
    raw = tag_binding_bytes(binding)
    assert raw.endswith(b"\n") and not raw.endswith(b"\n\n")
    assert (
        raw
        == json.dumps(
            binding,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        + b"\n"
    )
    assert validate_tag_binding_bytes(raw, inventory=inventory, staging=staging) == binding
    tag_object_sha = "b" * 40
    assert (
        validate_tag_object(
            {
                "sha": tag_object_sha,
                "tag": TAG,
                "message": raw.decode(),
                "object": {"type": "commit", "sha": SOURCE_COMMIT},
            },
            source_commit=SOURCE_COMMIT,
            binding=binding,
        )
        == tag_object_sha
    )
    assert (
        validate_tag_ref(
            {
                "ref": f"refs/tags/{TAG}",
                "object": {"type": "tag", "sha": tag_object_sha},
            },
            tag_object_sha=tag_object_sha,
        )
        is None
    )
    assert (
        validate_bound_release(
            release_api,
            inventory=inventory,
            staging=staging,
            phase="draft",
        )
        == release_api
    )
    published = json.loads(json.dumps(release_api))
    published["draft"] = False
    published["immutable"] = True
    assert (
        validate_bound_release(
            published,
            inventory=inventory,
            staging=staging,
            phase="published",
        )
        == published
    )


def test_support_release_refuses_a_changed_archive_or_tag_authority(
    tmp_path: Path,
) -> None:
    target = "aarch64-apple-darwin"
    bundle = _support_bundle(tmp_path, target)
    write_checksums(bundle)
    build_id = f"ffmpeg-{FFMPEG_VERSION}-r2-{target}"
    archive = tmp_path / f"openadapt-{build_id}.zip"
    manifest = tmp_path / f"{build_id}.manifest-entry.json"
    write_deterministic_zip(bundle, archive)
    value = manifest_entry(bundle, archive, target, build_id)
    manifest.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    archive.write_bytes(archive.read_bytes() + b"changed")
    with pytest.raises(ValueError, match="archive binding"):
        validate_manifest_entry(
            manifest,
            archive,
            target=target,
            source_commit=SOURCE_COMMIT,
        )

    creation, immutability = _rulesets()
    creation["conditions"]["ref_name"]["include"] = ["refs/tags/v*"]
    with pytest.raises(ValueError, match="ruleset differs"):
        normalize_tag_rulesets(creation, immutability)


def test_embedded_runtime_manifest_pins_complete_reviewed_release() -> None:
    manifest = json.loads(
        (
            Path(__file__).resolve().parents[1] / "src-tauri" / "ffmpeg-runtime-manifest.json"
        ).read_text()
    )
    assert manifest["schema_version"] == 1
    assert manifest["runtime"] == "ffmpeg"
    assert manifest["runtime_version"] == "8.1.2-r1"
    artifacts = manifest["artifacts"]
    assert {artifact["target"] for artifact in artifacts} == {
        "aarch64-apple-darwin",
        "x86_64-apple-darwin",
        "x86_64-pc-windows-msvc",
        "x86_64-unknown-linux-gnu",
    }
    for artifact in artifacts:
        assert artifact["url"].startswith(
            "https://github.com/OpenAdaptAI/openadapt-desktop/releases/download/"
            "ffmpeg-runtime-v8.1.2-r1/"
        )
        assert re.fullmatch(r"[0-9a-f]{64}", artifact["archive_sha256"])
        assert artifact["source"]["sha256"] == SOURCE_SHA256
        assert artifact["source"]["signing_key_fingerprint"] == SIGNING_KEY_FINGERPRINT
        assert {file.get("role") for file in artifact["files"] if file.get("role") is not None} == {
            "ffmpeg",
            "ffprobe",
        }


def test_runtime_workflow_is_pinned_attested_and_separate_from_installers() -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = (root / ".github" / "workflows" / "ffmpeg-runtime.yml").read_text()
    script = (root / "scripts" / "build_managed_ffmpeg_runtime.sh").read_text()

    revisions = re.findall(
        r"^\s*(?:-\s+)?uses:\s+\S+@([^\s#]+)",
        workflow,
        flags=re.MULTILINE,
    )
    assert revisions
    assert all(re.fullmatch(r"[0-9a-f]{40}", revision) for revision in revisions)
    assert SOURCE_SHA256 in workflow
    assert SIGNING_KEY_FINGERPRINT in workflow
    assert (
        "SOURCE_SIGNATURE_SHA256: "
        '"0a0963fccd70597838073f3e31b20f4a4d8cc2b5e577472c9a5a1f22624246f8"' in workflow
    )
    assert (
        "SIGNING_KEY_SHA256: "
        '"397b3becedcd5a98769967ff1ff8501ddc89f8368b8f766e4701377d7dbaabe5"' in workflow
    )
    assert "actions/attest-build-provenance@" in workflow
    assert "actions/create-github-app-token@" in workflow
    assert "permission-metadata: read" in workflow
    assert "persist-credentials: false" in workflow
    assert "token: ${{ steps.release_app.outputs.token }}" in workflow
    assert "GH_TOKEN: ${{ steps.release_app.outputs.token }}" in workflow
    assert "environment: release-identity" in workflow
    assert "environment: native-release" in workflow
    assert "group: ffmpeg-runtime-${{ github.ref }}" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "authorize-runtime-dispatch" in workflow
    assert "needs.authorize-runtime-dispatch.result == 'success'" in workflow
    assert '"${GITHUB_REF_TYPE}" != "branch"' in workflow
    assert 'tags:\n      - "ffmpeg-runtime-v*"' in workflow
    assert 'git tag --annotate "${RUNTIME_TAG}" "${GITHUB_SHA}"' in workflow
    assert 'push origin "refs/tags/${RUNTIME_TAG}:refs/tags/${RUNTIME_TAG}"' in workflow
    assert "GIT_CONFIG_KEY_0=http.https://github.com/.extraheader" in workflow
    assert 'GIT_CONFIG_VALUE_0="AUTHORIZATION: basic ${app_basic}"' in workflow
    assert "APP_TOKEN: ${{ steps.release_app.outputs.token }}" in workflow
    assert "refs/heads/main:refs/heads/main" not in workflow
    assert "--verify-tag" in workflow
    assert "--target" not in workflow
    assert 'cmp "release-assets/${name}" "existing-assets/${name}"' in workflow
    assert '.author.login == "openadapt-release[bot]"' in workflow
    assert '.author.id == "BOT_kgDOEype4g"' in workflow
    assert "--json assets,author,isDraft,isPrerelease,tagName" in workflow
    assert "--clobber" not in workflow
    assert "ADMIN_TOKEN" not in workflow
    assert "--prerelease" in workflow
    assert "src-tauri/binaries" not in workflow
    for target in (
        "aarch64-apple-darwin",
        "x86_64-apple-darwin",
        "x86_64-pc-windows-msvc",
        "x86_64-unknown-linux-gnu",
    ):
        assert target in workflow

    for flag in (
        "--disable-gpl",
        "--disable-nonfree",
        "--disable-version3",
        "--enable-ffmpeg",
        "--enable-ffprobe",
        "--enable-demuxer=concat,image2,mov,rawvideo",
        "--enable-muxer=mp4,null,image2,image2pipe,rawvideo",
        "--enable-filter=scale,format,setpts,select",
    ):
        assert flag in script
    assert "h264_videotoolbox" in script
    assert "h264_mf" not in script
    assert "--enable-mediafoundation" not in script
    assert "software_fallback_encoder" in script
    assert "--enable-encoder=png,mpeg4,rawvideo" in script
    assert "-c:v rawvideo -f rawvideo" in script


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("GITHUB_EVENT_NAME", "push"),
        ("GITHUB_REPOSITORY", "OpenAdaptAI/fork"),
        ("GITHUB_REF", "refs/heads/release"),
        ("GITHUB_REF_TYPE", "tag"),
        ("PUBLISH", "yes"),
    ],
)
def test_ffmpeg_dispatch_guard_refuses_every_invalid_identity(field: str, value: str) -> None:
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/ffmpeg-runtime.yml").read_text())
    script = workflow["jobs"]["authorize-runtime-dispatch"]["steps"][0]["run"]
    env = os.environ | {
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_REPOSITORY": "OpenAdaptAI/openadapt-desktop",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_REF_TYPE": "branch",
        "PUBLISH": "false",
        field: value,
    }

    assert subprocess.run(["bash", "-c", script], env=env, check=False).returncode != 0


def test_runtime_builder_normalizes_windows_paths_and_materializes_smoke_bytes() -> None:
    root = Path(__file__).resolve().parents[1]
    script = (root / "scripts" / "build_managed_ffmpeg_runtime.sh").read_text()

    source_conversion = 'SOURCE_ARCHIVE="$(cygpath -u "${SOURCE_ARCHIVE}")"'
    output_conversion = 'OUTPUT_DIR="$(cygpath -u "${OUTPUT_DIR}")"'
    temp_conversion = 'temp_root="$(cygpath -u "${temp_root}")"'
    assert source_conversion in script
    assert output_conversion in script
    assert temp_conversion in script
    assert script.index(source_conversion) < script.index('bundle_dir="${OUTPUT_DIR}/bundle"')
    assert script.index(output_conversion) < script.index('bundle_dir="${OUTPUT_DIR}/bundle"')
    assert script.index(temp_conversion) < script.index(
        'work_root="${temp_root}/openadapt-ffmpeg-${TARGET_TRIPLE}"'
    )
    assert script.index('exe_suffix=".exe"') < script.index(
        'make -j"${jobs}" "ffmpeg${exe_suffix}" "ffprobe${exe_suffix}"'
    )

    assert 'frames = b"".join(' in script
    assert '(root / "frames.rgb").write_bytes(frames)' in script
    assert '(root / "frames.rgb").write_bytes(\n' not in script
    assert '"ffconcat version 1.0\\n"' in script
    assert '-f concat -safe 1 -i "${smoke_dir}/frames.ffconcat"' in script
    assert r"'select=eq(n\,0)'" in script
    assert r"'select=eq(n\\,0)'" not in script
